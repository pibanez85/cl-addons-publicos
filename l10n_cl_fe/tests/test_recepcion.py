import base64
from types import SimpleNamespace
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged

from odoo.addons.l10n_cl_fe.models import mail_message_dte_document as recibidos

BHE = """<?xml version="1.0" encoding="UTF-8"?>
<datos><numeroBoleta>%(folio)s</numeroBoleta><fechaBoleta>20261005</fechaBoleta>
<rutEmisor>12345678</rutEmisor><dvEmisor>5</dvEmisor><domicilioEmisor>Calle 1</domicilioEmisor>
<rutReceptor>76086428</rutReceptor><dvReceptor>5</dvReceptor>
<totalHonorarios>%(bruto)s</totalHonorarios><porcentajeImpuesto>%(tasa)s</porcentajeImpuesto>
<impuestoHonorarios>%(retencion)s</impuestoHonorarios><retieneEmisor>%(retiene)s</retieneEmisor>
<prestacionServicios><item><descripcionLinea>Asesoría</descripcionLinea><valorServicio>%(bruto)s</valorServicio></item>
</prestacionServicios></datos>"""


class RecepcionBase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.partner_id.document_number = "76.086.428-5"
        cls.company.partner_id.vat = "CL760864285"


@tagged("post_install", "-at_install")
class TestRecepcionBhe(RecepcionBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Cuenta = cls.env["account.account"]
        if not Cuenta.search([("company_ids", "parent_of", cls.company.id), ("code", "=", "210704")]):
            Cuenta.create({"name": "Retención Segunda Categoría", "code": "210704", "account_type": "liability_current",
                           "company_ids": [(6, 0, cls.company.ids)]})
        gasto = Cuenta.create({"name": "Honorarios", "code": "519971", "account_type": "expense",
                               "company_ids": [(6, 0, cls.company.ids)]})
        cls.dc = cls.env["sii.document_class"].search([("sii_code", "=", 71)], limit=1)
        cls.env["account.journal"].search([("company_id", "=", cls.company.id), ("type", "=", "purchase")]).write(
            {"document_class_ids": [(3, cls.dc.id)]})
        cls.diario = cls.env["account.journal"].create({
            "name": "Honorarios", "code": "BHE71", "type": "purchase", "company_id": cls.company.id,
            "default_account_id": gasto.id, "document_class_ids": [(6, 0, cls.dc.ids)],
        })

    def _subir(self, folio, bruto, tasa, retencion, retiene="N"):
        xml = BHE % {"folio": folio, "bruto": bruto, "tasa": tasa, "retencion": retencion, "retiene": retiene}
        wizard = self.env["sii.dte.upload_xml.wizard"].create({
            "xml_file": base64.b64encode(xml.encode("UTF-8")).decode(), "filename": "bhe_%s.xml" % folio})
        return self.env["account.move"].browse(wizard.confirm(ret=True))

    def _montos(self, move):
        return move.amount_untaxed, move.amount_tax, move.amount_total

    def test_bhe_con_un_solo_impuesto_de_retencion(self):
        primera = self._subir(55, 210526, 14.5, 30526)
        segunda = self._subir(56, 137931, 13, 17931)
        self.assertEqual(self._montos(primera), (210526, -30526, 180000))
        self.assertEqual(self._montos(segunda), (137931, -17931, 120000))
        self.assertEqual(primera.invoice_line_ids.tax_ids, segunda.invoice_line_ids.tax_ids)
        self.assertEqual(len(primera.invoice_line_ids.tax_ids), 1)
        self.assertEqual((primera.move_type, primera.document_class_id, primera.sii_document_number, primera.journal_id),
                         ("in_invoice", self.dc, 55, self.diario))
        self.assertEqual(primera.line_ids.filtered("tax_line_id").account_id.code, "210704")
        self.assertEqual(primera.partner_id.company_type, "person")
        self.assertTrue(self.env["ir.attachment"].search([("res_model", "=", "account.move"), ("res_id", "=", primera.id)]))
        self.assertEqual(self._subir(55, 210526, 14.5, 30526), primera)
        (primera | segunda).action_post()
        self.assertEqual(self._montos(primera), (210526, -30526, 180000))
        self.assertEqual(self._montos(segunda), (137931, -17931, 120000))

    def test_bhe_que_retiene_el_emisor_va_sin_retencion(self):
        move = self._subir(57, 100000, 14.5, 14500, retiene="S")
        self.assertFalse(move.invoice_line_ids.tax_ids)
        self.assertEqual(self._montos(move), (100000, 0, 100000))


@tagged("post_install", "-at_install")
class TestBoletaSoloIva(RecepcionBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Odoo 20 (port): la base de pruebas sin demo crea la compañía principal en USD (redondeo 0,0001
        # según data/res.currency.csv); las boletas son en pesos chilenos, como en la base de 17.0.
        clp = cls.env.ref("base.CLP")
        clp.active = True
        cls.company.currency_id = clp
        Tax = cls.env["account.tax"]
        cls.iva = Tax.search([("sii_code", "=", 14), ("type_tax_use", "=", "sale"), ("amount", "=", 19),
                              ("price_include", "=", False), ("company_id", "=", cls.company.id)], limit=1)
        if not cls.iva:
            cls.iva = Tax.create({"name": "IVA venta prueba", "amount": 19, "type_tax_use": "sale", "sii_code": 14,
                                  "company_id": cls.company.id})
        cls.ila = Tax.create({"name": "ILA bebidas prueba", "amount": 10, "type_tax_use": "sale", "sii_code": 27,
                              "company_id": cls.company.id})
        cls.otro = Tax.create({"name": "Otro impuesto prueba", "amount": 5, "type_tax_use": "sale", "sii_code": 0,
                               "company_id": cls.company.id})
        cls.cliente = cls.env["res.partner"].create({"name": "Cliente boleta"})

    def _documento(self, codigo, impuestos):
        move = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.cliente.id,
            "invoice_line_ids": [(0, 0, {"name": "Bebida", "quantity": 1, "price_unit": 1000,
                                         "tax_ids": [(6, 0, impuestos.ids)]})],
        })
        move.document_class_id = self.env["sii.document_class"].search([("sii_code", "=", codigo)], limit=1)
        return move

    def test_boleta_consolida_todo_impuesto_en_el_iva(self):
        for impuestos, bruto in ((self.iva | self.ila, 1290), (self.iva | self.otro, 1240)):
            boleta = self._documento(39, impuestos)
            self.assertEqual(boleta.amount_total, bruto)
            boleta._consolidate_boleta_taxes_to_iva()
            neto = round(bruto / 1.19)
            self.assertEqual(boleta.line_ids.filtered("tax_line_id").tax_line_id, self.iva)
            self.assertEqual((boleta.amount_untaxed, boleta.amount_tax, boleta.amount_total), (neto, bruto - neto, bruto))

    def test_factura_mantiene_los_dos_impuestos(self):
        factura = self._documento(33, self.iva | self.ila)
        factura._consolidate_boleta_taxes_to_iva()
        self.assertEqual(factura.line_ids.filtered("tax_line_id").tax_line_id, self.iva | self.ila)
        self.assertEqual((factura.amount_untaxed, factura.amount_tax), (1000, 290))


@tagged("post_install", "-at_install")
class TestVerificacionSii(RecepcionBase):

    def _documento(self):
        return self.env["mail.message.dte.document"].create({
            "number": "123", "new_partner": "12345678-5 Proveedor Prueba SpA", "company_id": self.company.id,
            "document_class_id": self.env["sii.document_class"].search([("sii_code", "=", 33)], limit=1).id,
        })

    def _verificar(self, documento, codigo, metodo="action_verificar_sii"):
        respuesta = {"RUT12345678-5T33F123": {"respuesta": SimpleNamespace(codResp=codigo, listaEventosDoc=[])}}
        with patch.object(type(documento), "_get_datos_empresa", lambda d: {}), \
                patch.object(recibidos.fe, "consulta_reclamo_documento", lambda datos: respuesta):
            getattr(documento, metodo)()

    def test_verificar_marca_si_esta_o_no_en_el_sii_sin_cambiar_el_estado(self):
        documento = self._documento()
        for codigo, esperado in ((18, "no"), (10, "no"), (15, "si"), (16, "si")):
            self._verificar(documento, codigo)
            self.assertEqual((documento.en_sii, documento.state, documento.claim), (esperado, "draft", "N/D"))

    def test_respuesta_desconocida_no_cambia_la_marca(self):
        documento = self._documento()
        self._verificar(documento, 99)
        self.assertFalse(documento.en_sii)

    def test_cron_no_revisa_los_del_registro_de_compras_ni_los_confirmados(self):
        pendiente, confirmado, rcv = self._documento(), self._documento(), self._documento()
        confirmado.en_sii = "si"
        rcv.source = "rcv"
        self._verificar(self.env["mail.message.dte.document"], 18, metodo="_cron_verificar_sii")
        self.assertEqual((pendiente.en_sii, confirmado.en_sii, rcv.en_sii), ("no", "si", False))
