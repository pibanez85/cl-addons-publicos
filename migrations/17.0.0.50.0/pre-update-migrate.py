import logging

_logger = logging.getLogger(__name__)


def migrate(cr, installed_version):
    """Pobla firma_id en sii_cola_envio desde el user_id histórico."""
    _logger.info("Migrando sii_cola_envio a 17.0.0.50.0 (firma_id desde user_id)")

    cr.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'sii_cola_envio'
        AND column_name IN ('user_id', 'firma_id')
    """)
    cols = {row[0] for row in cr.fetchall()}
    if 'user_id' not in cols:
        _logger.info("user_id ya no existe en sii_cola_envio, nada que migrar")
        return

    if 'firma_id' not in cols:
        cr.execute("ALTER TABLE sii_cola_envio ADD COLUMN firma_id integer")

    cr.execute("""
        SELECT id, user_id, company_id
        FROM sii_cola_envio
        WHERE firma_id IS NULL
        AND user_id IS NOT NULL
        AND company_id IS NOT NULL
    """)
    rows = cr.fetchall()
    if not rows:
        _logger.info("Sin colas pendientes de asignar firma")
        return

    cr.execute("""
        SELECT DISTINCT ON (uu.res_users_id, cc.res_company_id)
               uu.res_users_id, cc.res_company_id, f.id
        FROM sii_firma f
        JOIN res_users_sii_firma_rel uu ON uu.sii_firma_id = f.id
        JOIN res_company_sii_firma_rel cc ON cc.sii_firma_id = f.id
        WHERE f.active = TRUE AND f.state = 'valid'
        ORDER BY uu.res_users_id, cc.res_company_id, f.priority ASC, f.id ASC
    """)
    firma_por_user_company = {
        (user_id, company_id): firma_id
        for user_id, company_id, firma_id in cr.fetchall()
    }

    cr.execute("""
        SELECT DISTINCT ON (cc.res_company_id)
               cc.res_company_id, f.id
        FROM sii_firma f
        JOIN res_company_sii_firma_rel cc ON cc.sii_firma_id = f.id
        WHERE f.active = TRUE AND f.state = 'valid'
        ORDER BY cc.res_company_id, f.priority ASC, f.id ASC
    """)
    firma_por_company = dict(cr.fetchall())

    actualizados = 0
    sin_firma = 0
    for cola_id, user_id, company_id in rows:
        firma_id = firma_por_user_company.get((user_id, company_id))
        if not firma_id:
            firma_id = firma_por_company.get(company_id)
        if firma_id:
            cr.execute(
                "UPDATE sii_cola_envio SET firma_id = %s WHERE id = %s",
                (firma_id, cola_id),
            )
            actualizados += 1
        else:
            sin_firma += 1

    _logger.info(
        "sii_cola_envio migradas: %s con firma, %s sin firma disponible",
        actualizados, sin_firma,
    )
