import logging

_logger = logging.getLogger(__name__)


def migrate(cr, installed_version):
    """Limpia packs 'consulta' que quedaron con documentos sin enviar."""
    if not installed_version:
        return

    rels = [
        ('sii_cola_envio_account_move_rel', 'account_move_id', 'account_move'),
        ('sii_cola_envio_book_rel', 'account_move_book_id', 'account_move_book'),
        ('sii_cola_envio_consumo_folios_rel', 'account_move_consumo_folios_id', 'account_move_consumo_folios'),
        ('sii_cola_envio_boleta_voucher_rel', 'account_move_boleta_voucher_id', 'account_move_boleta_voucher'),
    ]

    for rel, col, doc_table in rels:
        cr.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
            (rel,),
        )
        if not cr.fetchone():
            continue
        cr.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = 'sii_xml_request'",
            (doc_table,),
        )
        if not cr.fetchone():
            continue
        cr.execute(
            f"""
            DELETE FROM {rel}
            WHERE sii_cola_envio_id IN (
                SELECT id FROM sii_cola_envio
                WHERE tipo_trabajo = 'consulta' AND active = TRUE
            )
            AND {col} IN (
                SELECT d.id FROM {doc_table} d
                LEFT JOIN sii_xml_envio x ON x.id = d.sii_xml_request
                WHERE d.sii_xml_request IS NULL
                   OR x.sii_send_ident IS NULL
                   OR x.sii_send_ident = ''
            )
            """
        )
        _logger.info("%s: %d links despegados", rel, cr.rowcount)

    cr.execute("""
        DELETE FROM sii_cola_envio
        WHERE tipo_trabajo = 'consulta'
          AND active = TRUE
          AND NOT EXISTS (SELECT 1 FROM sii_cola_envio_account_move_rel r WHERE r.sii_cola_envio_id = sii_cola_envio.id)
          AND NOT EXISTS (SELECT 1 FROM sii_cola_envio_book_rel r WHERE r.sii_cola_envio_id = sii_cola_envio.id)
          AND NOT EXISTS (SELECT 1 FROM sii_cola_envio_consumo_folios_rel r WHERE r.sii_cola_envio_id = sii_cola_envio.id)
          AND NOT EXISTS (SELECT 1 FROM sii_cola_envio_boleta_voucher_rel r WHERE r.sii_cola_envio_id = sii_cola_envio.id)
    """)
    _logger.info("Colas 'consulta' vacías eliminadas: %d", cr.rowcount)
