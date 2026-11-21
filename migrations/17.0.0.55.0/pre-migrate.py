from odoo.tools.sql import column_exists, rename_column


def migrate(cr, version):
    if column_exists(cr, "account_move", "chofer_id") and not column_exists(cr, "account_move", "chofer"):
        rename_column(cr, "account_move", "chofer_id", "chofer")
