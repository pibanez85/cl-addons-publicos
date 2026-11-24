# -*- coding: utf-8 -*-
from . import controllers, models, wizard, report
import logging


_logger = logging.getLogger(__name__)
incompatible_modules = ['l10n_cl', 'l10n_latam_base', 'l10n_latam_invoice_document', 'l10n_latam_account_sequence']

def _check_pre_init(env):
    modules = env["ir.module.module"].sudo().search([
        ('name', 'in', incompatible_modules),
        ('state', '!=', 'uninstalled')
    ])
    if modules:
        _logger.warning("Existen módulos incompatibles")
        env.cr.execute("""
            UPDATE ir_model_data set module = 'l10n_cl_fe'
            WHERE name IN ('UF', 'UTM', 'OTR') AND module = 'l10n_cl'
            """)
    for module in env["ir.module.module"].sudo().search([
        ('name', 'in', incompatible_modules),
        ('state', 'in', ['uninstalled', 'to install', 'uninstallable'])
    ]):
        module.module_uninstall()
        module.unlink()

def _set_default_configs(env):
    ICPSudo = env["ir.config_parameter"].sudo()
    ICPSudo.set_int("account.auto_send_dte", 1)
    ICPSudo.set_bool("account.auto_send_email", True)
    ICPSudo.set_int("account.auto_send_persistencia", 24)

    # El XML de los DTE y los libros se arman con lang='es_CL' (dte_mixin exige el idioma activo)
    # y Odoo 20 rechaza un idioma inactivo en el contexto.
    env["res.lang"]._activate_lang("es_CL")

    for company in env["res.company"].sudo().search([]):
        env["account.chart.template"]._cl_asignar_cuentas_redondeo(company)

    # Odoo 20: que l10n_cl/l10n_latam* no se auto-instalen (ver ir_module_module.py)
    env["ir.module.module"]._cl_fe_desactivar_auto_install()

    #Eliminar incompatibilidad con l10n_cl
    modules = env["ir.module.module"].sudo().search([
        ('name', 'in', incompatible_modules),
        ('state', '!=', 'uninstalled')
    ])

    for module in modules:
        if module.name== 'l10n_cl':
            rut_type = env.ref("l10n_cl.it_RUT")
            env.cr.execute("""
                UPDATE res_partner
                SET document_number = vat,
                    vat = CONCAT('CL', REPLACE(REPLACE(vat, '.', ''), '-', ''))
                WHERE l10n_latam_identification_type_id = %s
            """, (rut_type.id,))

        """ @TODO otras asimilaciones y desinstalar """
