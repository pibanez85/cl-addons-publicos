# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
"""Smoke test of the Odoo 20 port: every report renders (HTML, PDF and XLSX).

The upstream tests only render a few of the reports; QWeb templates and XLSX writers
are evaluated lazily, so a field or API removed in Odoo 20 would only show up when a
user prints the report.
"""

import json

from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestRenderOdoo20(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.date_from = "2026-01-01"
        cls.date_to = "2026-12-31"
        cls.receivable = cls.company_data["default_account_receivable"]
        # Without a document layout, report_action opens the layout configurator.
        cls.env.company.external_report_layout_id = cls.env.ref(
            "web.external_layout_standard"
        )
        # A group that contains the receivable account (trial balance hierarchy).
        cls.group = cls.env["account.group"].create(
            {"code_prefix_start": cls.receivable.code[:1], "name": "Smoke group"}
        )
        cls.invoice = cls.init_invoice(
            "out_invoice",
            partner=cls.partner_a,
            invoice_date="2026-03-15",
            amounts=[1000.0],
            taxes=cls.tax_sale_a,
            post=True,
        )
        cls.bill = cls.init_invoice(
            "in_invoice",
            partner=cls.partner_b,
            invoice_date="2026-03-20",
            amounts=[400.0],
            taxes=cls.tax_purchase_a,
            post=True,
        )

    def _render_all(self, wizard, xlsx_report, html_report):
        # The web client sends the report data as JSON (dates become strings).
        data = json.loads(json.dumps(wizard._prepare_report_data(), default=str))
        report_model = self.env["ir.actions.report"].with_context(
            active_model=wizard._name, active_id=wizard.id, active_ids=wizard.ids
        )
        html, html_type = report_model._render_qweb_html(html_report, wizard.ids, data)
        self.assertEqual(html_type, "html")
        self.assertTrue(html)
        pdf, _pdf_type = report_model._render_qweb_pdf(html_report, wizard.ids, data)
        self.assertTrue(pdf)
        content, content_type = report_model._render_xlsx(
            xlsx_report, wizard.ids, data
        )
        self.assertEqual(content_type, "xlsx")
        self.assertEqual(content[:2], b"PK")
        for button in ("button_export_html", "button_export_pdf", "button_export_xlsx"):
            action = getattr(wizard, button)()
            self.assertEqual(action["type"], "ir.actions.report")
        return html

    def test_general_ledger(self):
        for grouped_by in ("partners", "taxes", "none"):
            wizard = self.env["general.ledger.report.wizard"].create(
                {
                    "date_from": self.date_from,
                    "date_to": self.date_to,
                    "grouped_by": grouped_by,
                    "centralize": False,
                }
            )
            self._render_all(
                wizard,
                "account_financial_report.action_report_general_ledger_xlsx",
                "account_financial_report.action_print_report_general_ledger_html",
            )

    def test_journal_ledger(self):
        for group_option in ("journal", "none"):
            wizard = self.env["journal.ledger.report.wizard"].create(
                {
                    "date_from": self.date_from,
                    "date_to": self.date_to,
                    "group_option": group_option,
                    "with_account_name": True,
                }
            )
            self._render_all(
                wizard,
                "account_financial_report.action_report_journal_ledger_xlsx",
                "account_financial_report.action_print_journal_ledger_wizard_html",
            )

    def test_trial_balance(self):
        for vals in (
            {},
            {"show_hierarchy": True},
            {"show_hierarchy": True, "limit_hierarchy_level": True},
            {"show_partner_details": True},
            {"foreign_currency": True},
        ):
            wizard = self.env["trial.balance.report.wizard"].create(
                dict(
                    vals,
                    date_from=self.date_from,
                    date_to=self.date_to,
                    fy_start_date=self.date_from,
                )
            )
            html = self._render_all(
                wizard,
                "account_financial_report.action_report_trial_balance_xlsx",
                "account_financial_report.action_report_trial_balance_html",
            )
            if vals.get("show_hierarchy"):
                self.assertIn(b"Smoke group", html)

    def test_open_items_and_aged_partner_balance(self):
        open_items = self.env["open.items.report.wizard"].create(
            {"date_at": self.date_to, "receivable_accounts_only": True}
        )
        open_items.onchange_type_accounts_only()
        self._render_all(
            open_items,
            "account_financial_report.action_report_open_items_xlsx",
            "account_financial_report.action_print_report_open_items_html",
        )
        aged = self.env["aged.partner.balance.report.wizard"].create(
            {
                "date_at": self.date_to,
                "receivable_accounts_only": True,
                "show_move_line_details": True,
            }
        )
        aged.onchange_type_accounts_only()
        self._render_all(
            aged,
            "account_financial_report.action_report_aged_partner_balance_xlsx",
            "account_financial_report.action_print_report_aged_partner_balance_html",
        )

    def test_vat_report(self):
        for based_on in ("taxtags", "taxgroups"):
            wizard = self.env["vat.report.wizard"].create(
                {
                    "date_from": self.date_from,
                    "date_to": self.date_to,
                    "based_on": based_on,
                    "tax_detail": True,
                }
            )
            self._render_all(
                wizard,
                "account_financial_report.action_report_vat_report_xlsx",
                "account_financial_report.action_print_report_vat_report_html",
            )

    def test_account_group_hierarchy(self):
        """account.group (removed from Odoo 20) keeps the Odoo 19 behaviour."""
        child = self.env["account.group"].create(
            {
                "code_prefix_start": self.receivable.code[:2],
                "name": "Smoke child group",
            }
        )
        self.assertEqual(child.parent_id, self.group)
        self.assertEqual(self.receivable.group_id, child)
        self.assertIn(self.receivable, self.group.compute_account_ids)
        other = self.env["account.account"].create(
            {"code": "999901", "name": "Smoke other", "account_type": "income"}
        )
        self.assertFalse(other.group_id)
        # Hierarchy is rebuilt when a group in between is removed.
        child.unlink()
        self.assertEqual(self.receivable.group_id, self.group)
        self.assertTrue(
            self.env["account.move.line"].search(
                [("account_id", "=", self.receivable.id)]
            )
        )
