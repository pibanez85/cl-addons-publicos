import logging

_logger = logging.getLogger(__name__)

RENAMES = {
    'account_move_referencias': [
        ('origen', 'folio'),
        ('sii_referencia_TpoDocRef', 'ref_id'),
        ('sii_referencia_CodRef', 'cod_ref'),
    ],
    'sale_order_referencias': [
        ('origen', 'folio'),
        ('sii_referencia_TpoDocRef', 'ref_id'),
    ],
}


def _column_exists(cr, table, column):
    cr.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
        (table, column),
    )
    return bool(cr.fetchone())


def _rename(cr, table, old, new):
    if not _column_exists(cr, table, old):
        return
    if _column_exists(cr, table, new):
        cr.execute(
            f'UPDATE "{table}" SET "{new}" = "{old}" WHERE "{new}" IS NULL AND "{old}" IS NOT NULL'
        )
        cr.execute(f'ALTER TABLE "{table}" DROP COLUMN "{old}"')
    else:
        cr.execute(f'ALTER TABLE "{table}" RENAME COLUMN "{old}" TO "{new}"')


def migrate(cr, version):
    if not version:
        return
    _logger.info(f"PRE-MIGRATION: l10n_cl_fe {version} -> 17.0.0.53.0 - rename referencias columns (folio/ref_id/cod_ref)")
    for table, pairs in RENAMES.items():
        for old, new in pairs:
            _rename(cr, table, old, new)
