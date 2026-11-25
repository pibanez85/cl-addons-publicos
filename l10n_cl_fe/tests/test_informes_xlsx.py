from odoo.tests.common import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestInformesXlsx(HttpCase):
    """ir.actions.report._render_xlsx usa la firma de report_xlsx (OCA):
    (report_ref, docids, data). l10n_cl_fe se carga después de report_xlsx cuando ambos
    están instalados, así que con otra firma rompía los informes XLSX de los módulos OCA
    (account_financial_report)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.contador = new_test_user(
            cls.env, login="cl_fe_contador_xlsx",
            groups="base.group_user,account.group_account_readonly",
        )
        cls.wizard = cls.env["l10n_cl_fe.balance8col.wizard"].with_user(cls.contador).create({})

    def test_render_xlsx_firma_report_xlsx(self):
        Report = self.env["ir.actions.report"].with_user(self.contador)
        data = {"form": self.wizard._build_data()}
        for ref in (
            "l10n_cl_fe.action_report_balance_8_columnas_xlsx",  # xmlid
            "l10n_cl_fe.report_balance_8_columnas_xlsx",  # report_name
            self.env.ref("l10n_cl_fe.action_report_balance_8_columnas_xlsx"),  # registro
        ):
            contenido, tipo = Report._render_xlsx(ref, self.wizard.ids, data)
            self.assertEqual(tipo, "xlsx")
            self.assertEqual(contenido[:2], b"PK")

    def test_descarga_xlsx_controlador(self):
        self.authenticate(self.contador.login, self.contador.login)
        respuesta = self.url_open(
            "/report/xlsx/l10n_cl_fe.report_balance_8_columnas_xlsx/%d" % self.wizard.id
        )
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.content[:2], b"PK")
        self.assertIn("spreadsheetml", respuesta.headers["Content-Type"])
