import json
import re

import pymupdf
from lxml import html as lxml_html

from odoo.tests.common import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestWeb(HttpCase):
    """Cliente web (assets JS/OWL y vistas del módulo) e informes PDF en Odoo 20."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("es_CL")
        clp = cls.env.ref("base.CLP")
        clp.active = True
        cls.company = cls.env["res.company"].create({
            "name": "Web CL", "country_id": cls.env.ref("base.cl").id, "currency_id": clp.id,
            "document_number": "76.123.456-0",
        })
        cls.env["account.chart.template"].try_loading("cl", cls.company, install_demo=False)
        # Admin con contabilidad completa (sin demo solo tiene facturación): los menús de libros,
        # balance y F29 del módulo son de contabilidad.
        cls.env.ref("base.user_admin").write({
            "company_ids": [(4, cls.company.id)], "company_id": cls.company.id,
            "group_ids": [(4, cls.env.ref("account.group_account_user").id),
                          (4, cls.env.ref("account.group_account_manager").id)],
        })
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=cls.company.ids))
        cls.cliente = cliente = cls.env["res.partner"].create({
            "name": "Cliente Web", "document_number": "11.111.111-1", "is_company": True,
        })
        iva = cls.env["account.chart.template"].with_company(cls.company).ref("IVAV_19")
        cls.factura = cls.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": cliente.id, "company_id": cls.company.id,
            "invoice_date": "2026-10-15",
            "invoice_line_ids": [(0, 0, {"name": "Servicio web", "quantity": 1, "price_unit": 1000,
                                         "tax_ids": [(6, 0, iva.ids)]})],
        })
        cls.factura.document_class_id = cls.env["sii.document_class"].search([("sii_code", "=", 33)], limit=1)
        cls.factura.sii_document_number = 4242
        # restore_mode: publica sin timbrar (no hay CAF ni firma en las pruebas).
        cls.factura.with_context(restore_mode=True).action_post()

    @classmethod
    def _factura_con(cls, folio, *impuestos, sii_code=33, **extra):
        """Documento publicado con una línea de 1.000 por cada impuesto (xml id de la plantilla)."""
        plantilla = cls.env["account.chart.template"].with_company(cls.company)
        move = cls.env["account.move"].create(dict({
            "move_type": "out_invoice", "partner_id": cls.cliente.id, "company_id": cls.company.id,
            "invoice_date": "2026-10-15",
            "invoice_line_ids": [(0, 0, {"name": "Línea %s" % xmlid, "quantity": 1, "price_unit": 1000,
                                         "tax_ids": [(6, 0, plantilla.ref(xmlid).ids)]})
                                 for xmlid in impuestos],
        }, **extra))
        move.document_class_id = cls.env["sii.document_class"].search([("sii_code", "=", sii_code)], limit=1)
        move.sii_document_number = folio
        move.with_context(restore_mode=True).action_post()
        return move

    def _browser(self, url, code="console.log('test successful')"):
        self.browser_js(url, code, ready="odoo.isReady", login="admin")

    def _esperar(self, url, selector):
        """Abre ``url`` y espera a que la vista muestre ``selector`` (sin errores de JS)."""
        code = """
            (async () => {
                const inicio = Date.now();
                while (!document.querySelector(%(sel)r)) {
                    if (Date.now() - inicio > 20000) {
                        console.error("no apareció " + %(sel)r);
                        return;
                    }
                    await new Promise((resolve) => setTimeout(resolve, 100));
                }
                console.log("test successful");
            })();
        """ % {"sel": selector}
        self._browser(url, code)

    def test_cliente_web_carga(self):
        self._esperar("/odoo/action-account.action_move_out_invoice_type", ".o_list_view .o_data_row")

    def test_formulario_de_factura_carga(self):
        # Formulario con el widget de totales (tax_totals, parche de totals_widget.js).
        self._esperar("/odoo/action-account.action_move_out_invoice_type/%s" % self.factura.id,
                      ".o_form_view .o_field_account-tax-totals-field span[name=amount_total]")

    def test_formulario_de_contacto_carga(self):
        # Formulario de contactos con RUT (document_number) del módulo.
        self._esperar("/odoo/action-contacts.action_contacts/%s" % self.cliente.id,
                      ".o_form_view .o_field_widget[name=document_number]")

    def test_vistas_del_modulo_cargan(self):
        """Abre en el cliente web cada acción de ventana del módulo: su vista por defecto y, si
        tiene, el formulario de un registro nuevo."""
        acciones = self.env["ir.model.data"].search([
            ("module", "=", "l10n_cl_fe"), ("model", "=", "ir.actions.act_window"),
        ])
        acciones = self.env["ir.actions.act_window"].browse(acciones.mapped("res_id")).filtered(
            lambda a: a.target != "new")
        self.assertTrue(acciones)
        pasos = [[a.id, "form" in (a.view_mode or "").split(",")] for a in acciones]
        code = """
            (async () => {
                const { App } = odoo.loader.modules.get("@odoo/owl");
                const env = [...App.apps][0].env;
                const esperar = () => new Promise((resolve) => setTimeout(resolve, 200));
                const fallas = [];
                const abrir = async (id, opciones, que) => {
                    try {
                        await env.services.action.doAction(id, { clearBreadcrumbs: true, ...opciones });
                        await esperar();
                    } catch (error) {
                        const detalle = (error.data && error.data.message) || error.message || error;
                        fallas.push(`acción ${id} (${que}): ${detalle}`);
                    }
                };
                for (const [id, conFormulario] of %s) {
                    await abrir(id, {}, "vista por defecto");
                    if (conFormulario) {
                        await abrir(id, { viewType: "form" }, "formulario nuevo");
                    }
                }
                if (fallas.length) {
                    console.error("Fallan " + fallas.length + " vistas:\\n" + fallas.join("\\n"));
                } else {
                    console.log("test successful");
                }
            })();
        """ % json.dumps(pasos)
        self._browser("/odoo", code)

    def test_pdf_factura(self):
        pdf, tipo = self.env["ir.actions.report"].with_context(force_report_rendering=True)._render_qweb_pdf(
            "account.account_invoices", self.factura.ids)
        self.assertEqual(tipo, "pdf")
        self.assertTrue(pdf.startswith(b"%PDF"))
        with pymupdf.open(stream=pdf, filetype="pdf") as documento:
            texto = "".join(pagina.get_text() for pagina in documento)
        self.assertIn("4242", texto)
        self.assertIn("1.190", texto)  # total con IVA

    @staticmethod
    def _monto(texto):
        texto = texto.replace("\xa0", " ")
        numero = re.sub(r"[^\d,]", "", texto).replace(",", ".")
        return (-1 if "-" in texto else 1) * float(numero)

    def _filas_de_totales(self, move):
        """Filas (etiqueta, monto) del cuadro de totales del informe, hasta "Total" inclusive."""
        contenido, _tipo = self.env["ir.actions.report"]._render_qweb_html("account.account_invoices", move.ids)
        documento = lxml_html.fromstring(contenido)
        etiqueta = lambda fila: " ".join(fila.xpath("./td[1]")[0].text_content().split())
        totales = [fila for fila in documento.iter("tr") if fila.xpath("./td") and etiqueta(fila) == "Total"]
        self.assertEqual(len(totales), 1, contenido)
        filas = []
        for fila in totales[0].getparent().iterchildren("tr"):
            filas.append((etiqueta(fila), self._monto(fila.xpath("./td")[-1].text_content())))
            if fila is totales[0]:
                break
        return filas

    def _assert_cuadra(self, move):
        filas = self._filas_de_totales(move)
        *detalle, (_total, total) = filas
        self.assertEqual(total, move.amount_total, filas)
        self.assertAlmostEqual(sum(monto for _etiqueta, monto in detalle), total, places=2,
                               msg="las filas impresas no suman el total: %s" % filas)
        return dict(detalle)

    def test_totales_impresos_con_retencion_cuadran(self):
        """Impuestos con repartición de retención ('R'): IVA carnes en una factura y en un ticket,
        y retención total de IVA en una factura de compra (46). Odoo 20 ya incluye la retención en
        los grupos de tax_totals, así que no se imprime una fila "Retención" aparte y las filas
        impresas suman el total impreso. (Con un impuesto 'A' el documento no se puede crear:
        ver PORT_NOTES.)"""
        venta = self._factura_con(5001, "IVAV_17", "IVAV_19")
        ticket = self._factura_con(5002, "IVAV_17", ticket=True)
        compra = self._factura_con(5003, "retencion_iva", sii_code=46, move_type="in_invoice")
        for move in (venta, ticket, compra):
            with self.subTest(move=move.sii_document_number, ticket=move.ticket):
                self.assertTrue(move.amount_tax_retencion)
                filas = self._assert_cuadra(move)
                self.assertNotIn("Retención", filas)
        pdf, _tipo = self.env["ir.actions.report"].with_context(force_report_rendering=True)._render_qweb_pdf(
            "account.account_invoices", compra.ids)
        with pymupdf.open(stream=pdf, filetype="pdf") as documento:
            texto = "".join(pagina.get_text() for pagina in documento)
        self.assertIn("5003", texto)
        self.assertNotIn("Retención", texto)
