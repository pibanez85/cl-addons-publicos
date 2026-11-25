from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestDescargaBoleta(HttpCase):
    """/download/boleta es público (consulta de boletas), pero solo entrega el PDF de una factura
    a quien puede leerla o trae su access_token."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.portal = new_test_user(cls.env, login="cl_fe_portal_pdf", groups="base.group_portal")
        cls.facturador = new_test_user(cls.env, login="cl_fe_facturador_pdf",
                                       groups="base.group_user,account.group_account_invoice")
        cliente = cls.env["res.partner"].create({"name": "Cliente PDF"})
        cls.factura = cls.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": cliente.id,
            "invoice_line_ids": [(0, 0, {"name": "Servicio", "quantity": 1, "price_unit": 100})],
        })

    def _descargar(self, **params):
        params = dict({"model": "account.move", "model_id": self.factura.id}, **params)
        query = "&".join("%s=%s" % kv for kv in params.items())
        return self.url_open("/download/boleta?%s" % query, allow_redirects=False)

    def test_anonimo_sin_token_rechazado(self):
        respuesta = self._descargar()
        self.assertEqual(respuesta.status_code, 404)
        self.assertNotIn("Cliente PDF", respuesta.text)

    def test_usuario_sin_acceso_rechazado(self):
        self.authenticate(self.portal.login, self.portal.login)
        respuesta = self._descargar()
        self.assertEqual(respuesta.status_code, 404)
        self.assertNotIn("Cliente PDF", respuesta.text)
        respuesta = self._descargar(access_token="token-falso")
        self.assertEqual(respuesta.status_code, 404)

    def test_solo_account_move(self):
        self.authenticate(self.facturador.login, self.facturador.login)
        respuesta = self._descargar(model="res.partner", model_id=self.factura.partner_id.id)
        self.assertEqual(respuesta.status_code, 404)

    def test_con_acceso_o_token(self):
        self.authenticate(self.facturador.login, self.facturador.login)
        self.assertEqual(self._descargar().status_code, 200)
        self.logout()
        token = self.factura._portal_ensure_token()
        self.assertEqual(self._descargar(access_token=token).status_code, 200)

    def test_consulta_de_boleta_enlaza_el_pdf_con_token(self):
        # La página /boleta/<folio> completa falla hoy en el informe (vistas, Task 4): se prueba
        # el bloque que arma el enlace de descarga.
        html = str(self.env["ir.qweb"]._render("l10n_cl_fe.boleta_found_layout", {"docs": self.factura.sudo()}))
        self.assertTrue(self.factura.access_token)
        url = "/download/boleta?model_id=%s&amp;access_token=%s" % (self.factura.id, self.factura.access_token)
        self.assertIn(url, html)
        self.assertEqual(self.url_open(url.replace("&amp;", "&")).status_code, 200)
