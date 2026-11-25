import html
import re
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode

import pymupdf

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


@tagged("post_install", "-at_install")
class TestConsultaBoleta(HttpCase):
    """Consulta pública de boletas: exige folio, fecha, monto exacto y RUT del emisor, solo para
    boletas (39/41) y notas de crédito (61) publicadas, y no deja un access_token guardado."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.document_number = "76.123.456-0"
        cliente = cls.env["res.partner"].create({"name": "Cliente Consulta"})

        def documento(codigo, folio):
            move = cls.env["account.move"].create({
                "move_type": "out_invoice", "partner_id": cliente.id,
                "invoice_line_ids": [(0, 0, {"name": "Servicio", "quantity": 1, "price_unit": 1190})],
            })
            move.action_post()
            clase = cls.env["sii.document_class"].search([("sii_code", "=", codigo)], limit=1)
            move.write({"document_class_id": clase.id, "sii_document_number": folio})
            return move

        cls.boleta = documento(39, 555001)
        cls.factura = documento(33, 555002)

    def setUp(self):
        super().setUp()
        # En modo de pruebas Odoo entrega el HTML del informe en vez del PDF salvo con
        # force_report_rendering: se fuerza el PDF real (wkhtmltopdf) del controlador.
        Report = type(self.env["ir.actions.report"])
        original = Report._pre_render_qweb_pdf

        def pdf_real(report, *args, **kwargs):
            return original(report.with_context(force_report_rendering=True), *args, **kwargs)

        self.startPatcher(patch.object(Report, "_pre_render_qweb_pdf", pdf_real))

    def _texto_pdf(self, contenido):
        self.assertTrue(contenido.startswith(b"%PDF"), contenido[:200])
        with pymupdf.open(stream=contenido, filetype="pdf") as documento:
            return "".join(pagina.get_text() for pagina in documento)

    def _params(self, move, **extra):
        return dict({
            "date": str(move.date), "amount_total": "%d" % move.amount_total,
            "rut_emisor": "76123456-0", "sii_codigo": move.document_class_id.sii_code,
        }, **extra)

    def _pdf(self, folio, params):
        return self.url_open("/boleta/%s/pdf?%s" % (folio, urlencode(params)), allow_redirects=False)

    def test_folio_solo_no_encuentra(self):
        respuesta = self.url_open("/boleta/555001")
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("Boleta no encontrada", respuesta.text)
        self.assertEqual(self._pdf(555001, {}).status_code, 404)
        self.assertEqual(self._pdf(555001, {"sii_codigo": 39}).status_code, 404)

    def test_folio_fecha_monto_y_rut_encuentra(self):
        respuesta = self._pdf(555001, self._params(self.boleta))
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.headers["Content-Type"], "application/pdf")
        self.assertIn("555001", self._texto_pdf(respuesta.content))
        # El RUT se compara sin puntos, guion ni ceros a la izquierda.
        self.assertEqual(self._pdf(555001, self._params(self.boleta, rut_emisor="76.123.456-0")).status_code, 200)
        # Sin sii_codigo se busca una boleta afecta (39), como el formulario.
        params = self._params(self.boleta)
        del params["sii_codigo"]
        self.assertEqual(self._pdf(555001, params).status_code, 200)
        # La consulta no deja un token permanente del portal.
        self.assertFalse(self.boleta.access_token)

    def test_monto_o_fecha_distintos_no_encuentran(self):
        self.assertEqual(self._pdf(555001, self._params(self.boleta, amount_total="1")).status_code, 404)
        self.assertEqual(self._pdf(555001, self._params(self.boleta, date="2000-01-01")).status_code, 404)
        self.assertEqual(self._pdf(555001, self._params(self.boleta, amount_total="x")).status_code, 404)
        self.assertEqual(self._pdf(555001, self._params(self.boleta, amount_total="nan")).status_code, 404)

    def test_factura_33_rechazada(self):
        respuesta = self._pdf(555002, self._params(self.factura))
        self.assertEqual(respuesta.status_code, 404)
        respuesta = self.url_open("/boleta/555002?%s" % urlencode(self._params(self.factura)))
        self.assertIn("Boleta no encontrada", respuesta.text)
        # Control positivo: el mismo documento como boleta 39 sí se encuentra.
        self.factura.document_class_id = self.env["sii.document_class"].search([("sii_code", "=", 39)], limit=1)
        params = self._params(self.factura)
        self.assertEqual(params["sii_codigo"], 39)
        respuesta = self.url_open("/boleta/555002?%s" % urlencode(params))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("Boleta encontrada", respuesta.text)
        self.assertEqual(self._pdf(555002, params).status_code, 200)

    def test_rut_emisor_distinto_no_encuentra(self):
        self.assertEqual(self._pdf(555001, self._params(self.boleta, rut_emisor="77777777-7")).status_code, 404)
        self.assertEqual(self._pdf(555001, self._params(self.boleta, rut_emisor="")).status_code, 404)

    def test_pagina_encontrada_con_enlace_sin_token(self):
        params = self._params(self.boleta)
        respuesta = self.url_open("/boleta/555001?%s" % urlencode(params))
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("Boleta encontrada", respuesta.text)
        self.assertNotIn("Boleta no encontrada", respuesta.text)
        # El documento se muestra en la página (informe de la factura/boleta con sus totales).
        self.assertIn("555001", respuesta.text)
        self.assertIn("Cliente Consulta", respuesta.text)
        enlaces = re.findall(r'href="/boleta/555001/pdf\?([^"]*)"', respuesta.text)
        self.assertEqual(len(enlaces), 1, respuesta.text)
        consulta = parse_qs(html.unescape(enlaces[0]))
        self.assertEqual(consulta, {clave: [str(valor)] for clave, valor in params.items()})
        self.assertNotIn("access_token", respuesta.text)
        self.assertFalse(self.boleta.access_token)
