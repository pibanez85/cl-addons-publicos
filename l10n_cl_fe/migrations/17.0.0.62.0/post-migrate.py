from odoo import SUPERUSER_ID, api

URL_TRAFQUIN = "https://trafquin.cl"
CAMPOS_PERIODO = [
    "f29_descuadres", "f29_debito", "f29_credito", "impuesto_ventas", "impuesto_compras", "f29_iva_pagar",
    "f29_remanente", "f29_ppm", "total_a_pagar", "recargo_f29", "f29_debito_odoo", "f29_credito_odoo",
    "f29_iva_pagar_odoo", "declaracion_estado", "declaracion_fecha", "declaracion_total", "vence", "anio",
    "rcv_estado", "contable_estado", "estado",
]


def _nombre_cron_f29(env):
    cron = env.ref("l10n_cl_fe.ir_cron_sii_revision_mensual", raise_if_not_found=False)
    if cron:
        for lang, _name in env["res.lang"].get_installed():
            cron.with_context(lang=lang).name = "Declaración Mensual F29 con el SII (propuesta, RCV y boletas)"


def _url_trafquin(cr):
    cr.execute("UPDATE res_company SET url_trafquin = %s WHERE COALESCE(url_trafquin, '') = ''", [URL_TRAFQUIN])


def _fecha_libros_de_la_declaracion(cr):
    cr.execute("""
        UPDATE account_move_book b
           SET date = (to_date(b.periodo_tributario || '-01', 'YYYY-MM-DD') + interval '1 month' - interval '1 day')::date
         WHERE coalesce(b.sii_result, 'draft') = 'draft'
           AND b.periodo_tributario ~ '^[0-9]{4}-[0-9]{2}$'
           AND b.id IN (SELECT compra_book_id FROM sii_revision_periodo WHERE compra_book_id IS NOT NULL
                        UNION SELECT venta_book_id FROM sii_revision_periodo WHERE venta_book_id IS NOT NULL)
    """)


def _cuentas_f29(env):
    Plantilla = env["account.chart.template"]
    for company in env["res.company"].search([("chart_template", "=", "cl")]):
        Plantilla._cl_asignar_cuentas_f29(company)


def _recalcular_periodos(env):
    Periodo = env["sii.revision.periodo"].with_context(active_test=False)
    periodos = Periodo.search([])
    for campo in CAMPOS_PERIODO:
        env.add_to_compute(Periodo._fields[campo], periodos)
    periodos.flush_recordset(CAMPOS_PERIODO)


def _retencion_honorarios(env):
    env.cr.execute("""
        SELECT res_id FROM ir_model_data
         WHERE module = 'account' AND model = 'account.tax' AND name LIKE '%%\\_I_IU2C'
    """)
    Tax = env["account.tax"].with_context(active_test=False)
    for tax in Tax.browse([r[0] for r in env.cr.fetchall()]).exists():
        env.cr.execute("""
            SELECT 1 FROM account_move_line WHERE tax_line_id = %s
            UNION ALL
            SELECT 1 FROM account_move_line_account_tax_rel WHERE account_tax_id = %s
            LIMIT 1
        """, [tax.id, tax.id])
        if env.cr.fetchone():
            continue
        if Tax.search_count([("company_id", "child_of", tax.company_id.root_id.id), ("type_tax_use", "=", "purchase"),
                             ("name", "=", "Retención Honorarios"), ("id", "!=", tax.id)]):
            continue
        valores_reparto = {"tag_ids": [(5,)]}
        if "sii_type" in tax.repartition_line_ids._fields:
            valores_reparto["sii_type"] = False
        tax.repartition_line_ids.write(valores_reparto)
        valores = {"name": "Retención Honorarios"}
        if "invoice_label" in tax._fields:
            valores["invoice_label"] = "Retención Honorarios"
        for lang, _name in env["res.lang"].get_installed():
            tax.with_context(lang=lang).write(valores)
        if tax.amount > 0:
            tax.amount = -tax.amount


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    _nombre_cron_f29(env)
    _url_trafquin(cr)
    _fecha_libros_de_la_declaracion(cr)
    _cuentas_f29(env)
    _retencion_honorarios(env)
    _recalcular_periodos(env)
