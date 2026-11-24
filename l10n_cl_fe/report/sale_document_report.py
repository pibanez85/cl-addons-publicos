from odoo import api, fields, models
from odoo.tools import SQL


class SaleDocumentReport(models.Model):
    _name = 'l10n_cl.sale.document.report'
    _description = "Ventas por tipo de documento"
    _auto = False
    _order = 'date desc, id desc'
    _rec_name = 'name'

    date = fields.Date(string="Fecha", readonly=True)
    document_class_id = fields.Many2one('sii.document_class', string="Tipo de documento", readonly=True)
    folio = fields.Char(string="Folio", readonly=True)
    name = fields.Char(string="Referencia", readonly=True)
    origen = fields.Selection([('account', "Contabilidad")], string="Origen", readonly=True)
    journal_id = fields.Many2one('account.journal', string="Diario", readonly=True)
    partner_id = fields.Many2one('res.partner', string="Cliente", readonly=True)
    user_id = fields.Many2one('res.users', string="Vendedor", readonly=True)
    company_id = fields.Many2one('res.company', string="Compañía", readonly=True)
    currency_id = fields.Many2one('res.currency', string="Moneda", readonly=True)
    amount_untaxed = fields.Monetary(string="Neto + exento", readonly=True)
    amount_tax = fields.Monetary(string="Impuestos", readonly=True)
    amount_total = fields.Monetary(string="Total", readonly=True)

    @api.model
    def _origen_account(self):
        return SQL("'account'")

    @api.model
    def _select_extra_account(self):
        return SQL("")

    @api.model
    def _query_account(self):
        return SQL(
            """
            SELECT m.id * 2                     AS id,
                   m.invoice_date               AS date,
                   m.document_class_id          AS document_class_id,
                   m.sii_document_number::varchar AS folio,
                   m.name                       AS name,
                   %(origen)s                   AS origen,
                   m.journal_id                 AS journal_id,
                   m.commercial_partner_id      AS partner_id,
                   m.invoice_user_id            AS user_id,
                   m.company_id                 AS company_id,
                   c.currency_id                AS currency_id,
                   m.amount_untaxed_signed      AS amount_untaxed,
                   m.amount_tax_signed          AS amount_tax,
                   m.amount_total_signed        AS amount_total
                   %(extra)s
              FROM account_move m
              JOIN res_company c ON c.id = m.company_id
             WHERE m.move_type IN ('out_invoice', 'out_refund', 'out_receipt')
               AND m.state = 'posted'
            """,
            origen=self._origen_account(),
            extra=self._select_extra_account(),
        )

    @api.model
    def _queries(self):
        return [self._query_account()]

    @property
    def _table_sql(self):
        # Odoo 20: _table_query pasó a _table_sql (subconsulta entre paréntesis)
        return SQL("(%s)", SQL(" UNION ALL ").join(self._queries()))
