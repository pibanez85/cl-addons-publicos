# Part of Odoo. See LICENSE file for full copyright and licensing details.
import logging

from odoo import Command, models
from odoo.addons.account.models.chart_template import template

_logger = logging.getLogger(__name__)

CUENTA_GANANCIA_REDONDEO = ('420106', 'Ganancia por Redondeo Efectivo', 'income_other')
CUENTAS_ILA = ('account_110709', 'account_210710')
CUENTA_PERDIDA_REDONDEO = ('410104', 'Pérdida por Redondeo Efectivo', 'expense')
CUENTAS_F29 = {
    'f29_cuenta_debito_id': '210701',
    'f29_cuenta_credito_id': '110701',
    'f29_cuenta_remanente_id': '110703',
    'f29_cuenta_ppm_id': '110705',
    'f29_cuenta_retencion_id': '210704',
    'f29_cuenta_impuesto_unico_id': '210703',
    'f29_cuenta_por_pagar_id': '210706',
    'f29_cuenta_recargo_id': '620203',
    'f29_cuenta_reajuste_id': '420203',
    'f29_cuenta_postergado_id': '210711',
}


class AccountChartTemplate(models.AbstractModel):
    _inherit = 'account.chart.template'

    def _get_chart_template_mapping(self, get_all=False):
        res = super()._get_chart_template_mapping(get_all=get_all)
        fe = self.env['ir.module.module'].sudo().search([('name', '=', 'l10n_cl_fe')], limit=1)
        res.update({k: v for k, v in (fe.account_templates or {}).items() if get_all or v['visible']})
        return res

    def _post_load_data(self, template_code, company, template_data):
        res = super()._post_load_data(template_code, company, template_data)
        if template_code == 'cl':
            self._cl_asignar_cuentas_redondeo(company or self.env.company)
            self._cl_asignar_cuentas_f29(company or self.env.company)
        return res

    def _cl_asignar_cuentas_f29(self, company):
        company = company.sudo()
        Cuenta = self.env['account.account'].sudo().with_company(company)
        valores = {}
        for campo, codigo in CUENTAS_F29.items():
            if not company[campo]:
                cuenta = Cuenta.search([('code', '=', codigo), ('company_ids', 'parent_of', company.id)], limit=1)
                if cuenta:
                    valores[campo] = cuenta.id
        if not company.f29_diario_id:
            diario = self.env['account.journal'].sudo().search(
                [('company_id', '=', company.id), ('type', '=', 'general')], order='sequence, id', limit=1)
            if diario:
                valores['f29_diario_id'] = diario.id
        if valores:
            company.write(valores)
        return valores

    def _cl_cuenta_redondeo(self, company, datos):
        codigo, nombre, tipo = datos
        Cuenta = self.env['account.account'].sudo().with_company(company)
        if self.env['res.lang']._lang_get('es_CL'):  # Odoo 20 falla con un idioma no activo en el contexto
            Cuenta = Cuenta.with_context(lang='es_CL')
        tipos = ('income', 'income_other') if tipo == 'income_other' else ('expense', 'expense_other')
        cuentas = Cuenta.search([('company_ids', 'parent_of', company.id), ('account_type', 'in', tipos)])
        cuenta = cuentas.filtered(lambda c: any(palabra in (c.name or '').lower() for palabra in ('redondeo', 'rounding')))[:1]
        if cuenta:
            return cuenta
        if Cuenta.search_count([('code', '=', codigo), ('company_ids', 'parent_of', company.id)], limit=1):
            _logger.warning('El código %s ya está ocupado en %s: asigne a mano la cuenta de redondeo.', codigo, company.name)
            return Cuenta
        return Cuenta.create({'code': codigo, 'name': nombre, 'account_type': tipo, 'company_ids': [Command.set(company.root_id.ids)]})

    def _cl_asignar_cuentas_redondeo(self, company):
        Cuenta = self.env['account.account'].sudo().with_company(company)
        if not Cuenta.search_count([('company_ids', 'parent_of', company.id)], limit=1):
            return
        ganancia = self._cl_cuenta_redondeo(company, CUENTA_GANANCIA_REDONDEO)
        perdida = self._cl_cuenta_redondeo(company, CUENTA_PERDIDA_REDONDEO)
        if not ganancia or not perdida:
            return
        for redondeo in self.env['account.cash.rounding'].sudo().with_company(company).search([]):
            valores = {}
            if (not redondeo.profit_account_id or redondeo.profit_account_id.account_type == 'asset_cash'):
                valores['profit_account_id'] = ganancia.id
            if (not redondeo.loss_account_id or redondeo.loss_account_id.account_type == 'asset_cash'):
                valores['loss_account_id'] = perdida.id
            if valores:
                redondeo.write(valores)

    def _cl_cargar_impuestos_ila(self, company):
        Plantilla = self.sudo().with_company(company).with_context(lang='en_US')
        if Plantilla.ref('ila_24_compra', raise_if_not_found=False):
            return
        cuentas = Plantilla._get_account_account('cl')
        faltantes = [k for k in CUENTAS_ILA if not Plantilla.ref(k, raise_if_not_found=False)]
        ocupados = self.env['account.account'].sudo().search_count([
            ('code', 'in', [cuentas[k]['code'] for k in faltantes]),
            ('company_ids', 'parent_of', company.id),
        ], limit=1)
        if ocupados:
            _logger.warning('Los códigos de cuenta ILA ya están ocupados en %s: cree a mano los impuestos ILA.', company.name)
            return
        datos = {'account.account': {k: cuentas[k] for k in faltantes}}
        if not Plantilla.ref('tax_group_ila', raise_if_not_found=False):
            datos['account.tax.group'] = {'tax_group_ila': Plantilla._get_account_tax_group('cl')['tax_group_ila']}
        datos['account.tax'] = {k: v for k, v in Plantilla._get_account_tax('cl').items() if k.startswith('ila_')}
        Plantilla._load_data(datos)
        Plantilla._load_translations(companies=company)

    @template('cl')
    def _get_cl_template_data(self):
        return {
            'code_digits': '6',
            'property_account_receivable_id': 'account_110301',
            'property_account_payable_id': 'account_210201',
            'property_account_expense_categ_id': 'account_530201',
            'property_account_income_categ_id': 'account_410201',
            'property_stock_account_input_categ_id': 'account_110605',
            'property_stock_account_output_categ_id': 'account_110606',
            'property_stock_valuation_account_id': 'account_110607',
        }

    @template('cl', 'res.company')
    def _get_cl_res_company(self):
        return {
            self.env.company.id: {
                'anglo_saxon_accounting': True,
                'account_fiscal_country_id': 'base.cl',
                'bank_account_code_prefix': '1101',
                'cash_account_code_prefix': '1101',
                'transfer_account_code_prefix': '117',
                'account_default_pos_receivable_account_id': 'account_110403',
                'income_currency_exchange_account_id': 'account_620201',
                'expense_currency_exchange_account_id': 'account_620201',
                'tax_calculation_rounding_method': 'round_globally',
                'account_sale_tax_id': 'IVAV_19',
                'account_purchase_tax_id': 'IVAC_19',
            },
        }
