import base64

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestAccesos(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.interno = new_test_user(cls.env, login="cl_fe_interno", groups="base.group_user")
        cls.portal = new_test_user(cls.env, login="cl_fe_portal", groups="base.group_portal")
        cls.contador = new_test_user(cls.env, login="cl_fe_contador",
                                     groups="base.group_user,account.group_account_manager")

    def test_firma_solo_para_contadores(self):
        # El .p12, su clave y la llave privada no deben ser legibles por un usuario interno cualquiera.
        with self.assertRaises(AccessError):
            self.env["sii.firma"].with_user(self.interno).search([])
        with self.assertRaises(AccessError):
            self.env["sii.firma"].with_user(self.portal).search([])
        self.env["sii.firma"].with_user(self.contador).search([])
        for campo in ("file_content", "password", "priv_key", "cert"):
            grupos = set((self.env["sii.firma"]._fields[campo].groups or "").split(","))
            self.assertIn("account.group_account_manager", grupos, campo)
            self.assertNotIn("base.group_user", grupos, campo)

    def test_portal_sin_acceso_a_datos_internos(self):
        # Los CAF contienen la llave privada RSA del SII para el timbre (TED).
        for modelo in ("dte.caf", "account.move.referencias", "sale.order.referencias",
                       "mail.message.dte", "sii.xml.envio", "sii.cola_envio", "account.move.book",
                       "partner.activities"):
            with self.subTest(modelo=modelo), self.assertRaises(AccessError):
                self.env[modelo].with_user(self.portal).search([], limit=1)
        with self.assertRaises(AccessError):
            self.env["sii.activity.description"].with_user(self.portal).create({"name": "Prueba"})

    def test_portal_lee_catalogos(self):
        for modelo in ("sii.document_class", "sii.document_type", "sii.document_letter",
                       "sii.responsability", "sii.regional.offices", "res.country.state.provincia",
                       "sii.activity.description"):
            with self.subTest(modelo=modelo):
                self.env[modelo].with_user(self.portal).search([], limit=1)

    def _caf(self):
        return self.env["dte.caf"].create({
            "filename": "caf_accesos.xml", "company_id": self.env.company.id,
            "caf_string": "<AUTORIZACION><CAF/><RSASK>llave privada</RSASK></AUTORIZACION>",
        })

    def test_caf_llave_privada_solo_para_contadores(self):
        # caf_string/caf_file traen la llave privada RSA con la que se timbra (TED).
        caf = self._caf()
        for campo in ("caf_string", "caf_file"):
            with self.subTest(campo=campo), self.assertRaises(AccessError):
                caf.with_user(self.interno)[campo]
        self.assertIn("RSASK", caf.with_user(self.contador).caf_string)
        # El usuario interno sigue viendo el CAF (folios, vencimiento) sin la llave.
        self.assertEqual(caf.with_user(self.interno).filename, "caf_accesos.xml")

    def test_timbre_lee_el_caf_con_sudo(self):
        caf = self._caf()
        factura = self.env["account.move"].with_user(self.interno).browse()
        contenido = factura._caf_file(caf.with_user(self.interno), 1)
        self.assertTrue(contenido)
        self.assertIn(b"RSASK", base64.b64decode(contenido))

    def _firma(self, usuarios, companias=None, **extra):
        return self.env["sii.firma"].sudo().create(dict({
            "name": "firma_%s.p12" % usuarios[0].login,
            "user_ids": [(6, 0, [u.id for u in usuarios])],
            "company_ids": [(6, 0, (companias or self.env.company).ids)],
            "state": "valid",
            "expire_date": fields.Date.today() + relativedelta(years=1),
            "subject_serial_number": "12345678-5",
        }, **extra))

    def test_firma_del_contexto_requiere_autorizacion(self):
        ajena = self._firma([self.interno])
        propia = self._firma([self.contador])
        company = self.env.company
        Wizard = self.env["sii.dte.upload_xml.wizard"].with_user(self.contador)
        with self.assertRaises(UserError):
            Wizard.with_context(firma_id=ajena.id)._resolve_firma(company)
        self.assertEqual(Wizard.with_context(firma_id=propia.id)._resolve_firma(company), propia)
        cliente = self.env["res.partner"].create({"name": "Cliente firma"})
        factura = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": cliente.id,
            "invoice_line_ids": [(0, 0, {"name": "Servicio", "quantity": 1, "price_unit": 100})],
        }).with_user(self.contador)
        with self.assertRaises(UserError):
            factura.with_context(firma_id=ajena.id)._resolve_firma()
        self.assertEqual(factura.with_context(firma_id=propia.id)._resolve_firma(), propia)
        with self.assertRaises(UserError):
            self.env["sii.xml.envio"].with_user(self.contador).with_context(firma_id=ajena.id)._resolve_firma(company)
        # sudo (procesos internos) sí puede usar la firma indicada.
        self.assertEqual(Wizard.sudo().with_context(firma_id=ajena.id)._resolve_firma(company), ajena)

    def test_firma_del_contexto_de_otra_compania_o_vencida(self):
        otra = self.env["res.company"].create({"name": "Otra compañía firma"})
        de_otra = self._firma([self.contador], companias=otra)
        vencida = self._firma([self.contador], name="vencida.p12",
                              expire_date=fields.Date.today() - relativedelta(days=1))
        Wizard = self.env["sii.dte.upload_xml.wizard"].with_user(self.contador)
        for firma in (de_otra, vencida):
            with self.subTest(firma=firma.name), self.assertRaises(UserError):
                Wizard.with_context(firma_id=firma.id)._resolve_firma(self.env.company)

    def test_apicaf_exige_firma_autorizada_para_la_compania(self):
        otra = self.env["res.company"].create({"name": "Otra compañía apicaf"})
        de_otra = self._firma([self.contador], companias=otra)
        wizard = self.env["dte.caf.apicaf"].with_user(self.contador).new({
            "firma": de_otra.id, "company_id": self.env.company.id, "operacion": "obtener",
        })
        wizard.conectar_api()
        self.assertIn("no tiene firma electrónica autorizada", wizard.message or "")
