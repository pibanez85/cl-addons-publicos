"""Libro de compras y ventas (``account.move.book``): suma de los documentos del mes.

Cifras escritas a mano (no calculadas con el código bajo prueba), IVA 19%, redondeo a pesos:

Ventas del mes
- Factura 33 A: 3 x 1.000 + 1 x 2.990 afectos = neto 5.990, IVA 1.138 (5.990 x 19% = 1.138,1),
  más 499 exento, total 7.627.
- Factura 33 B: 2 x 5.000 afecto = neto 10.000, IVA 1.900, más 1.500 exento, total 13.400.
- Nota de crédito 61 que anula la factura A: neto 5.990, IVA 1.138, exento 499, total 7.627.
Libro de ventas = 33 A + 33 B - 61 = neto 10.000, IVA 1.900, exento 1.500, total 13.400.
(Si la nota sumara en vez de restar: neto 21.980, IVA 4.176, total 28.654.)

Compras del mes
- Factura 33 de proveedor: 4 x 2.500 afectos = neto 10.000, IVA 1.900, total 11.900.
"""
from lxml import etree

from odoo import fields
from odoo.tests import Form, tagged

from .common_dte import DteCase
from .test_emision import TestEmision

SII = {"sii": "http://www.sii.cl/SiiDte"}


@tagged("post_install", "-at_install")
class TestLibro(DteCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.periodo = fields.Date.context_today(cls.env["res.company"]).strftime("%Y-%m")
        cls.diario_compras = cls.env["account.journal"].search([
            ("company_id", "=", cls.company.id), ("type", "=", "purchase"),
        ], limit=1)
        cls.diario_compras.write({
            "journal_activities_ids": [(6, 0, cls.diario.journal_activities_ids.ids)],
            "use_documents": True,
            "document_class_ids": [(6, 0, [c.id for c in cls.clases.values()])],
        })
        cls.iva_compra = cls.env["account.chart.template"].with_company(cls.company).ref("IVAC_19")
        cls.proveedor = cls.env["res.partner"].create({
            "name": "Proveedor Ltda.", "is_company": True, "document_number": "96.806.980-2",
            "country_id": cls.env.ref("base.cl").id,
        })

    def _venta(self, lineas):
        factura = self.factura(lineas)
        factura.action_post()
        return factura

    def _nota_61(self, factura):
        return TestEmision._nota(self, factura, 61, "1")

    def _compra(self, folio):
        producto = self.producto
        compra = self.env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": self.proveedor.id,
            "company_id": self.company.id,
            "journal_id": self.diario_compras.id,
            "invoice_date": fields.Date.context_today(self.env["res.company"]),
            "use_documents": False,  # como una factura recibida: el DTE es del proveedor, no se timbra
            "document_class_id": self.clases[33].id,
            "sii_document_number": folio,
            "invoice_line_ids": [(0, 0, {
                "product_id": producto.id, "name": producto.name, "quantity": 4, "price_unit": 2500,
                "tax_ids": [(6, 0, self.iva_compra.ids)],
            })],
        })
        compra.action_post()
        return compra

    def _libro(self, operacion):
        with Form(self.env["account.move.book"]) as f:
            f.tipo_operacion = operacion
            f.periodo_tributario = self.periodo
        return f.record

    def test_libro_mes(self):
        self.caf(33, 1, 10)
        self.caf(61, 1, 10)
        a = self._venta([(self.producto, 3, 1000), (self.producto, 1, 2990), (self.producto_exento, 1, 499)])
        b = self._venta([(self.producto, 2, 5000), (self.producto_exento, 1, 1500)])
        nota = self._nota_61(a)
        nota.action_post()
        self.assertEqual((a.amount_total, b.amount_total, nota.amount_total), (7627, 13400, 7627))
        compra = self._compra(777)
        self.assertEqual(compra.amount_total, 11900)

        ventas = self._libro("VENTA")
        self.assertEqual(ventas.move_ids, a | b | nota)
        self.assertEqual(
            (ventas.total_afecto, ventas.total_iva, ventas.total_exento, ventas.total),
            (10000, 1900, 1500, 13400),
        )

        compras = self._libro("COMPRA")
        self.assertEqual(compras.move_ids, compra)
        self.assertEqual(
            (compras.total_afecto, compras.total_iva, compras.total_exento, compras.total),
            (10000, 1900, 0, 11900),
        )

    def test_libro_xml_valido(self):
        """El XML del libro de ventas (LibroCompraVenta) valida contra el XSD del SII y trae los
        totales del mes: la nota de crédito entra como documento propio y resta en el resumen."""
        self.caf(33, 1, 10)
        self.caf(61, 1, 10)
        a = self._venta([(self.producto, 3, 1000), (self.producto, 1, 2990), (self.producto_exento, 1, 499)])
        b = self._venta([(self.producto, 2, 5000), (self.producto_exento, 1, 1500)])
        nota = self._nota_61(a)
        nota.action_post()
        libro = self._libro("VENTA")
        libro._validar()
        xml = etree.fromstring(libro.sii_xml_request.xml_envio.encode("ISO-8859-1"),
                               etree.XMLParser(encoding="ISO-8859-1"))
        esquema = self.xsd("LibroCV_v10")
        self.assertTrue(esquema.validate(xml), esquema.error_log)
        resumen = {}
        for r in xml.iterfind(".//sii:ResumenPeriodo/sii:TotalesPeriodo", SII):
            resumen[int(r.findtext("sii:TpoDoc", namespaces=SII))] = {
                etree.QName(n).localname: n.text for n in r}
        self.assertEqual(set(resumen), {33, 61})
        self.assertEqual((resumen[33]["TotDoc"], resumen[33]["TotMntNeto"], resumen[33]["TotMntIVA"]),
                         ("2", "15990", "3038"))
        self.assertEqual((resumen[61]["TotDoc"], resumen[61]["TotMntNeto"], resumen[61]["TotMntIVA"]),
                         ("1", "5990", "1138"))
        folios = sorted(int(d.findtext("sii:NroDoc", namespaces=SII))
                        for d in xml.iterfind(".//sii:Detalle", SII))
        self.assertEqual(folios, sorted([a.sii_document_number, b.sii_document_number, nota.sii_document_number]))
