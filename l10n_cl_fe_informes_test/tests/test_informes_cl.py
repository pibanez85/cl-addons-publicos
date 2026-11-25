# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
"""Balance de comprobación de ``account_financial_report`` con el plan de cuentas chileno.

Mes con las facturas de la prueba del libro de compras y ventas (``l10n_cl_fe``), IVA 19%,
redondeo a pesos. Cifras escritas a mano:

- Factura 33 A: 3 x 1.000 + 1 x 2.990 afectos = neto 5.990, IVA 1.138 (5.990 x 19% = 1.138,1),
  más 499 exento, total 7.627.
- Factura 33 B: 2 x 5.000 afecto = neto 10.000, IVA 1.900, más 1.500 exento, total 13.400.
- Compra 33: 4 x 2.500 afectos = neto 10.000, IVA 1.900, total 11.900.

Asientos del mes (compañía nueva, sin saldo inicial):

- Debe: clientes 7.627 + 13.400 = 21.027; gasto 10.000; IVA crédito fiscal (110701) 1.900.
  Total 32.927.
- Haber: ingresos 5.990 + 499 + 10.000 + 1.500 = 17.989; IVA débito fiscal (210701) 1.138 +
  1.900 = 3.038; proveedores 11.900. Total 32.927.
"""

from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.tests import tagged

from odoo.addons.l10n_cl_fe.tests.common_dte import DteCase


@tagged("post_install", "-at_install")
class TestInformesCl(DteCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.hoy = fields.Date.context_today(cls.env["res.company"])
        cls.diario_compras = cls.env["account.journal"].search(
            [("company_id", "=", cls.company.id), ("type", "=", "purchase")], limit=1
        )
        cls.iva_compra = (
            cls.env["account.chart.template"].with_company(cls.company).ref("IVAC_19")
        )
        cls.proveedor = cls.env["res.partner"].create(
            {
                "name": "Proveedor Ltda.",
                "is_company": True,
                "document_number": "96.806.980-2",
                "country_id": cls.env.ref("base.cl").id,
            }
        )

    def _compra(self, folio):
        compra = self.env["account.move"].create(
            {
                "move_type": "in_invoice",
                "partner_id": self.proveedor.id,
                "company_id": self.company.id,
                "journal_id": self.diario_compras.id,
                "invoice_date": self.hoy,
                # Factura recibida: el DTE es del proveedor, no se timbra.
                "use_documents": False,
                "document_class_id": self.clases[33].id,
                "sii_document_number": folio,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.producto.id,
                            "name": self.producto.name,
                            "quantity": 4,
                            "price_unit": 2500,
                            "tax_ids": [(6, 0, self.iva_compra.ids)],
                        },
                    )
                ],
            }
        )
        compra.action_post()
        return compra

    def _cuenta(self, codigo):
        return self.env["account.account"].search(
            [("code", "=", codigo), ("company_ids", "in", self.company.id)], limit=1
        )

    def _balance(self):
        inicio = self.hoy.replace(day=1)
        wizard = self.env["trial.balance.report.wizard"].create(
            {
                "company_id": self.company.id,
                "date_from": inicio,
                "date_to": inicio + relativedelta(months=1, days=-1),
                "fy_start_date": self.hoy.replace(month=1, day=1),
                "target_move": "posted",
                "hide_account_at_0": True,
            }
        )
        data = wizard._prepare_report_data()
        return self.env[
            "report.account_financial_report.trial_balance"
        ]._get_report_values(wizard, data)

    def test_informes_cl(self):
        self.caf(33, 1, 10)
        a = self.factura(
            [
                (self.producto, 3, 1000),
                (self.producto, 1, 2990),
                (self.producto_exento, 1, 499),
            ]
        )
        b = self.factura([(self.producto, 2, 5000), (self.producto_exento, 1, 1500)])
        (a | b).action_post()
        compra = self._compra(777)
        self.assertEqual(
            (a.amount_total, b.amount_total, compra.amount_total), (7627, 13400, 11900)
        )

        res = self._balance()
        cuentas = [
            linea for linea in res["trial_balance"] if linea["type"] == "account_type"
        ]
        debe = sum(linea["debit"] for linea in cuentas)
        haber = sum(linea["credit"] for linea in cuentas)
        self.assertEqual((debe, haber), (32927, 32927))
        self.assertEqual(sum(linea["ending_balance"] for linea in cuentas), 0)

        por_cuenta = {linea["id"]: linea for linea in cuentas}
        iva_debito = por_cuenta[self._cuenta("210701").id]
        self.assertEqual(
            (iva_debito["debit"], iva_debito["credit"], iva_debito["ending_balance"]),
            (0, 3038, -3038),
        )
        iva_credito = por_cuenta[self._cuenta("110701").id]
        self.assertEqual(
            (iva_credito["debit"], iva_credito["credit"], iva_credito["ending_balance"]),
            (1900, 0, 1900),
        )
