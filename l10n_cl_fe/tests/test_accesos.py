from odoo.exceptions import AccessError
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
