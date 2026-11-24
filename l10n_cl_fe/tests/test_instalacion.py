from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

CODIGOS_SII = [33, 34, 39, 41, 46, 52, 56, 61, 110, 111, 112]


@tagged("post_install", "-at_install")
class TestInstalacion(TransactionCase):

    def _estado_modulos_incompatibles(self):
        Modulo = self.env["ir.module.module"]
        modulos = Modulo.search(["|", ("name", "=", "l10n_cl"), ("name", "=like", "l10n_latam%")])
        return {m.name: m.state for m in modulos}

    def test_no_instala_l10n_cl(self):
        instalados = {n: e for n, e in self._estado_modulos_incompatibles().items() if e == "installed"}
        self.assertEqual(instalados, {})
        # El pre_init_hook borra los registros de l10n_cl/l10n_latam*, pero "Actualizar lista de
        # aplicaciones" (update_list) los vuelve a crear desde sus manifiestos.
        Modulo = self.env["ir.module.module"]
        Modulo.update_list()
        l10n_cl = Modulo.search([("name", "=", "l10n_cl")])
        self.assertTrue(l10n_cl)
        # Un `odoo -u`/`-i` corre update_list sin el código de l10n_cl_fe cargado y deja
        # auto_install=True otra vez: el filtro debe valer igual.
        l10n_cl.auto_install = True
        # Odoo 20 auto-instala los módulos con auto_install y 'countries' del país de una compañía
        # nueva (res.company.install_l10n_modules); l10n_cl no debe quedar como candidato.
        compania = self.env["res.company"].create({
            "name": "Compañía CL sin l10n_cl",
            "country_id": self.env.ref("base.cl").id,
        })
        candidatos = [n for n in compania.uninstalled_l10n_module_ids.mapped("name")
                      if n == "l10n_cl" or n.startswith("l10n_latam")]
        self.assertEqual(candidatos, [])
        # Tope duro: el manifiesto declara 'excludes', así que button_install se niega.
        excluidos = set(Modulo.search([("name", "=", "l10n_cl_fe")]).exclusion_ids.mapped("name"))
        self.assertLessEqual({"l10n_cl", "l10n_latam_invoice_document", "l10n_latam_check"}, excluidos)
        with self.assertRaisesRegex(UserError, "incompatible"):
            l10n_cl.button_install()

    def test_tipos_documento(self):
        clases = self.env["sii.document_class"].search([("sii_code", "in", CODIGOS_SII)])
        self.assertEqual(sorted(set(clases.mapped("sii_code"))), CODIGOS_SII)

    def test_plan_de_cuentas(self):
        compania = self.env["res.company"].create({
            "name": "Compañía CL de prueba",
            "country_id": self.env.ref("base.cl").id,
        })
        self.env["account.chart.template"].try_loading("cl", compania, install_demo=False)
        self.assertEqual(compania.chart_template, "cl")
        self.assertTrue(self.env["account.account"].search_count([("company_ids", "in", compania.id)]))
        iva_venta = self.env["account.tax"].search([
            ("company_id", "=", compania.id),
            ("type_tax_use", "=", "sale"),
            ("amount_type", "=", "percent"),
            ("amount", "=", 19),
            ("name", "ilike", "IVA"),
        ])
        self.assertTrue(iva_venta)
        # Cargar la plantilla no debe instalar (ni dejar por instalar) l10n_cl ni l10n_latam*.
        pendientes = {n: e for n, e in self._estado_modulos_incompatibles().items()
                      if e in ("installed", "to install", "to upgrade")}
        self.assertEqual(pendientes, {})
