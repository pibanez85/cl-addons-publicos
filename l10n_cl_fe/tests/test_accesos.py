import base64
from unittest.mock import patch

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
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
        cls.facturador = new_test_user(cls.env, login="cl_fe_facturador",
                                       groups="base.group_user,account.group_account_invoice")
        cls.facturador_sin_firma = new_test_user(cls.env, login="cl_fe_facturador_sin_firma",
                                                 groups="base.group_user,account.group_account_invoice")

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

    # Cola de envío: se procesa con sudo (cron y botón) usando su firma_id, así que un usuario
    # sin sudo solo puede dejar en una cola documentos que se firmarán con una firma que él mismo
    # puede usar. El respaldo de la firma del admin queda solo para el modo sudo (cron).

    def _factura(self, nombre="Cliente cola"):
        cliente = self.env["res.partner"].create({"name": nombre})
        return self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": cliente.id,
            "invoice_line_ids": [(0, 0, {"name": "Servicio", "quantity": 1, "price_unit": 100})],
        })

    def _cola(self, firma, usuario=None, **extra):
        Cola = self.env["sii.cola_envio"]
        Cola = Cola.with_user(usuario) if usuario else Cola.sudo()
        return Cola.create(dict({
            "company_id": self.env.company.id, "firma_id": firma.id, "tipo_trabajo": "envio",
        }, **extra))

    def test_cola_no_acepta_documentos_si_la_firma_es_ajena(self):
        ajena = self._firma([self.contador])
        propia = self._firma([self.facturador])
        factura = self._factura()
        cola_ajena = self._cola(ajena)
        with self.assertRaises(ValidationError):
            cola_ajena.with_user(self.facturador).write({"document_ids": [(4, factura.id)]})
        with self.assertRaises(ValidationError):
            self._cola(ajena, usuario=self.facturador)
        # Su propia cola sí.
        cola_propia = self._cola(propia, usuario=self.facturador)
        cola_propia.write({"document_ids": [(4, factura.id)]})
        self.assertEqual(cola_propia.document_ids, factura)

    def test_cola_no_acepta_la_firma_del_admin_sin_sudo(self):
        del_admin = self._firma([self.env.ref("base.user_admin")])
        propia = self._firma([self.facturador])
        with self.assertRaises(ValidationError):
            self._cola(del_admin, usuario=self.facturador)
        cola_propia = self._cola(propia, usuario=self.facturador)
        with self.assertRaises(ValidationError):
            cola_propia.write({"firma_id": del_admin.id})
        # En modo sudo (cron) sí.
        self.assertEqual(self._cola(del_admin).firma_id, del_admin)

    def test_respaldo_de_la_firma_del_admin_solo_si_se_pide(self):
        del_admin = self._firma([self.env.ref("base.user_admin")])
        propia = self._firma([self.facturador])
        company = self.env.company
        Move = self.env["account.move"]
        self.assertEqual(Move.with_user(self.facturador)._resolve_cola_firma(company), propia)
        self.assertFalse(Move.with_user(self.facturador_sin_firma)._resolve_cola_firma(company))
        # sudo solo (el botón Procesar corre con sudo) no da la firma del admin.
        self.assertFalse(Move.with_user(self.facturador_sin_firma).sudo()._resolve_cola_firma(company))
        # El respaldo es explícito (lo pide el cron, ver _validar_firma).
        self.assertEqual(
            Move.with_user(self.facturador_sin_firma)._resolve_cola_firma(company, respaldo_admin=True),
            del_admin)
        self.assertEqual(
            Move.with_user(self.facturador)._resolve_cola_firma(company, respaldo_admin=True), propia)

    def test_encolar_con_firma_propia_y_sin_firma(self):
        del_admin = self._firma([self.env.ref("base.user_admin")])
        propia = self._firma([self.facturador])
        vals = {"company_id": self.env.company.id, "tipo_trabajo": "envio", "es_boleta": False}
        # Con firma propia, la cola queda con esa firma.
        cola = self._factura("Cliente propio").with_user(self.facturador)._enqueue_uniform(dict(vals))
        self.assertEqual(cola.firma_id, propia)
        # Un segundo documento del mismo usuario se agrega a su cola.
        otra = self._factura("Cliente propio 2").with_user(self.facturador)._enqueue_uniform(dict(vals))
        self.assertEqual(otra, cola)
        self.assertEqual(len(cola.document_ids), 2)
        # Sin firma propia, la cola queda sin firma y el proceso (sudo) usa la del admin.
        factura = self._factura("Cliente sin firma")
        cola_sin = factura.with_user(self.facturador_sin_firma)._enqueue_uniform(dict(vals))
        self.assertNotEqual(cola_sin, cola)
        self.assertFalse(cola_sin.firma_id)
        self.assertEqual(cola_sin.document_ids, factura)
        # sudo sin el contexto del cron (p. ej. el botón Procesar) no usa la firma del admin.
        self.assertFalse(cola_sin.with_user(self.facturador_sin_firma).sudo()._validar_firma())
        self.assertFalse(cola_sin.firma_id)
        # El cron sí.
        procesadas = []

        def procesar(cola):
            procesadas.append(cola.id)
            cola._validar_firma()

        Cola = type(self.env["sii.cola_envio"])
        with patch.object(Cola, "_procesar_tipo_trabajo", procesar):
            self.env["sii.cola_envio"].with_user(self.facturador_sin_firma)._cron_procesar_cola()
        self.assertIn(cola_sin.id, procesadas)
        self.assertEqual(cola_sin.firma_id, del_admin)

    def test_encolar_sin_ninguna_firma_disponible(self):
        vals = {"company_id": self.env.company.id, "tipo_trabajo": "envio", "es_boleta": False}
        with self.assertRaises(UserError):
            self._factura().with_user(self.facturador_sin_firma)._enqueue_uniform(vals)

    def _sin_procesar(self):
        """Evita el envío real al SII al probar action_procesar."""
        return patch.object(type(self.env["sii.cola_envio"]), "_procesar_tipo_trabajo", lambda cola: None)

    def test_procesar_sin_firma_propia_no_usa_la_del_admin(self):
        self._firma([self.env.ref("base.user_admin")])
        cola = self.env["sii.cola_envio"].sudo().create({
            "company_id": self.env.company.id, "tipo_trabajo": "envio",
            "document_ids": [(6, 0, self._factura().ids)],
        })
        with self._sin_procesar(), self.assertRaises(UserError):
            cola.with_user(self.facturador_sin_firma).action_procesar()
        self.assertFalse(cola.firma_id)
        # Tampoco con el contexto del cron en la llamada (p. ej. por RPC).
        procesadas = []

        def procesar(c):
            procesadas.append(c.id)
            c._validar_firma()

        Cola = type(self.env["sii.cola_envio"])
        with patch.object(Cola, "_procesar_tipo_trabajo", procesar), self.assertRaises(UserError):
            cola.with_user(self.facturador_sin_firma).with_context(cola_cron=True).action_procesar()
        self.assertFalse(procesadas)
        self.assertFalse(cola.firma_id)

    def test_procesar_asigna_la_firma_propia(self):
        self._firma([self.env.ref("base.user_admin")])
        propia = self._firma([self.facturador])
        cola = self.env["sii.cola_envio"].sudo().create({
            "company_id": self.env.company.id, "tipo_trabajo": "envio",
            "document_ids": [(6, 0, self._factura().ids)],
        })
        with self._sin_procesar():
            cola.with_user(self.facturador).action_procesar()
        self.assertEqual(cola.firma_id, propia)

    def test_procesar_requiere_escribir_la_cola(self):
        otra = self.env["res.company"].create({"name": "Otra compañía cola"})
        self._firma([self.facturador])
        de_otra = self.env["sii.cola_envio"].sudo().create({"company_id": otra.id, "tipo_trabajo": "envio"})
        propia = self.env["sii.cola_envio"].sudo().create({
            "company_id": self.env.company.id, "tipo_trabajo": "envio",
        })
        with self._sin_procesar():
            with self.assertRaises(AccessError):
                de_otra.with_user(self.facturador).action_procesar()
            with self.assertRaises(AccessError):
                propia.with_user(self.interno).action_procesar()

    def test_cola_ajena_no_se_puede_desfirmar_ni_tocar(self):
        ajena = self._firma([self.contador])
        propia = self._firma([self.facturador])
        factura = self._factura()
        cola_ajena = self._cola(ajena, document_ids=[(6, 0, factura.ids)])
        with self.assertRaises(ValidationError):
            cola_ajena.with_user(self.facturador).write({"firma_id": False})
        # Tampoco reemplazarla por la propia (la firma ajena ya firmaba esos documentos).
        with self.assertRaises(ValidationError):
            cola_ajena.with_user(self.facturador).write({"firma_id": propia.id})
        with self.assertRaises(ValidationError):
            cola_ajena.with_user(self.facturador).write({"document_ids": [(3, factura.id)]})
        self.assertEqual(cola_ajena.firma_id, ajena)
        self.assertEqual(cola_ajena.document_ids, factura)
