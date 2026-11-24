import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountJournalSiiDocumentClass(models.Model):
    _name = "account.journal.sii_document_class"
    _description = "Journal SII Documents"
    _order = "sequence"

    @api.depends("sii_document_class_id", "sequence_id")
    def get_secuence_name(self):
        for r in self:
            sequence_name = r.sii_document_class_id.name or ""
            if r.sequence_id:
                sequence_name = "({}) {}".format(r.qty_available, r.sequence_id.name)
            r.name = sequence_name

    name = fields.Char(compute="get_secuence_name",)
    sii_document_class_id = fields.Many2one("sii.document_class", string="Document Type", required=True,)
    sequence_id = fields.Many2one(
        "ir.sequence",
        string="Entry Sequence",
        help="""This field contains the information related to the numbering \
            of the documents entries of this document type.""",
        domain=[('is_dte', '=', True)],
        check_company=True,
    )
    journal_id = fields.Many2one("account.journal", string="Journal", required=True,)
    sequence = fields.Integer(string="Sequence")
    company_id = fields.Many2one("res.company", related="journal_id.company_id", store=True,)
    qty_available = fields.Integer(string="Quantity Available", related="sequence_id.qty_available")
    is_dte = fields.Boolean(string="Is DTE", related="sii_document_class_id.dte")

    @api.onchange("sii_document_class_id")
    def check_sii_document_class(self):
        dc = self.sii_document_class_id
        if self.sequence_id and dc != self.sequence_id.sii_document_class_id:
            raise UserError("El tipo de Documento de la secuencia es distinto")
        if dc.es_guia():
            raise UserError("Las guías no es pueden agregar por esta área")
        if dc.es_voucher() and self.sequence_id:
            self.sequence_id = False

    def _siguiente_folio(self):
        self.ensure_one()
        if not self.sequence_id or not self.sequence_id.is_dte:
            return 0
        try:
            return self.sequence_id.get_folio(readonly=True)
        except Exception:
            _logger.warning(
                "No se pudo obtener el siguiente folio de la secuencia %s", self.sequence_id.id, exc_info=True
            )
            return 0

    @api.depends_context("mostrar_folio")
    def _compute_display_name(self):
        if not self.env.context.get("mostrar_folio"):
            return super()._compute_display_name()
        for r in self:
            if not r.sequence_id:
                r.display_name = r.name
                continue
            folio = r._siguiente_folio()
            detalle = "folio {}, {} disponibles".format(folio, r.qty_available) if folio else "sin folios"
            r.display_name = "{} ({})".format(r.sequence_id.name, detalle)

    @api.model
    def _name_search(self, name, domain=None, operator="ilike", limit=100, order=None):
        domain = domain or []
        recs = self.browse()
        if name:
            recs = self.search(
                ["|", ("sequence_id.name", "=", name), ("sii_document_class_id.name", "=", name)] + domain, limit=limit
            )
        if not recs:
            recs = self.search(
                ["|", ("sequence_id.name", operator, name), ("sii_document_class_id.name", operator, name)] + domain,
                limit=limit,
            )
        return recs.ids
