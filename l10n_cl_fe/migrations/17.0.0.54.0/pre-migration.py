# Copyright 2026 Daniel Santibáñez Polanco
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
import logging

_logger = logging.getLogger(__name__)

VIEJO = "l10n_cl_currency_rate_sbif"


def _borrar_vistas(cr, condicion, params=()):
    cr.execute(
        f"""
        WITH d AS (
            DELETE FROM ir_model_data
            WHERE model = 'ir.ui.view' AND {condicion}
            RETURNING res_id
        )
        DELETE FROM ir_ui_view WHERE id IN (SELECT res_id FROM d)
        """,
        params,
    )
    return cr.rowcount


def migrate(cr, version):
    """Absorbe l10n_cl_currency_rate_sbif: la actualización diaria de UF, UTM, dólar
    y euro desde la API de la CMF (ex SBIF) pasa a ser parte de l10n_cl_fe.

    Si la base lo tiene instalado aparte, sus xmlid pasan a l10n_cl_fe: el cron y
    los parámetros (clave y URL de la API) se conservan en vez de duplicarse, y el
    módulo queda desinstalado. Si ya lo fusionó OpenUpgrade (merged_modules del
    apriori de 16.0 y 17.0) solo quedan sus vistas, ahora con xmlid de l10n_cl_fe.
    En ambos casos se borran sus vistas: las reemplazan las de l10n_cl_fe, y las
    de 16.0 usan attrs, que 17.0 rechaza al validar.
    """
    if not version:
        return
    borradas = _borrar_vistas(cr, "module = %s", (VIEJO,))
    borradas += _borrar_vistas(
        cr,
        "module = 'l10n_cl_fe' AND (name = 'view_res_currency_form'"
        " OR name LIKE 'res_config_settings_view_form_openupgrade_%%')",
    )
    cr.execute("SELECT state FROM ir_module_module WHERE name = %s", (VIEJO,))
    row = cr.fetchone()
    if not row or row[0] not in ("installed", "to upgrade", "to remove"):
        if borradas:
            _logger.info("%s vistas de %s borradas", borradas, VIEJO)
        return
    cr.execute(
        """
        UPDATE ir_model_data SET module = 'l10n_cl_fe'
        WHERE module = %s
          AND name NOT IN (SELECT name FROM ir_model_data WHERE module = 'l10n_cl_fe')
        """,
        (VIEJO,),
    )
    # Los que chocan (model_res_currency y demás) apuntan a registros que ya tienen
    # el xmlid de l10n_cl_fe: sobra la referencia, no el registro.
    cr.execute("DELETE FROM ir_model_data WHERE module = %s", (VIEJO,))
    cr.execute(
        "UPDATE ir_module_module SET state = 'uninstalled', latest_version = NULL WHERE name = %s",
        (VIEJO,),
    )
    cr.execute("DELETE FROM ir_module_module_dependency WHERE name = %s", (VIEJO,))
    _logger.info("%s absorbido por l10n_cl_fe (%s vistas viejas borradas)", VIEJO, borradas)
