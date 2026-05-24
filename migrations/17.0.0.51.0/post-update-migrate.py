import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

_IVA_CODES = (14, 15)
_ADICIONAL_CODES = (24, 25, 26, 27, 271)
_ESPECIFICO_CODES = (28, 35, 51)
_EXTRA_CODES = _ADICIONAL_CODES + _ESPECIFICO_CODES


def migrate(cr, version):
    """Recalcula boletas de venta históricas para consolidar impuestos
    adicionales/específicos en el IVA, alineando el asiento contable
    con el DTE enviado al SII.
    """
    if not version:
        return
    _logger.info(
        "POST-MIGRATION: %s -> 17.0.0.51.0 - Consolidación contable de "
        "impuestos adicionales/específicos al IVA en boletas de venta",
        version,
    )

    cr.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'account_move_referencias' "
        "AND column_name IN ('ref_id', 'sii_referencia_TpoDocRef')"
    )
    cols = {row[0] for row in cr.fetchall()}
    if 'ref_id' in cols:
        ref_col = 'ref_id'
    elif 'sii_referencia_TpoDocRef' in cols:
        ref_col = 'sii_referencia_TpoDocRef'
    else:
        _logger.info("Migración boletas: tabla account_move_referencias sin columna de referencia conocida; se omite.")
        return

    cr.execute(
        f"""
        SELECT DISTINCT m.id
          FROM account_move m
          JOIN account_move_line ml ON ml.move_id = m.id
          JOIN account_tax t        ON t.id = ml.tax_line_id
          JOIN sii_document_class dc ON dc.id = m.document_class_id
         WHERE m.state = 'posted'
           AND m.move_type IN ('out_invoice', 'out_refund')
           AND t.sii_code IN %s
           AND (
               dc.sii_code IN (39, 41)
               OR EXISTS (
                   SELECT 1
                     FROM account_move_referencias r
                     JOIN sii_document_class dcref
                       ON dcref.id = r."{ref_col}"
                    WHERE r.move_id = m.id
                      AND dcref.sii_code IN (39, 41)
               )
           )
        """,
        (_EXTRA_CODES,),
    )
    move_ids = [row[0] for row in cr.fetchall()]
    if not move_ids:
        _logger.info("Migración boletas: no hay movimientos a recalcular.")
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    moves = env["account.move"].browse(move_ids)

    ok, skipped, failed = 0, 0, 0
    for move in moves:
        try:
            if not (move.es_boleta() or move.es_nc_boleta()):
                skipped += 1
                continue
            move._consolidate_boleta_taxes_to_iva()
            move.with_context(
                check_move_validity=False, skip_invoice_sync=True,
            )._compute_amount()
            ok += 1
        except Exception as e:
            failed += 1
            _logger.warning(
                "No se pudo recalcular boleta %s (id=%s): %s",
                move.display_name, move.id, e,
            )

    _logger.info(
        "Migración boletas adicional/específico → IVA: "
        "%d recalculadas, %d ignoradas, %d con error sobre %d candidatas.",
        ok, skipped, failed, len(move_ids),
    )
