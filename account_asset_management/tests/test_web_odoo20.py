# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
"""Web client of the Odoo 20 port: the module's views open without JS errors."""

from odoo.tests import HttpCase, tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestAssetWebClient(AccountTestInvoicingCommon, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
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
        cls.profile = cls.env["account.asset.profile"].create(
            {
                "account_expense_depreciation_id": cls.company_data[
                    "default_account_expense"
                ].id,
                "account_asset_id": cls.company_data["default_account_assets"].id,
                "account_depreciation_id": cls.company_data[
                    "default_account_assets"
                ].id,
                "journal_id": cls.company_data["default_journal_purchase"].id,
                "name": "Hardware - 3 Years",
                "method_time": "year",
                "method_number": 3,
                "method_period": "year",
            }
        )
        cls.asset = cls.env["account.asset"].create(
            {
                "name": "Laptop",
                "profile_id": cls.profile.id,
                "purchase_value": 3000,
                "date_start": "2026-01-01",
            }
        )
        cls.asset.compute_depreciation_board()
        cls.asset.validate()

    def _open(self, url, selector, then=""):
        code = """
            (async () => {
                const wait = async (sel) => {
                    const start = Date.now();
                    while (!document.querySelector(sel)) {
                        if (Date.now() - start > 30000) {
                            throw new Error("timeout waiting for " + sel);
                        }
                        await new Promise((resolve) => setTimeout(resolve, 100));
                    }
                };
                await wait(%(sel)r);
                %(then)s
                console.log("test successful");
            })();
        """ % {"sel": selector, "then": then}
        self.browser_js(url, code, ready="odoo.isReady", login="admin")

    def test_views(self):
        prefix = "/odoo/action-account_asset_management"
        self._open(f"{prefix}.account_asset_action", ".o_list_view .o_data_row")
        self._open(
            f"{prefix}.account_asset_action/{self.asset.id}",
            ".o_form_view .o_field_widget[name='profile_id']",
            # Depreciation board tab: the lines of the one2many.
            then="""
                [...document.querySelectorAll(".o_notebook .nav-link")]
                    .find((tab) => tab.textContent.includes("Depreciation Board"))
                    .click();
                await wait(".o_field_one2many[name='depreciation_line_ids'] .o_data_row");
            """,
        )
        self._open(f"{prefix}.account_asset_profile_action", ".o_list_view .o_data_row")
        self._open(
            f"{prefix}.account_asset_profile_action/{self.profile.id}",
            ".o_form_view",
        )
        self._open(f"{prefix}.account_asset_group_action", ".o_list_view")
        self._open(f"{prefix}.account_asset_compute_action", ".modal .o_form_view")
        self._open(f"{prefix}.wiz_account_asset_report_action", ".modal .o_form_view")
        self._open(f"{prefix}.action_account_asset_report", ".o_pivot_view .o_pivot")
