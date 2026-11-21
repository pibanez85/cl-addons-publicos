from odoo import fields, models


class DteReferenciaMixin(models.AbstractModel):
    _name = 'l10n_cl_fe.dte.referencia.mixin'
    _description = 'Mixin para líneas de referencia DTE'
    _order = 'sequence ASC'

    folio = fields.Char(string="Folio Referencia")
    ref_id = fields.Many2one(
        'sii.document_class', string="Tipo Documento Referencia",
    )
    cod_ref = fields.Selection(
        [
            ('1', 'Anula Documento de Referencia'),
            ('2', 'Corrige texto Documento Referencia'),
            ('3', 'Corrige montos'),
        ],
        string="Código Referencia",
    )
    motivo = fields.Char(string="Motivo")
    fecha_documento = fields.Date(string="Fecha Documento")
    rut_otro = fields.Char(string="RUT Otro Contribuyente")
    ind_global = fields.Boolean(
        string="Referencia Global",
        help="Referencia a más de 20 documentos del mismo tipo, por ejemplo la factura de todas las guías del mes. El folio va en 0 y el motivo explica qué documentos abarca.",
    )
    sequence = fields.Integer(string="Secuencia", default=1)

    @api.onchange("ind_global")
    def _onchange_ind_global(self):
        if self.ind_global:
            self.folio = "0"

    def _dte_referenceable_models(self):
        return ['account.move', 'account.move.boleta_voucher']

    def _dte_referencia_parent(self):
        raise NotImplementedError(
            f"{self._name} debe implementar _dte_referencia_parent()"
        )

    def _buscar_documento_referenciado(self):
        self.ensure_one()
        if not self.ref_id or not self.ref_id.dte:
            return False
        if not self.folio:
            return False
        try:
            doc_number = int(self.folio)
        except (ValueError, TypeError):
            return False
        parent = self._dte_referencia_parent()
        domain = [
            ('sii_document_number', '=', doc_number),
            ('document_class_id', '=', self.ref_id.id),
            ('company_id', '=', parent.company_id.id),
        ]
        for model_name in self._dte_referenceable_models():
            rec = self.env[model_name].search(domain, limit=1)
            if rec:
                return rec
        return False
