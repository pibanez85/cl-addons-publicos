from odoo import api, fields, models
from odoo.tools import SQL


class VoucherFacturaReport(models.Model):
    _name = 'l10n_cl.voucher.factura.report'
    _description = "Pagos electrónicos de facturas"
    _auto = False
    _order = 'date desc, id desc'
    _rec_name = 'pago'

    date = fields.Date(string="Fecha del pago", readonly=True)
    canal = fields.Selection(
        [('presencial', "Presencial"), ('internet', "Internet")],
        string="Canal", readonly=True,
    )
    origen = fields.Selection([('account', "Contabilidad")], string="Origen", readonly=True)
    move_id = fields.Many2one('account.move', string="Factura", readonly=True)
    document_class_id = fields.Many2one('sii.document_class', string="Tipo de documento", readonly=True)
    folio = fields.Char(string="Folio", readonly=True)
    partner_id = fields.Many2one('res.partner', string="Cliente", readonly=True)
    journal_id = fields.Many2one('account.journal', string="Medio de pago", readonly=True)
    pago = fields.Char(string="Pago", readonly=True)
    company_id = fields.Many2one('res.company', string="Compañía", readonly=True)
    currency_id = fields.Many2one('res.currency', string="Moneda", readonly=True)
    documentos = fields.Integer(string="Tot. Documentos", readonly=True)
    monto_total = fields.Monetary(string="Monto Total", readonly=True)
    monto_neto = fields.Monetary(string="Monto Neto", readonly=True)
    monto_iva = fields.Monetary(string="Monto IVA", readonly=True)
    monto_exento = fields.Monetary(string="Monto Exento", readonly=True)

    @api.model
    def _excluir_account(self):
        return SQL("")

    @api.model
    def _origen_account(self):
        return SQL("'account'")

    @api.model
    def _pagos_account(self):
        return SQL(
            """
            SELECT pr.id * 2                AS id,
                   pm.date                  AS date,
                   %(origen)s               AS origen,
                   m.id                     AS move_id,
                   pm.journal_id            AS journal_id,
                   pm.name                  AS pago,
                   pr.amount                AS monto
              FROM account_partial_reconcile pr
              JOIN account_move_line dl ON dl.id = pr.debit_move_id
              JOIN account_move m ON m.id = dl.move_id
              JOIN account_move_line cl ON cl.id = pr.credit_move_id
              JOIN account_move pm ON pm.id = cl.move_id
             WHERE pm.state = 'posted'
               %(excluir)s
            """,
            origen=self._origen_account(),
            excluir=self._excluir_account(),
        )

    @api.model
    def _pagos(self):
        return [self._pagos_account()]

    @property
    def _table_sql(self):
        # Odoo 20: _table_query pasó a _table_sql (subconsulta entre paréntesis)
        return SQL("(%s)", self._consulta())

    @api.model
    def _consulta(self):
        return SQL(
            """
            SELECT x.*,
                   x.monto_total - x.monto_exento - x.monto_neto AS monto_iva
              FROM (
                SELECT p.id,
                       p.date,
                       CASE WHEN EXISTS (
                            SELECT 1 FROM res_company_medios_pago_internet_rel ri
                             WHERE ri.company_id = m.company_id AND ri.journal_id = p.journal_id)
                            THEN 'internet' ELSE 'presencial' END AS canal,
                       p.origen,
                       m.id                         AS move_id,
                       m.document_class_id          AS document_class_id,
                       m.sii_document_number::varchar AS folio,
                       m.commercial_partner_id      AS partner_id,
                       p.journal_id,
                       p.pago,
                       m.company_id,
                       c.currency_id,
                       1                            AS documentos,
                       p.monto                      AS monto_total,
                       ROUND(p.monto * COALESCE(ex.exento, 0) / NULLIF(m.amount_total_signed, 0)) AS monto_exento,
                       ROUND((p.monto - ROUND(p.monto * COALESCE(ex.exento, 0) / NULLIF(m.amount_total_signed, 0))) / 1.19) AS monto_neto
                  FROM (%(pagos)s) p
                  JOIN account_move m ON m.id = p.move_id
                  JOIN res_company c ON c.id = m.company_id
                  JOIN sii_document_class dc ON dc.id = m.document_class_id
                  LEFT JOIN LATERAL (
                       SELECT -SUM(l.balance) AS exento
                         FROM account_move_line l
                        WHERE l.move_id = m.id
                          AND l.display_type = 'product'
                          AND NOT EXISTS (
                              SELECT 1
                                FROM account_move_line_account_tax_rel r
                                JOIN account_tax t ON t.id = r.account_tax_id
                               WHERE r.account_move_line_id = l.id AND t.amount > 0)
                  ) ex ON TRUE
                 WHERE m.move_type = 'out_invoice'
                   AND m.state = 'posted'
                   AND dc.sii_code IN (33, 34)
                   AND c.modelo_emision_boleta = 'voucher'
                   AND p.monto > 0
                   AND EXISTS (
                       SELECT 1 FROM account_journal_res_company_rel rm
                        WHERE rm.res_company_id = m.company_id AND rm.account_journal_id = p.journal_id)
              ) x
            """,
            pagos=SQL(" UNION ALL ").join(self._pagos()),
        )
