from odoo import api, models
from odoo.exceptions import UserError

# l10n_cl_fe reemplaza a estos módulos: no deben instalarse junto a él.
DOMINIO_INCOMPATIBLES = ["|", ("name", "=", "l10n_cl"), ("name", "=like", "l10n_latam%")]


class IRModule(models.Model):
    _inherit = "ir.module.module"

    @api.model
    def _cl_fe_desactivar_auto_install(self):
        """Odoo 20 auto-instala l10n_cl (auto_install + countries=['cl']) al crear una compañía
        chilena (res.company.install_l10n_modules) o al instalar account con una compañía chilena
        (button_install). Se apaga auto_install de los módulos incompatibles."""
        modulos = self.sudo().search(DOMINIO_INCOMPATIBLES + [("auto_install", "=", True)])
        if modulos:
            modulos.write({"auto_install": False})

    @api.model
    def update_list(self):
        # update_list vuelve a leer auto_install desde los manifiestos.
        res = super().update_list()
        self._cl_fe_desactivar_auto_install()
        return res

    def modules_to_remove(self):
        modules_to_remove = self.mapped("name")
        if "l10n_cl_fe" in modules_to_remove:
            if self.env["sii.xml.envio"].search([("state", "=", "Aceptado")], limit=1):
                raise UserError("NO puede desinstalar el módulo ya que tiene DTEs válidos emitidos")
        return super(IRModule, self).modules_to_remove()

    def button_uninstall(self):
        modules_to_remove = self.mapped("name")
        if "l10n_cl_fe" in modules_to_remove:
            if self.env["sii.xml.envio"].search([("state", "=", "Aceptado")], limit=1):
                raise UserError("NO puede desinstalar el módulo ya que tiene DTEs válidos emitidos")
        return super(IRModule, self).button_uninstall()
