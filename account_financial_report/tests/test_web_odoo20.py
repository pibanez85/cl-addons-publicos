# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
"""Web client of the Odoo 20 port: the HTML report action (patched ReportAction)."""

from odoo.tests import HttpCase, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestReportActionWebClient(AccountTestInvoicingCommon, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.external_report_layout_id = cls.env.ref(
            "web.external_layout_standard"
        )
        cls.env.ref("base.user_admin").write(
            {
                "company_ids": [(4, cls.env.company.id)],
                "company_id": cls.env.company.id,
                # Full accounting (group_account_user) as in a database with the
                # Accounting app; the account manager group alone only implies Invoicing.
                "group_ids": [
                    (4, cls.env.ref("account.group_account_user").id),
                    (4, cls.env.ref("account.group_account_manager").id),
                ],
            }
        )
        cls.init_invoice(
            "out_invoice",
            partner=cls.partner_a,
            invoice_date="2026-03-15",
            amounts=[1000.0],
            post=True,
        )
        wizard = cls.env["trial.balance.report.wizard"].create(
            {
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
                "fy_start_date": "2026-01-01",
            }
        )
        cls.server_action = cls.env["ir.actions.server"].create(
            {
                "name": "Open trial balance (test)",
                "model_id": cls.env.ref(
                    "account_financial_report.model_trial_balance_report_wizard"
                ).id,
                "state": "code",
                "code": (
                    f"action = model.browse({wizard.id}).button_export_html()"
                ),
            }
        )

    def test_trial_balance_html_report_action(self):
        """The report opens in the web client with the Export button and the
        account lines linked to their journal items (no JS errors)."""
        code = """
            (async () => {
                const wait = async (fn, what) => {
                    const start = Date.now();
                    let res;
                    while (!(res = fn())) {
                        if (Date.now() - start > 30000) {
                            throw new Error("timeout waiting for " + what);
                        }
                        await new Promise((resolve) => setTimeout(resolve, 100));
                    }
                    return res;
                };
                const exportButton = await wait(
                    () => document.querySelector(".o_action button[title='Export']"),
                    "Export button"
                );
                const iframe = document.querySelector(".o_action iframe");
                await wait(
                    () => iframe.contentDocument &&
                        iframe.contentDocument.querySelector(
                            "a > [res-model='account.move.line'][domain]"
                        ),
                    "account link in the report"
                );
                if (!exportButton.textContent.includes("Export")) {
                    throw new Error("Export button without label");
                }
                // Export downloads the XLSX (report_xlsx handler): no error dialog.
                exportButton.click();
                await new Promise((resolve) => setTimeout(resolve, 3000));
                if (document.querySelector(".o_error_dialog")) {
                    throw new Error("error dialog after Export");
                }
                console.log("test successful");
            })();
        """
        self.browser_js(
            f"/odoo/action-{self.server_action.id}",
            code,
            ready="odoo.isReady",
            login="admin",
        )

    def test_wizards_and_configuration_views(self):
        prefix = "/odoo/action-account_financial_report"
        for action in (
            "action_general_ledger_wizard",
            "action_journal_ledger_wizard",
            "action_trial_balance_wizard",
            "action_open_items_wizard",
            "action_aged_partner_balance_wizard",
            "action_vat_report_wizard",
        ):
            self._open(f"{prefix}.{action}", ".modal .o_form_view")
        self._open(f"{prefix}.action_aged_partner_report_configuration", ".o_list_view")
        self._open(f"{prefix}.action_account_group_tree", ".o_list_view")

    def _open(self, url, selector):
        code = """
            (async () => {
                const start = Date.now();
                while (!document.querySelector(%(sel)r)) {
                    if (Date.now() - start > 30000) {
                        throw new Error("timeout waiting for " + %(sel)r);
                    }
                    await new Promise((resolve) => setTimeout(resolve, 100));
                }
                console.log("test successful");
            })();
        """ % {"sel": selector}
        self.browser_js(url, code, ready="odoo.isReady", login="admin")
