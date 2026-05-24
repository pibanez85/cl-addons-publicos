import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

_REL_TABLES = [
    ('sii_cola_envio_account_move_rel', 'account_move_id', 'account_move'),
    ('sii_cola_envio_book_rel', 'account_move_book_id', 'account_move_book'),
    ('sii_cola_envio_consumo_folios_rel', 'account_move_consumo_folios_id', 'account_move_consumo_folios'),
    ('sii_cola_envio_boleta_voucher_rel', 'account_move_boleta_voucher_id', 'account_move_boleta_voucher'),
]


def _split_cola(cr, env, cola_id):
    """Si la cola tiene mezcla de docs con/sin envio, la deja con los docs
    sin envio (tipo_trabajo='envio') y mueve los docs ya enviados a una
    cola nueva por cada envio_id, con envio_id seteado y tipo_trabajo='consulta'."""
    cola = env['sii.cola_envio'].browse(cola_id)
    if not cola.exists():
        return 0

    # Recolectar docs (modelo, doc_id, envio_id) de cada relation
    docs_by_envio = {}
    docs_sin_envio = []
    for rel_table, doc_col, doc_table in _REL_TABLES:
        cr.execute(
            f"SELECT rel.{doc_col}, d.sii_xml_request "
            f"FROM {rel_table} rel JOIN {doc_table} d ON d.id = rel.{doc_col} "
            f"WHERE rel.sii_cola_envio_id = %s",
            (cola_id,),
        )
        for doc_id, envio_id in cr.fetchall():
            entry = (rel_table, doc_col, doc_id)
            if envio_id:
                docs_by_envio.setdefault(envio_id, []).append(entry)
            else:
                docs_sin_envio.append(entry)

    if not docs_by_envio:
        return 0

    # Si todos los docs son del mismo envio (o todos sin envio), no hay mezcla
    if not docs_sin_envio and len(docs_by_envio) == 1:
        envio_id = next(iter(docs_by_envio))
        if cola.envio_id.id != envio_id:
            cr.execute(
                "UPDATE sii_cola_envio SET envio_id = %s WHERE id = %s",
                (envio_id, cola_id),
            )
        return 0

    # Hay mezcla: crear cola nueva por cada envio distinto y mover sus docs
    splits = 0
    for envio_id, entries in docs_by_envio.items():
        cr.execute(
            "INSERT INTO sii_cola_envio "
            "(name, tipo_trabajo, active, company_id, firma_id, set_pruebas, "
            " es_boleta, manual, envio_id, n_atencion, sequence, intento, queue_log, "
            " create_date, write_date, create_uid, write_uid) "
            "SELECT name, 'consulta', active, company_id, firma_id, set_pruebas, "
            "       es_boleta, manual, %s, n_atencion, sequence, intento, queue_log, "
            "       NOW(), NOW(), create_uid, write_uid "
            "FROM sii_cola_envio WHERE id = %s "
            "RETURNING id",
            (envio_id, cola_id),
        )
        new_cola_id = cr.fetchone()[0]
        for rel_table, doc_col, doc_id in entries:
            cr.execute(
                f"DELETE FROM {rel_table} "
                f"WHERE sii_cola_envio_id = %s AND {doc_col} = %s",
                (cola_id, doc_id),
            )
            cr.execute(
                f"INSERT INTO {rel_table} (sii_cola_envio_id, {doc_col}) "
                f"VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (new_cola_id, doc_id),
            )
        splits += 1

    # La cola original queda con los docs sin envio en tipo_trabajo='envio'
    cr.execute(
        "UPDATE sii_cola_envio SET envio_id = NULL, tipo_trabajo = 'envio' WHERE id = %s",
        (cola_id,),
    )
    return splits


def migrate(cr, version):
    if not version:
        return
    _logger.info(f"POST-MIGRATION: l10n_cl_fe {version} -> 17.0.0.52.2 - Asignar envio_id a sii_cola_envio y separar colas con docs mixtos")

    env = api.Environment(cr, SUPERUSER_ID, {})

    cr.execute("SELECT id FROM sii_cola_envio WHERE active = TRUE")
    cola_ids = [r[0] for r in cr.fetchall()]
    total_splits = 0
    for cola_id in cola_ids:
        try:
            total_splits += _split_cola(cr, env, cola_id)
        except Exception as e:
            _logger.warning("No se pudo migrar cola %s: %s", cola_id, e, exc_info=True)
    _logger.info(
        "Migración cola → envio_id: %s colas revisadas, %s envíos extraídos a nuevas colas.",
        len(cola_ids), total_splits,
    )
