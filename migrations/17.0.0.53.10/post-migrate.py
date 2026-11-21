import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return
    _logger.info(
        f"POST-MIGRATION: {version} -> 17.0.0.53.10 - "
        "use_documents sólo en diarios de venta/compra y movimientos con documento DTE, "
        "recálculo de discount_amount (equivalente a 16.0.0.46.6/46.7/46.8)"
    )
    cr.execute("""
        UPDATE account_journal
           SET use_documents = FALSE
         WHERE use_documents = TRUE
           AND type NOT IN ('sale', 'purchase')
    """)
    _logger.info("POST-MIGRATION: %s diarios sin use_documents", cr.rowcount)
    cr.execute("""
        UPDATE account_move
           SET use_documents = FALSE
         WHERE use_documents = TRUE
           AND document_class_id IN (SELECT id FROM sii_document_class WHERE dte = FALSE)
    """)
    _logger.info("POST-MIGRATION: %s movimientos sin use_documents", cr.rowcount)

    env = api.Environment(cr, SUPERUSER_ID, {})
    lines = env['account.move.line'].search([
        ('discount', '>', 0),
        ('discount_amount', '=', 0),
    ])
    lines.with_context(check_move_validity=False).set_discount_amount()
    _logger.info("POST-MIGRATION: discount_amount recalculado en %s líneas", len(lines))
