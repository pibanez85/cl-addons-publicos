# -*- coding: utf-8 -*-
from odoo.addons.account import SYSCOHADA_LIST, VAT_LIST


def _auto_install_l10n(env):
    # Check the country of the main company (only) and eventually load some module needed in that country
    country = env.company.country_id
    country_code = country.code
    if country_code:
        module_list = []
        if not env.company.chart_template:
            template_code = env['account.chart.template']._guess_chart_template(country)
            module_list.append(env['account.chart.template']._get_chart_template_mapping()[template_code]['module'])
        if country_code in ['US', 'CA']:
            module_list.append('account_check_printing')
        if country_code in SYSCOHADA_LIST + VAT_LIST:
            module_list.append('base_vat')
        if country_code == 'uk':
            module_list.append('account_bacs')
        elif country_code == 'CL':
            module_list.append('l10n_cl_fe')

        module_ids = env['ir.module.module'].search([('name', 'in', module_list), ('state', '=', 'uninstalled')])
        if module_ids:
            module_ids.sudo().button_install()


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
    ICPSudo.set_param("account.auto_send_dte", 1)
    ICPSudo.set_param("account.auto_send_email", True)
    ICPSudo.set_param("account.auto_send_persistencia", 24)

    for company in env["res.company"].sudo().search([]):
        env["account.chart.template"]._cl_asignar_cuentas_redondeo(company)

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
