import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """sii_result pasa a ser el campo stored canónico de envío SII en libros
    y consumo de folios (heredado del envío-mixin), y se elimina `state`.
    Renombramos la columna física para preservar datos.
    """
    if not version:
        return
    _logger.info(f"PRE-MIGRATION: {version} -> 17.0.0.52.0 - rename state → sii_result en libros y consumo de folios")

    for table in ("account_move_book", "account_move_consumo_folios", "stock_picking_book", "account_move_boleta_voucher"):
        cr.execute(
            "SELECT to_regclass(%s)", (f'public.{table}',),
        )
        if cr.fetchone()[0] is None:
            continue
        cr.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s AND column_name IN ('state', 'sii_result')",
            (table,),
        )
        cols = {row[0] for row in cr.fetchall()}
        if "state" in cols and "sii_result" not in cols:
            cr.execute(f'ALTER TABLE {table} RENAME COLUMN state TO sii_result')
            _logger.info(f"  {table}: state → sii_result")
        elif "state" in cols and "sii_result" in cols:
            cr.execute(
                f"UPDATE {table} SET sii_result = state WHERE sii_result IS NULL AND state IS NOT NULL"
            )
            cr.execute(f'ALTER TABLE {table} DROP COLUMN state')
            _logger.info(f"  {table}: backfill sii_result desde state, drop state")
        else:
            _logger.info(f"  {table}: nada que migrar (state={'sí' if 'state' in cols else 'no'}, sii_result={'sí' if 'sii_result' in cols else 'no'})")

    for table in ("account_move_consumo_folios", "pos_order", "stock_picking"):
        cr.execute("SELECT to_regclass(%s)", (f'public.{table}',))
        if cr.fetchone()[0] is None:
            continue
        cr.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = 'responsable_envio'",
            (table,),
        )
        if cr.fetchone():
            cr.execute(f'ALTER TABLE {table} DROP COLUMN responsable_envio')
            _logger.info(f"  {table}: drop columna legacy responsable_envio")

    for table in ("pos_order", "account_move_boleta_voucher"):
        cr.execute("SELECT to_regclass(%s)", (f'public.{table}',))
        if cr.fetchone()[0] is None:
            continue
        cr.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = 'sii_document_number'",
            (table,),
        )
        row = cr.fetchone()
        if row and row[0] == 'integer':
            cr.execute(f'ALTER TABLE {table} ALTER COLUMN sii_document_number TYPE bigint')
            _logger.info(f"  {table}: sii_document_number int4 → int8 (BigInt)")
