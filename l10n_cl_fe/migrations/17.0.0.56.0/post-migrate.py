import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Plantilla = env['account.chart.template']
    for company in env['res.company'].search([]):
        Plantilla._cl_asignar_cuentas_redondeo(company)
        if company.chart_template != 'cl' or company.parent_id:
            continue
        try:
            with cr.savepoint():
                Plantilla._cl_cargar_impuestos_ila(company)
        except Exception:
            _logger.warning('No se pudieron cargar los impuestos ILA en %s', company.name, exc_info=True)
