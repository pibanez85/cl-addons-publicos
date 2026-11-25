import logging

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# l10n_cl_fe reemplaza a estos módulos: no deben instalarse junto a él.
DOMINIO_INCOMPATIBLES = ["|", ("name", "=", "l10n_cl"), ("name", "=like", "l10n_latam%")]

# Privacidad: módulos de Odoo que se auto-instalan y envían datos de contactos a servicios
# externos sin que nadie lo pida; el despliegue debe impedir que se instalen.
# partner_autocomplete (auto_install con iap_mail) manda a la IAP de Odoo el nombre y el VAT de
# los contactos al escribirlos y enriquece la compañía al iniciar sesión.
MODULOS_EXTERNOS_BLOQUEADOS = ("partner_autocomplete",)


def es_incompatible(nombre):
    return nombre == "l10n_cl" or nombre.startswith("l10n_latam")


class IRModule(models.Model):
    _inherit = "ir.module.module"

    @api.model
    def _cl_fe_desactivar_auto_install(self):
        """Odoo 20 auto-instala l10n_cl (auto_install + countries=['cl']) al crear una compañía
        chilena (res.company.install_l10n_modules) o al instalar account con una compañía chilena
        (button_install). Se apaga auto_install de los módulos incompatibles.

        Es solo una ayuda: `odoo -u/-i` corre update_list antes de cargar este módulo y vuelve a
        leer auto_install de los manifiestos. Lo que impide la instalación es el filtro en
        res.company._compute_uninstalled_l10n_module_ids y el 'excludes' del manifiesto."""
        modulos = self.sudo().search(DOMINIO_INCOMPATIBLES + [("auto_install", "=", True)])
        if modulos:
            modulos.write({"auto_install": False})

    @api.model
    def _cl_fe_avisar_modulos_externos(self):
        """Avisa si quedó instalado un módulo de MODULOS_EXTERNOS_BLOQUEADOS.

        Este módulo no puede impedirlo: Odoo los marca para instalar en el mismo button_install que
        l10n_cl_fe (auto_install con iap_mail), antes de cargar el código de l10n_cl_fe, y un
        'excludes' en el manifiesto hace fallar la instalación de l10n_cl_fe ("are incompatible").
        Desinstalarlo en el post_init recarga el registro y Odoo ya no corre las pruebas
        post_install de esa instalación. El bloqueo va en el despliegue (ver PORT_NOTES)."""
        instalados = self.sudo().search([
            ("name", "in", MODULOS_EXTERNOS_BLOQUEADOS), ("state", "=", "installed")])
        for modulo in instalados:
            _logger.warning(
                "%s está instalado y envía datos de contactos a servicios externos (IAP de Odoo): "
                "desinstálelo o impida su instalación en el despliegue (ver PORT_NOTES de l10n_cl_fe).",
                modulo.name)

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
