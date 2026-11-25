from contextlib import contextmanager
from decimal import ROUND_HALF_UP, Decimal
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from odoo.addons.l10n_cl_fe.models import dte_mixin

from .common_dte import NS, DteCase


def iva_sii(neto, tasa=19):
    """IVA como lo valida el SII: MntNeto x TasaIVA / 100, redondeado a pesos (mitad hacia arriba)."""
    return int((Decimal(neto) * tasa / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


@tagged("post_install", "-at_install")
class TestEmision(DteCase):
    """Emisión (timbraje y firma) de facturas 33/34 y notas 56/61: el XML valida contra el XSD
    del SII y sus totales son los del asiento."""

    def _publicar(self, documento):
        documento.action_post()
        self.assertEqual(documento.state, "posted")
        self.assertTrue(documento.sii_document_number)
        return documento

    def _nota(self, factura, tipo, cl_refund, motivo="Anula factura"):
        """Nota de crédito/débito sobre ``factura`` con el asistente de rectificativas (modo chileno)."""
        asistente = self.env["account.move.reversal"].with_context(
            active_model="account.move", active_ids=factura.ids,
        ).create({
            "move_ids": [(6, 0, factura.ids)],
            "journal_id": factura.journal_id.id,
            "reason": motivo,
            "refund_method": "cl_refund",
            "tipo_nota": self.clases[tipo].id,
            "cl_refund": cl_refund,
        })
        accion = asistente.reverse_moves()
        nota = self.env["account.move"].browse(accion["res_id"])
        self.assertEqual(nota.document_class_id, self.clases[tipo])
        return nota

    @contextmanager
    def espias(self):
        """Espía la firma (fe.timbrar), el encolado y la creación de envíos sin cambiar su efecto.

        Los asserts de base de datos tras un assertRaises no prueban nada (Odoo 20 revierte el
        savepoint); las llamadas registradas por los espías, sí."""
        Move = type(self.env["account.move"])
        Cola = type(self.env["sii.cola_envio"])
        Envio = type(self.env["sii.xml.envio"])
        with patch.object(dte_mixin.fe, "timbrar", wraps=dte_mixin.fe.timbrar) as timbrar, \
                patch.object(Move, "queue", autospec=True, side_effect=Move.queue) as queue, \
                patch.object(Cola, "create", autospec=True, side_effect=Cola.create) as cola, \
                patch.object(Envio, "create", autospec=True, side_effect=Envio.create) as envio:
            yield {"firma": timbrar, "encolado": queue, "cola": cola, "envio": envio}

    def _referencia(self, xml):
        ref = xml.find(".//sii:Documento/sii:Referencia", NS)
        self.assertIsNotNone(ref, "el DTE no trae Referencia")
        return {campo: ref.findtext("sii:%s" % campo, namespaces=NS)
                for campo in ("TpoDocRef", "FolioRef", "CodRef", "RazonRef")}

    def test_certificado_cargado(self):
        self.assertEqual(self.cert.state, "valid")
        self.assertEqual(self.cert.subject_serial_number, "11111111-1")
        self.assertEqual(self.env.ref("base.user_admin").get_digital_signature(self.company), self.cert)
        caf = self.caf(33, 1, 10)
        self.assertEqual((caf.state, caf.start_nm, caf.final_nm), ("in_use", 1, 10))
        self.assertEqual(caf.rut_n, "76086428-5")

    def test_factura_33_xml_valido(self):
        self.caf(33, 1, 10)
        with self.espias() as espias:
            factura = self._publicar(self.factura([(self.producto, 3, 1000), (self.producto, 1, 2990)]))
        # Control positivo de los espías que usa _assert_no_emite.
        self.assertEqual(espias["firma"].call_count, 1)
        self.assertEqual(espias["encolado"].call_count, 1)
        self.assertEqual(espias["cola"].call_count, 1)
        xml = self.assertXsd(factura)
        totales = self.totales(xml)
        self.assertEqual(totales["MntNeto"], 5990)
        self.assertEqual(totales["IVA"], 1138)
        self.assertEqual(totales["MntTotal"], 7128)
        self.assertEqual(factura.amount_total, 7128)
        self.assertEqual(int(xml.findtext(".//sii:IdDoc/sii:Folio", namespaces=NS)), factura.sii_document_number)
        self.assertEqual(xml.findtext(".//sii:Emisor/sii:RUTEmisor", namespaces=NS), "76086428-5")
        self.assertTrue(factura.sii_barcode, "falta el TED")
        # Queda en la cola de envío (el envío al SII no es parte de esta prueba).
        self.assertTrue(self.env["sii.cola_envio"].search([("document_ids", "in", factura.ids)]))

    def test_redondeo_exento_y_afecto(self):
        self.caf(33, 1, 10)
        factura = self._publicar(self.factura([(self.producto, 1, 1001), (self.producto_exento, 1, 499)]))
        totales = self.totales(self.assertXsd(factura))
        self.assertEqual(totales["MntTotal"], factura.amount_total)
        self.assertEqual(totales["MntExe"], 499)
        self.assertEqual(totales["MntNeto"] + totales["IVA"] + totales["MntExe"], totales["MntTotal"])

    def test_redondeo_con_descuento(self):
        """El descuento de la línea se redondea a pesos (SII): el XML (encabezado y detalle) y el
        asiento deben dar los mismos montos, peso por peso."""
        self.caf(33, 1, 10)
        factura = self._publicar(self.factura([
            (self.producto, 3, 1333, 7),   # 3.999 - 7% = 3.719,07
            (self.producto, 3, 1350, 7),   # 4.050 - 7% = 3.766,5 (el medio peso)
            (self.producto_exento, 1, 499),
        ]))
        xml = self.assertXsd(factura)
        totales = self.totales(xml)
        self.assertEqual(totales["MntTotal"], factura.amount_total)
        self.assertEqual(totales["MntNeto"] + totales["IVA"] + totales["MntExe"], totales["MntTotal"])
        self.assertEqual(totales["IVA"], factura.amount_tax)
        self.assertEqual(totales["MntNeto"] + totales["MntExe"], factura.amount_untaxed)
        neto = exento = 0
        lineas = factura.invoice_line_ids.sorted("sequence")
        for detalle in xml.findall(".//sii:Documento/sii:Detalle", NS):
            monto = int(detalle.findtext("sii:MontoItem", namespaces=NS))
            bruto = round(float(detalle.findtext("sii:PrcItem", namespaces=NS))
                          * float(detalle.findtext("sii:QtyItem", namespaces=NS)))
            descuento = int(detalle.findtext("sii:DescuentoMonto", default="0", namespaces=NS))
            self.assertEqual(monto, bruto - descuento, "MontoItem != PrcItem x QtyItem - DescuentoMonto")
            if detalle.findtext("sii:IndExe", namespaces=NS):
                exento += monto
            else:
                neto += monto
        self.assertEqual(neto, totales["MntNeto"])
        self.assertEqual(exento, totales["MntExe"])
        self.assertEqual(sorted(int(m.text) for m in xml.iterfind(".//sii:Detalle/sii:MontoItem", NS)),
                         sorted(int(l.price_subtotal) for l in lineas))

    def _assert_iva_sii(self, lineas):
        factura = self._publicar(self.factura(lineas))
        totales = self.totales(self.assertXsd(factura))
        self.assertEqual(totales["IVA"], iva_sii(totales["MntNeto"]),
                         "IVA %s != MntNeto %s x 19%%" % (totales["IVA"], totales["MntNeto"]))
        self.assertEqual(totales["MntTotal"], factura.amount_total)
        self.assertEqual(totales["IVA"], factura.amount_tax)
        self.assertEqual(totales["MntNeto"] + totales["IVA"] + totales.get("MntExe", 0), totales["MntTotal"])
        return factura, totales

    def test_iva_consistente_con_neto(self):
        """El SII valida IVA = MntNeto x 19% redondeado: el asiento debe dar ese IVA (no el de la base
        sin redondear) y el XML, los montos del asiento."""
        self.caf(33, 1, 10)
        # 205 - 50%: el SII redondea el descuento (102,5 -> 103), neto 102, IVA 19,38 -> 19.
        # (Antes: neto 103 con el subtotal de Odoo e IVA 19 sobre 102,5.)
        _factura, totales = self._assert_iva_sii([(self.producto, 1, 205, 50)])
        self.assertEqual((totales["MntNeto"], totales["IVA"], totales["MntTotal"]), (102, 19, 121))
        # Precio con decimales sin descuento: neto 103, IVA 19,57 -> 20 (sobre 102,5 sería 19).
        _factura, totales = self._assert_iva_sii([(self.producto, 1, 102.5)])
        self.assertEqual((totales["MntNeto"], totales["IVA"], totales["MntTotal"]), (103, 20, 123))

    def test_iva_consistente_con_neto_varias_lineas(self):
        self.caf(33, 1, 10)
        # Netos del SII 102 + 52 + 103 + 1.000 = 1.257 (IVA 238,83 -> 239) y exento 499.
        _factura, totales = self._assert_iva_sii([
            (self.producto, 1, 205, 50),
            (self.producto, 1, 105, 50),
            (self.producto, 1, 102.5),
            (self.producto, 1, 1000),
            (self.producto_exento, 1, 499),
        ])
        self.assertEqual((totales["MntNeto"], totales["IVA"], totales["MntExe"]), (1257, 239, 499))

    def test_numeracion_sin_documento_en_diario_con_documentos(self):
        """Un movimiento sin documento del SII en el diario de ventas sigue la numeración de Odoo:
        el reinicio (anual) se deduce de los movimientos sin documento, no de un DTE ("FAC 1")."""
        self.caf(33, 1, 10)

        def sin_documento(fecha):
            return self.env["account.move"].create({
                "move_type": "out_invoice", "partner_id": self.cliente.id,
                "company_id": self.company.id, "journal_id": self.diario.id,
                "invoice_date": fecha, "use_documents": False,
                "invoice_line_ids": [(0, 0, {"product_id": self.producto.id, "quantity": 1,
                                             "price_unit": 1000, "tax_ids": [(6, 0, self.iva.ids)]})],
            })

        primero = sin_documento("2026-06-10")
        primero.action_post()
        self.assertFalse(primero.sii_document_number)
        # El DTE es el movimiento más reciente del diario antes del siguiente.
        dte = self._publicar(self.factura([(self.producto, 1, 1000)], invoice_date="2026-06-11"))
        self.assertNotEqual(dte.name.split("/")[0], primero.name.split("/")[0])
        segundo = sin_documento("2026-06-12")
        self.assertEqual(segundo._get_last_sequence(), primero.name)
        segundo.action_post()
        prefijo, numero = primero.name.rsplit("/", 1)
        self.assertEqual(segundo.name, "%s/%s" % (prefijo, str(int(numero) + 1).zfill(len(numero))))
        # Y el DTE sigue su propia numeración (folio del CAF).
        otro = self._publicar(self.factura([(self.producto, 1, 1000)], invoice_date="2026-06-12"))
        self.assertEqual(otro.sii_document_number, dte.sii_document_number + 1)

    def test_factura_exenta_34(self):
        self.caf(34, 1, 10)
        factura = self._publicar(self.factura([(self.producto_exento, 2, 1500)], codigo=34))
        xml = self.assertXsd(factura)
        totales = self.totales(xml)
        self.assertNotIn("IVA", totales)
        self.assertEqual(totales["MntExe"], totales["MntTotal"])
        self.assertEqual(totales["MntTotal"], 3000)

    def test_nota_credito_61_referencia(self):
        self.caf(33, 1, 10)
        self.caf(61, 1, 10)
        factura = self._publicar(self.factura([(self.producto, 3, 1000), (self.producto, 1, 2990)]))
        nota = self._publicar(self._nota(factura, 61, "1"))
        self.assertEqual(nota.move_type, "out_refund")
        xml = self.assertXsd(nota)
        ref = self._referencia(xml)
        self.assertEqual(int(ref["TpoDocRef"]), 33)
        self.assertEqual(int(ref["FolioRef"]), factura.sii_document_number)
        self.assertEqual(int(ref["CodRef"]), 1)
        self.assertEqual(self.totales(xml)["MntTotal"], factura.amount_total)

    def test_nota_debito_56(self):
        self.caf(33, 1, 10)
        self.caf(56, 1, 10)
        factura = self._publicar(self.factura([(self.producto, 1, 1000)]))
        nota = self._publicar(self._nota(factura, 56, "3", motivo="Corrige montos"))
        self.assertEqual(nota.move_type, "out_invoice")
        xml = self.assertXsd(nota)
        ref = self._referencia(xml)
        self.assertEqual(int(ref["TpoDocRef"]), 33)
        self.assertEqual(int(ref["FolioRef"]), factura.sii_document_number)
        self.assertEqual(int(ref["CodRef"]), 3)

    def _assert_no_emite(self, factura, *palabras):
        with self.espias() as espias, self.assertRaises(UserError) as error:
            factura.action_post()
        mensaje = str(error.exception).lower()
        self.assertTrue(any(p.lower() in mensaje for p in palabras),
                        "%r no menciona %s" % (str(error.exception), " ni ".join(palabras)))
        # El error llega antes de firmar y de encolar o crear un envío.
        for nombre, espia in espias.items():
            self.assertFalse(espia.called, "se llamó a %s antes del error" % nombre)
        return mensaje

    def test_sin_certificado(self):
        self.caf(33, 1, 10)
        self.cert.active = False
        self._assert_no_emite(self.factura([(self.producto, 1, 1000)]), "certificado")

    def test_sin_fecha_de_resolucion(self):
        self.caf(33, 1, 10)
        self.company.dte_resolution_date = False
        self._assert_no_emite(self.factura([(self.producto, 1, 1000)]), "resolución")

    def test_sin_rut_ni_giro_de_la_compania(self):
        self.caf(33, 1, 10)
        self.company.activity_description = False
        self._assert_no_emite(self.factura([(self.producto, 1, 1000)]), "giro")
        self.company.document_number = False
        self._assert_no_emite(self.factura([(self.producto, 1, 1000)]), "rut")

    def test_sin_caf(self):
        self._assert_no_emite(self.factura([(self.producto, 1, 1000)]), "CAF", "folio")

    def test_caf_agotado(self):
        self.caf(33, 1, 1)
        self._publicar(self.factura([(self.producto, 1, 1000)]))
        self._assert_no_emite(self.factura([(self.producto, 1, 1000)]), "CAF", "folio")

    def test_caf_vencido(self):
        self.caf(33, 1, 10, vencido=True)
        self._assert_no_emite(self.factura([(self.producto, 1, 1000)]), "CAF", "folio")
