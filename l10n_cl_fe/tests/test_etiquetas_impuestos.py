from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestEtiquetasImpuestos(TransactionCase):
    """Odoo 18+ usa etiquetas de impuesto sin signo: el signo lo da el saldo del apunte y el '-' de
    la fórmula del informe. Venta: IVA al haber (saldo negativo) y fórmula '-IVA Debito Fiscal'.
    Compra: IVA al debe (saldo positivo) y fórmula 'IVA Pagado Compras Recuperables'."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("es_CL")
        clp = cls.env.ref("base.CLP")
        clp.active = True
        cls.company = cls.env["res.company"].create({
            "name": "Etiquetas CL", "country_id": cls.env.ref("base.cl").id, "currency_id": clp.id,
        })
        cls.env["account.chart.template"].try_loading("cl", cls.company, install_demo=False)
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=cls.company.ids))
        cls.cliente = cls.env["res.partner"].create({"name": "Cliente etiquetas"})
        cls.dc33 = cls.env["sii.document_class"].search([("sii_code", "=", 33)], limit=1)

    def _impuesto(self, xmlid):
        return self.env["account.chart.template"].with_company(self.company).ref(xmlid)

    def _valor_informe(self, move, expresion):
        """Lo que suma la línea del informe de impuestos (motor tax_tags) para este documento."""
        expresion = self.env.ref("l10n_cl_fe.%s" % expresion)
        nombre = expresion.formula.lstrip("-")
        lineas = move.line_ids.filtered(lambda l: nombre in l.tax_tag_ids.mapped("name"))
        signo = -1 if expresion.formula.startswith("-") else 1
        return signo * sum(lineas.mapped("balance"))

    def _publicar(self, move_type, impuesto):
        move = self.env["account.move"].create({
            "move_type": move_type, "partner_id": self.cliente.id, "company_id": self.company.id,
            "invoice_date": "2026-10-15",
            "invoice_line_ids": [(0, 0, {"name": "Servicio", "quantity": 1, "price_unit": 1000,
                                         "tax_ids": [(6, 0, impuesto.ids)]})],
        })
        if move_type == "out_invoice":
            move.document_class_id = self.dc33
            move.sii_document_number = 1
        # restore_mode: publica sin timbrar (no hay CAF ni firma en las pruebas).
        move.with_context(restore_mode=True).action_post()
        self.assertEqual(move.state, "posted")
        return move

    def test_factura_33_iva_en_debito_fiscal(self):
        iva = self._impuesto("IVAV_19")
        factura = self._publicar("out_invoice", iva)
        linea_iva = factura.line_ids.filtered(lambda l: l.tax_line_id == iva)
        self.assertEqual(linea_iva.balance, -190)
        self.assertIn("IVA Debito Fiscal", linea_iva.tax_tag_ids.mapped("name"))
        self.assertEqual(self._valor_informe(factura, "tax_report_iva_debito_fiscal_tag"), 190)
        self.assertEqual(self._valor_informe(factura, "tax_report_ventas_netas_gravadas_c_iva_tag"), 1000)

    def test_compra_iva_en_credito_fiscal(self):
        iva = self._impuesto("IVAC_19")
        compra = self._publicar("in_invoice", iva)
        linea_iva = compra.line_ids.filtered(lambda l: l.tax_line_id == iva)
        self.assertEqual(linea_iva.balance, 190)
        self.assertIn("IVA Pagado Compras Recuperables", linea_iva.tax_tag_ids.mapped("name"))
        self.assertEqual(self._valor_informe(compra, "tax_report_compras_iva_recup_tag"), 190)
        self.assertEqual(self._valor_informe(compra, "tax_report_compras_netas_gr_iva_recup_tag"), 1000)

    def test_nota_de_credito_resta_del_debito_fiscal(self):
        iva = self._impuesto("IVAV_19")
        nc = self.env["account.move"].create({
            "move_type": "out_refund", "partner_id": self.cliente.id, "company_id": self.company.id,
            "invoice_date": "2026-10-15",
            "invoice_line_ids": [(0, 0, {"name": "Servicio", "quantity": 1, "price_unit": 1000,
                                         "tax_ids": [(6, 0, iva.ids)]})],
        })
        nc.with_context(restore_mode=True).action_post()
        self.assertEqual(self._valor_informe(nc, "tax_report_iva_debito_fiscal_tag"), -190)
