import logging
from datetime import date, datetime
import pytz
from collections import defaultdict
from odoo import api, fields, models, tools, Command
from odoo.exceptions import UserError
from odoo.tools.translate import _
from odoo.tools import SQL, frozendict
from odoo.tools.misc import formatLang
from contextlib import ExitStack, contextmanager

_logger = logging.getLogger(__name__)


try:
    from facturacion_electronica import facturacion_electronica as fe
except Exception as e:
    _logger.warning("Problema al cargar Facturación electrónica: %s" % str(e))
try:
    from io import BytesIO
except ImportError:
    _logger.warning("no se ha cargado io")
try:
    import base64
except ImportError:
    _logger.warning("Cannot import base64 library")
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    _logger.warning("no se ha cargado PIL")


tz_stgo = pytz.timezone("America/Santiago")


class Referencias(models.Model):
    _name = "account.move.referencias"
    _inherit = "l10n_cl_fe.dte.referencia.mixin"
    _description = "Línea de referencia de Documentos DTE"

    move_id = fields.Many2one("account.move", ondelete="cascade", index=True, copy=False, string="Documento")

    def _dte_referencia_parent(self):
        return self.move_id


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "l10n_cl_fe.dte.mixin", "l10n_cl_fe.dte.transporte.mixin"]

    _dte_cola_field = 'document_ids'
    _dte_xml_envio_field = 'move_ids'

    @api.onchange("use_documents")
    def get_dc_ids(self):
        for r in self:
            r.document_class_ids = self.env['sii.document_class']
            if not r.is_invoice():
                r.journal_document_class_id = False
                r.document_class_id = False
                continue
            dc_type = ["invoice", "invoice_in"]
            if r.use_documents and r.move_type == "in_invoice":
                dc_type = ["invoice_in"]
            elif r.move_type in ['in_refund', 'out_refund']:
                dc_type = ["credit_note", "debit_note"]
            if r.use_documents and not r.journal_document_class_id:
                dominio = [("journal_id", "=", r.journal_id.id), ("sii_document_class_id.document_type", "in", dc_type)]
                if r.document_class_id:
                    dominio.append(("sii_document_class_id", "=", r.document_class_id.id))
                r.journal_document_class_id = self.env["account.journal.sii_document_class"].search(dominio, limit=1)
                if not r.document_class_id:
                    r.document_class_id = r.journal_document_class_id.sii_document_class_id
            if not r.use_documents and r.move_type in ["in_invoice", "in_refund"]:
                for dc in r.journal_id.document_class_ids:
                    if dc.document_type in dc_type:
                        r.document_class_ids += dc
            else:
                jdc_ids = self.env["account.journal.sii_document_class"].search(
                    [("journal_id", "=", r.journal_id.id),
                     ("sii_document_class_id.document_type", "in", dc_type),]
                )
                for dc in jdc_ids:
                    r.document_class_ids += dc.sii_document_class_id

    document_class_ids = fields.Many2many(
        "sii.document_class", compute="get_dc_ids", string="Available Document Classes",
    )
    journal_document_class_id = fields.Many2one(
        "account.journal.sii_document_class",
        string="Documents Type",
        readonly=True,
        check_company=True,
    )
    iva_uso_comun = fields.Boolean(string="Iva Uso Común", readonly=True, )
    no_rec_code = fields.Selection(
        [
            ("1", "Compras destinadas a IVA a generar operaciones no gravados o exentas."),
            ("2", "Facturas de proveedores registrados fuera de plazo."),
            ("3", "Gastos rechazados."),
            ("4", "Entregas gratuitas (premios, bonificaciones, etc.) recibidos."),
            ("9", "Otros."),
        ],
        string="Código No recuperable",
        readonly=True,

    )  # @TODO select 1 automático si es emisor 2Categoría
    referencias = fields.One2many(
        "account.move.referencias", "move_id", readonly=True,
    )
    forma_pago = fields.Selection(
        [("1", "Contado"), ("2", "Crédito"), ("3", "Gratuito")],
        string="Forma de pago",
        readonly=True,

        default="1",
    )
    contact_id = fields.Many2one("res.partner", string="Contacto",)
    estado_recep_dte = fields.Selection(
        [("recibido", "Recibido en DTE"), ("mercaderias", "Recibido mercaderias"), ("validate", "Validada Comercial")],
        string="Estado de Recepcion del Envio",
        default="recibido",
        copy=False,
    )
    estado_recep_glosa = fields.Char(string="Información Adicional del Estado de Recepción", copy=False,)
    ticket = fields.Boolean(
        string="Formato Ticket", default=False, readonly=True,
    )
    claim = fields.Selection(
        [
            ("ACD", "Acepta Contenido del Documento"),
            ("RCD", "Reclamo al  Contenido del Documento "),
            ("ERM", " Otorga  Recibo  de  Mercaderías  o Servicios"),
            ("RFP", "Reclamo por Falta Parcial de Mercaderías"),
            ("RFT", "Reclamo por Falta Total de Mercaderías"),
            ("PAG", "DTE Pagado al Contado"),
            ("ENC", "Recepción de NC, distinta de anulación, que referencia al documento."),
            ("NCA", "Recepción de NC de anulación que referencia al documento."),
        ],
        string="Reclamo",
        copy=False,
    )
    claim_description = fields.Char(string="Detalle Reclamo", readonly=True,)
    activity_description = fields.Many2one(
        "sii.activity.description", string="Giro", related="commercial_partner_id.activity_description", readonly=True,
    )
    amount_untaxed_global_discount = fields.Float(
        string="Global Discount Amount", default=0.00,
        readonly=True,

    )
    amount_untaxed_global_recargo = fields.Float(
        string="Global Recargo Amount", default=0.00,
        readonly=True,

    )
    global_descuentos_recargos = fields.One2many(
        "account.move.gdr",
        "move_id",
        string="Descuentos / Recargos globales",
        readonly=True,

    )
    acteco_ids = fields.Many2many(
        "partner.activities", related="commercial_partner_id.acteco_ids", string="Partner Activities"
    )
    acteco_id = fields.Many2one(
        "partner.activities", string="Partner Activity", readonly=True,
    )
    respuesta_ids = fields.Many2many("sii.respuesta.cliente", string="Recepción del Cliente", readonly=True,)
    ind_servicio = fields.Selection(
        [
            ('1', "1.- Factura de servicios periódicos domiciliarios 2"),
            ('2', "2.- Factura de otros servicios periódicos"),
            (
                '3',
                "3.- Factura de Servicios. (en caso de Factura de Exportación: Servicios calificados como tal por Aduana)",
            ),
            ('4', "4.- Servicios de Hotelería"),
            ('5', "5.- Servicio de Transporte Terrestre Internacional"),
        ]
    )
    claim_ids = fields.One2many("sii.dte.claim", "move_id", string="Historial de Reclamos")
    amount_tax_retencion = fields.Monetary(string='Monto Retención', store=True, readonly=True,
        compute='_compute_amount',
        inverse='_inverse_amount_total',
    )
    amount_tax_retencion_signed = fields.Monetary(string='Monto Retención con signo', store=True, readonly=True,
        compute='_compute_amount',
        inverse='_inverse_amount_total',
    )
    sequence_number_next = fields.Integer(
        compute='_get_sequence_number_next'
    )
    sequence_number_next_prefix = fields.Integer(
        compute='_get_sequence_prefix'
    )
    comision_ids = fields.One2many(
        'account.move.comision',
        'move_id',
        string='Comisiones'
    )

    @api.depends(
        'line_ids.matched_debit_ids.debit_move_id.move_id.origin_payment_id.is_matched',
        'line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.amount_residual',
        'line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.amount_residual_currency',
        'line_ids.matched_credit_ids.credit_move_id.move_id.origin_payment_id.is_matched',
        'line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.amount_residual',
        'line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.amount_residual_currency',
        'line_ids.balance',
        'line_ids.currency_id',
        'line_ids.amount_currency',
        'line_ids.amount_residual',
        'line_ids.amount_residual_currency',
        'line_ids.payment_id.state',
        'line_ids.full_reconcile_id',
        'tax_totals',
        'state',
        'global_descuentos_recargos.amount_untaxed')
    def _compute_amount(self):
        # Copia de account.move._compute_amount de Odoo 20 con lo propio de Chile: las líneas de
        # descuento/recargo global y comisiones ('D', 'R', 'C') suman al neto, y las líneas de
        # impuesto de retención ('R') o anticipadas ('A') se separan en amount_tax_retencion.
        self.line_ids.fetch([
            'debit',
            'balance',
            'amount_currency',
            'amount_residual',
            'amount_residual_currency',
            'display_type',
            'tax_repartition_line_id'
        ])
        for move in self:
            total_untaxed, total_untaxed_currency = 0.0, 0.0
            total_tax, total_tax_currency = 0.0, 0.0
            total_residual, total_residual_currency = 0.0, 0.0
            total_reconciled, total_reconciled_currency = 0.0, 0.0
            total, total_currency = 0.0, 0.0
            total_retencion, total_retencion_currency = 0.0, 0.0
            sign = move.direction_sign
            for line in move.line_ids:
                if move.is_invoice(True):
                    # === Invoices ===
                    if line.display_type in ('tax', 'non_deductible_tax') or (line.display_type == 'rounding' and line.tax_repartition_line_id):
                        # Tax amount.
                        total_tax += line.balance
                        total_tax_currency += line.amount_currency
                        total += line.balance
                        total_currency += line.amount_currency
                        if line.tax_repartition_line_id.sii_type == 'R':
                            total_retencion -= line.balance
                            total_retencion_currency -= line.amount_currency
                            total_tax -= line.balance
                            total_tax_currency -= line.amount_currency
                        elif line.tax_repartition_line_id.sii_type == 'A':
                            total_retencion += line.balance
                            total_retencion_currency += line.amount_currency
                            if line.tax_repartition_line_id.credec:
                                total_tax -= line.balance
                                total_tax_currency -= line.amount_currency
                            total -= (sign * line.balance)
                            total_currency -= (sign * line.amount_currency)
                    elif line.display_type in ('product', 'rounding', 'non_deductible_product', 'non_deductible_product_total', 'C', 'D', 'R'):
                        # Untaxed amount.
                        total_untaxed += line.balance
                        total_untaxed_currency += line.amount_currency
                        total += line.balance
                        total_currency += line.amount_currency
                    elif line.display_type == 'payment_term':
                        # Residual and reconciled amounts.
                        total_residual += line.amount_residual
                        total_residual_currency += line.amount_residual_currency
                        total_reconciled += line.balance - line.amount_residual
                        total_reconciled_currency += line.amount_currency - line.amount_residual_currency
                else:
                    # === Miscellaneous journal entry ===
                    if line.debit:
                        total += line.balance
                        total_currency += line.amount_currency

            move.amount_untaxed = sign * total_untaxed_currency
            move.amount_tax = sign * total_tax_currency
            move.amount_total = sign * total_currency
            move.amount_untaxed_signed = -total_untaxed
            move.amount_untaxed_in_currency_signed = -total_untaxed_currency
            move.amount_tax_signed = -total_tax
            move.amount_total_signed = abs(total) if move.move_type == 'entry' else -total
            move.amount_total_in_currency_signed = abs(move.amount_total) if move.move_type == 'entry' else -(sign * move.amount_total)
            move.amount_tax_retencion = sign * total_retencion_currency
            move.amount_tax_retencion_signed = -total_retencion
            if move.state == 'draft':
                # The payment term lines are only synchronized when saving: follow the lines being edited.
                tax_totals = move.tax_totals or {}
                move.amount_residual = tax_totals.get('total_amount_currency', 0.0) + sign * total_reconciled_currency
                move.amount_residual_signed = -sign * tax_totals.get('total_amount', 0.0) - total_reconciled
            else:
                move.amount_residual = -sign * total_residual_currency
                move.amount_residual_signed = total_residual

    @api.depends('invoice_payment_term_id', 'invoice_date', 'currency_id', 'amount_total_in_currency_signed', 'invoice_date_due')
    def _compute_needed_terms(self):
        # Copia de Odoo 20: las cuotas se calculan sobre el impuesto menos la retención (líneas 'R'/'A').
        AccountTax = self.env['account.tax']
        for invoice in self:
            is_draft = invoice.id != invoice._origin.id
            needed_terms = {}
            invoice.needed_terms_dirty = True
            sign = 1 if invoice.is_inbound(include_receipts=True) else -1
            if invoice.is_invoice(True) and invoice.invoice_line_ids:
                if invoice.invoice_payment_term_id:
                    if is_draft:
                        tax_amount_currency = 0.0
                        tax_amount = tax_amount_currency
                        untaxed_amount_currency = 0.0
                        untaxed_amount = untaxed_amount_currency
                        sign = invoice.direction_sign
                        base_lines, _tax_lines = invoice._get_rounded_base_and_tax_lines(round_from_tax_lines=False)
                        AccountTax._add_accounting_data_in_base_lines_tax_details(base_lines, invoice.company_id, include_caba_tags=invoice.always_tax_exigible)
                        tax_results = AccountTax._prepare_tax_lines(base_lines, invoice.company_id)
                        for _base_line, to_update in tax_results['base_lines_to_update']:
                            untaxed_amount_currency += sign * to_update['amount_currency']
                            untaxed_amount += sign * to_update['balance']
                        for tax_line_vals in tax_results['tax_lines_to_add']:
                            tax_amount_currency += sign * tax_line_vals['amount_currency']
                            tax_amount += sign * tax_line_vals['balance']
                    else:
                        tax_amount_currency = (invoice.amount_tax - invoice.amount_tax_retencion) * sign
                        tax_amount = invoice.amount_tax_signed - invoice.amount_tax_retencion_signed
                        untaxed_amount_currency = invoice.amount_untaxed * sign
                        untaxed_amount = invoice.amount_untaxed_signed
                    invoice_payment_terms = invoice.invoice_payment_term_id._compute_terms(
                        date_ref=invoice.invoice_date or invoice.date or fields.Date.context_today(invoice),
                        currency=invoice.currency_id or invoice.journal_id.currency_id or invoice.company_currency_id,
                        tax_amount_currency=tax_amount_currency,
                        tax_amount=tax_amount,
                        untaxed_amount_currency=untaxed_amount_currency,
                        untaxed_amount=untaxed_amount,
                        company=invoice.company_id,
                        cash_rounding=invoice.invoice_cash_rounding_id,
                        sign=sign
                    )
                    for term_line in invoice_payment_terms['line_ids']:
                        key = frozendict({
                            'move_id': invoice.id,
                            'date_maturity': fields.Date.to_date(term_line.get('date')),
                            'discount_date': invoice_payment_terms.get('discount_date'),
                        })
                        values = {
                            'balance': term_line['company_amount'],
                            'amount_currency': term_line['foreign_amount'],
                            'discount_date': invoice_payment_terms.get('discount_date'),
                            'discount_balance': invoice_payment_terms.get('discount_balance') or 0.0,
                            'discount_amount_currency': invoice_payment_terms.get('discount_amount_currency') or 0.0,
                        }
                        if key not in needed_terms:
                            needed_terms[key] = values
                        else:
                            needed_terms[key]['balance'] += values['balance']
                            needed_terms[key]['amount_currency'] += values['amount_currency']
                else:
                    needed_terms[frozendict({
                        'move_id': invoice.id,
                        'date_maturity': fields.Date.to_date(invoice.invoice_date_due),
                        'discount_date': False,
                        'discount_balance': 0.0,
                        'discount_amount_currency': 0.0
                    })] = {
                        'balance': invoice.amount_total_signed,
                        'amount_currency': invoice.amount_total_in_currency_signed,
                    }
            invoice.needed_terms = list(needed_terms.items())

    def _get_last_sequence_domain(self, relaxed=False):
        # Odoo 18+ devuelve una condición SQL (antes una tupla (where_string, param)). La de Odoo
        # (diario, período, rectificativas, pagos) más el tipo de documento del SII: cada tipo
        # (33, 34, 56, 61...) lleva su propia numeración en el mismo diario.
        condition = super()._get_last_sequence_domain(relaxed)
        if not self.journal_id.use_documents:
            return condition
        if self.use_documents and self.document_class_id.dte:
            return SQL("%s AND use_documents AND document_class_id = %s", condition, self.document_class_id.id)
        return SQL("%s AND (use_documents = FALSE OR use_documents IS NULL)", condition)

    def _set_next_sequence(self):
        self.ensure_one()
        if self.use_documents and self.journal_document_class_id.sequence_id and not self.document_class_id.es_voucher():
            if not (self.journal_id.restore_mode or self.env.context.get("restore_mode", False)):
                sequence = self.journal_document_class_id.sequence_id
                if not self._get_last_sequence():
                    sequence = sequence.with_context(force_number_next=True)
                self.sii_document_number = sequence.next_by_id()
            self[self._sequence_field] = '%s%s' % (self.document_class_id.doc_code_prefix, self.sii_document_number)
        else:
            self.use_documents = False
            super(AccountMove, self)._set_next_sequence()

    def _consolidate_boleta_taxes_to_iva(self):
        self.ensure_one()
        if not self.is_sale_document():
            return
        if not (self.es_boleta() or self.es_nc_boleta()):
            return
        extra_tax_lines = self.line_ids.filtered(
            lambda l: l.tax_line_id and not l.tax_line_id.es_iva()
        )
        if not extra_tax_lines:
            return
        iva_tax_lines = self.line_ids.filtered(
            lambda l: l.tax_line_id and l.tax_line_id.es_iva()
        )
        if not iva_tax_lines:
            return
        iva_rate = iva_tax_lines[0].tax_line_id.amount or 19.0
        iva_factor = 1 + iva_rate / 100.0
        ctx = {"check_move_validity": False, "skip_invoice_sync": True, "dynamic_unlink": True}
        currency = self.currency_id or self.company_id.currency_id
        total_iva_signed = 0.0
        any_changed = False
        for base in self.invoice_line_ids:
            has_iva = any(t.es_iva() for t in base.tax_ids)
            if not has_iva:
                continue
            if not base.balance:
                continue
            sign = -1 if base.balance < 0 else 1
            has_extra = any(not t.es_iva() for t in base.tax_ids)
            if has_extra:
                bruto = currency.round(base.price_total)
                neto = currency.round(bruto / iva_factor)
                iva = bruto - neto
                base.with_context(**ctx).write({
                    "balance": sign * neto,
                    "amount_currency": sign * neto,
                    "price_subtotal": neto,
                    "price_total": bruto,
                })
                any_changed = True
            else:
                iva = currency.round(base.price_total - base.price_subtotal)
            total_iva_signed += sign * iva
        if not any_changed:
            return
        iva_line = iva_tax_lines[0]
        iva_amount = currency.round(total_iva_signed)
        iva_line.with_context(**ctx).write({
            "balance": iva_amount,
            "amount_currency": iva_amount,
        })
        if len(iva_tax_lines) > 1:
            iva_tax_lines[1:].with_context(**ctx).unlink()
        extra_tax_lines.with_context(**ctx).unlink()

    def _lines_total(self):
        """Devuelve (total_lineas, limite_dte) según _line_size() y la cantidad
        de referencias del documento (cada referencia >2 descuenta una línea)."""
        total_lines = 0
        for l in self.invoice_line_ids:
            total_lines += l._line_size()
        dte_lines = self.company_id.dte_lines or 60
        if self.referencias:
            dte_lines -= max(0, len(self.referencias) - 2)
        return total_lines, dte_lines

    def _apply_line_limit(self):
        """Para dte_lines_action='create' divide el documento en varios cuando
        excede el límite del SII; para 'stop' lanza UserError. Devuelve el
        recordset de moves nuevos creados (para que el caller los una a self
        antes de hacer super()._post)."""
        originales = to_verify = self
        original = False
        total = 0
        extra = self.env['account.move']
        while to_verify:
            r = to_verify[0]
            to_verify -= r
            if r.ticket or not r.use_documents:
                continue
            dte_action = r.company_id.dte_lines_action
            if dte_action not in ('create', 'stop'):
                continue
            if r in originales:
                original = r
            total_lines, dte_lines = r._lines_total()
            if total_lines <= dte_lines:
                if total:
                    total = 0
                continue
            if dte_action == 'stop':
                raise UserError(_("Se está creando una cantidad de líneas mayor a la permitida"))
            if original == r:
                total = r.amount_total
            else:
                total += r.amount_total
            current_lines = 0
            new = r.copy()
            new.invoice_date = r.invoice_date
            new.invoice_date_due = r.invoice_date_due
            new.date = r.date
            j = 0
            for il in r.invoice_line_ids:
                current_lines += il._line_size()
                if current_lines > dte_lines:
                    if hasattr(il, 'sale_line_ids') and il.sale_line_ids:
                        new.invoice_line_ids[j].sale_line_ids += il.sale_line_ids
                    il.unlink()
                j += 1
            i = 1
            current_lines = 0
            for nil in new.invoice_line_ids:
                current_lines += nil._line_size()
                if current_lines <= dte_lines:
                    nil.unlink()
                    continue
                nil.sequence = i
                i += 1
            extra += new
            to_verify += new
            if r != original:
                total -= (r.amount_total + new.amount_total)
        return extra

    def _post(self, soft=True):
        for move in self:
            move._consolidate_boleta_taxes_to_iva()
        if soft:
            future_moves = self.filtered(lambda move: move.date > fields.Date.context_today(self))
            candidates = self - future_moves
        else:
            candidates = self
        candidates = candidates.filtered(
            lambda m: m.company_id.dte_lines_action in ('create', 'stop')
            and not m.ticket and m.use_documents
        )
        if candidates:
            extra = candidates._apply_line_limit()
            if extra:
                self = self + extra
        to_post = super(AccountMove, self)._post(soft=soft)
        invoices_to_queue = self.env['account.move']
        for inv in to_post:
            if inv.document_class_id and inv.document_class_id.es_voucher():
                inv.use_documents = False
            if not inv.is_invoice() or not inv.journal_document_class_id.sequence_id or not inv.use_documents or inv.state != 'posted':
                continue
            if not inv.sii_document_number:
                inv._set_next_sequence()
            inv.sii_result = "NoEnviado"
            if inv.journal_id.restore_mode or self.env.context.get("restore_mode", False):
                inv.sii_result = "Proceso"
            else:
                inv._validaciones_uso_dte()
                inv._timbrar()
                invoices_to_queue |= inv
            po = self.env['purchase.order'].browse(
                inv.env.context.get('purchase_to_done', False))
            if po:
                po.write({"state": "done"})
        invoices_to_queue.queue()
        return to_post

    @contextmanager
    def _sync_gdr_lines(self, container):
        yield
        for invoice in container['records']:
            invoice._recompute_global_gdr_lines()

    @contextmanager
    def _sync_comisiones_lines(self, container):
        yield
        for invoice in container['records']:
            invoice._recompute_comisiones_lines()

    @api.onchange('journal_id')
    def _onchange_journal_id(self):
        super(AccountMove, self)._onchange_journal_id()
        if self.move_type in ('in_invoice', 'in_refund'):
            self.use_documents = False
        else:
            self.use_documents = bool(self.journal_id.document_class_ids)
        if self.is_invoice():
            self.get_dc_ids()

    def _get_move_imps(self):
        imps = {}
        for l in self.line_ids:
            if l.tax_line_id:
                if l.tax_line_id:
                    if l.tax_line_id.id not in imps:
                        imps[l.tax_line_id.id] = {
                            "tax_id": l.tax_line_id.id,
                            "credit": 0,
                            "debit": 0,
                            "code": l.tax_line_id.sii_code,
                        }
                    imps[l.tax_line_id.id]["credit"] += l.credit
                    imps[l.tax_line_id.id]["debit"] += l.debit
            elif l.tax_ids and l.tax_ids[0].amount == 0:  # caso monto exento
                if not l.tax_ids[0].id in imps:
                    imps[l.tax_ids[0].id] = {
                        "tax_id": l.tax_ids[0].id,
                        "credit": 0,
                        "debit": 0,
                        "code": l.tax_ids[0].sii_code,
                    }
                imps[l.tax_ids[0].id]["credit"] += l.credit
                imps[l.tax_ids[0].id]["debit"] += l.debit
        return imps

    def totales_por_movimiento(self):
        move_imps = self._get_move_imps()
        imps = {
            "iva": 0,
            "exento": 0,
            "otros_imps": 0,
        }
        for _key, i in move_imps.items():
            if i["code"] in [14]:
                imps["iva"] += i["credit"] or i["debit"]
            elif i["code"] == 0:
                imps["exento"] += i["credit"] or i["debit"]
            else:
                imps["otros_imps"] += i["credit"] or i["debit"]
        imps["neto"] = self.amount_total - imps["otros_imps"] - imps["exento"] - imps["iva"]
        return imps

    @api.onchange("invoice_line_ids", "journal_document_class_id")
    def _onchange_invoice_line_ids(self):
        i = 0
        for l in self.invoice_line_ids:
            i += 1
            if l.sequence == -1 or l.sequence == 0:
                l.sequence = i
        if (
            not self.ticket
            and self.company_id.dte_lines_action == 'stop'
        ):
            total_lines, dte_lines = self._lines_total()
            if total_lines > dte_lines:
                raise UserError("Se está creando una cantidad de líneas de detalle mayor a la permitida")

    @api.depends("state", "journal_id", "invoice_date", "journal_document_class_id", "use_documents")
    def _get_sequence_prefix(self):
        for invoice in self:
            invoice.sequence_number_next_prefix = ''
            if invoice.journal_document_class_id:
                invoice.sequence_number_next_prefix = invoice.document_class_id.doc_code_prefix or ""

    @api.depends("state", "journal_id", "journal_document_class_id")
    def _get_sequence_number_next(self):
        for invoice in self:
            invoice.sequence_number_next = -1
            if invoice.journal_document_class_id and invoice.journal_document_class_id.sequence_id:
                invoice.sequence_number_next = invoice.journal_document_class_id.sequence_id.number_next_actual

    def _get_sync_stack(self, container):
        # Odoo 20 arma la sincronización de líneas dinámicas en _get_sync_stack: se agregan las
        # de descuentos/recargos globales y comisiones entre los impuestos (50) y el epd (70).
        stack, update_containers = super()._get_sync_stack(container)
        _tax_container, invoice_container, _misc_container = update_containers()
        stack += [
            (55, self._sync_gdr_lines(invoice_container)),
            (56, self._sync_comisiones_lines(invoice_container)),
        ]
        return stack, update_containers

    def _prepare_gdr_base_line_for_taxes_computation(self, line):
        """Línea de descuento/recargo global ('D'/'R') o comisión ('C') como base line del motor
        de impuestos de Odoo 20 (igual que las líneas de redondeo: monto ya sin impuestos)."""
        self.ensure_one()
        sign = self.direction_sign
        return self.env['account.tax']._prepare_base_line_for_taxes_computation(
            line,
            price_unit=sign * line.amount_currency,
            quantity=1.0,
            sign=sign,
            special_mode='total_excluded',
            is_refund=self.is_refund(),
            rate=self.invoice_currency_rate,
        )

    def _get_rounded_base_and_tax_lines(self, round_from_tax_lines=True):
        base_lines, tax_lines = super()._get_rounded_base_and_tax_lines(round_from_tax_lines=round_from_tax_lines)
        gdr_amls = self.line_ids.filtered(lambda line: line.display_type in ('C', 'D', 'R')) if self.id else False
        if gdr_amls:
            AccountTax = self.env['account.tax']
            gdr_lines = [self._prepare_gdr_base_line_for_taxes_computation(line) for line in gdr_amls]
            AccountTax._add_tax_details_in_base_lines(gdr_lines, self.company_id)
            base_lines += gdr_lines
            AccountTax._round_base_lines_tax_details(base_lines, self.company_id, tax_lines=tax_lines if round_from_tax_lines else [])
        return base_lines, tax_lines

    def copy(self, default=None):
        copied_am = super().copy(default)
        if default and default.get("referencias"):
            if default["referencias"][0][2].get('cod_ref') == '2':
                copied_am.invoice_line_ids.unlink()
                prod = self.env['product.product'].search(
                        [
                                ('product_tmpl_id', '=', self.env.ref('l10n_cl_fe.no_product').id),
                        ]
                    )
                copied_am.write({'invoice_line_ids': [
                    Command.create({
                        'product_id': prod.id,
                        'name': prod.name,
                        'quantity': 1,
                        'price_unit': 0
                    })
                ]})
        return copied_am

    def _reverse_moves(self, default_values_list=None, cancel=False):
        ''' Reverse a recordset of account.move.
        If cancel parameter is true, the reconcilable or liquidity lines
        of each original move will be reconciled with its reverse's.

        :param default_values_list: A list of default values to consider per move.
                                    ('type' & 'reversed_entry_id' are computed in the method).
        :return:                    An account.move recordset, reverse of the current self.
        '''
        if not default_values_list:
            default_values_list = [{} for move in self]

        if cancel:
            lines = self.mapped('line_ids')
            # Avoid maximum recursion depth.
            if lines:
                lines.remove_move_reconcile()

        TYPE_REVERSE_MAP = {
            'entry': 'entry',
            'out_invoice': 'out_refund',
            'out_refund': 'entry',
            'in_invoice': 'in_refund',
            'in_refund': 'entry',
            'out_receipt': 'entry',
            'in_receipt': 'entry',
        }
        reverse_moves = self.env['account.move']
        for move, default_values in zip(self, default_values_list):
            move_type = move.move_type
            refund_type = TYPE_REVERSE_MAP[move_type]
            if move.document_class_id and default_values.get('document_class_id'):
                dc = self.env['sii.document_class'].sudo().browse(default_values['document_class_id'])
                if move_type == 'out_invoice' and dc.document_type == "credit_note":
                    refund_type = 'out_refund'
                elif move_type in ['out_invoice', 'out_refund']:
                    refund_type = 'out_invoice'
                elif move_type == 'in_invoice' and dc.document_type == "credit_note":
                    refund_type = 'in_refund'
                else:
                    refund_type = 'in_invoice'
            default_values.update({
                'move_type': refund_type,
                'reversed_entry_id': move.id,
            })
            reverse_moves += move.with_context(
                move_reverse_cancel=cancel,
                include_business_fields=True,
                skip_invoice_sync=bool(move.tax_cash_basis_origin_move_id),
            ).copy(default_values)
        reverse_moves.with_context(skip_invoice_sync=cancel).write({'line_ids': [
            Command.update(line.id, {
                'balance': -line.balance,
                'amount_currency': -line.amount_currency,
            })
            for line in reverse_moves.line_ids
            if line.move_id.move_type == 'entry' or line.display_type == 'cogs'
        ]})

        # Reconcile moves together to cancel the previous one.
        if cancel:
            reverse_moves.with_context(move_reverse_cancel=cancel)._post(soft=False)
            for move, reverse_move in zip(self, reverse_moves):
                group = defaultdict(list)
                for line in (move.line_ids + reverse_move.line_ids).filtered(lambda l: not l.reconciled):
                    group[(line.account_id, line.currency_id)].append(line.id)
                for (account, dummy), line_ids in group.items():
                    if account.reconcile or account.account_type in ('asset_cash', 'liability_credit_card'):
                        self.env['account.move.line'].browse(line_ids).with_context(move_reverse_cancel=cancel).reconcile()

        return reverse_moves

    @api.onchange("invoice_payment_term_id")
    def _onchange_payment_term(self):
        if self.invoice_payment_term_id and self.invoice_payment_term_id.dte_sii_code:
            self.forma_pago = self.invoice_payment_term_id.dte_sii_code

    def button_cancel(self):
        for r in self:
            if r.sii_xml_request and r.sii_result not in [False, "draft", "NoEnviado", "Anulado"]:
                raise UserError(_("You can not cancel a valid document on SII"))
        return super().button_cancel()


    def unlink(self):
        to_unlink = self.env['account.move']
        for r in self:
            if r.sii_xml_request and r.sii_result in ["Aceptado", "Reparo", "Rechazado"]:
                raise UserError(_("You can not delete a valid document on SII"))
            to_unlink += r
        return super(AccountMove, to_unlink).unlink()

    @api.constrains("ref", "partner_id", "company_id", "move_type", "journal_document_class_id")
    def _check_reference_in_invoice(self):
        moves = self.filtered(lambda move: move.is_purchase_document() and move.ref)
        for r in moves:
            if r.move_type in ["in_invoice", "in_refund"] and r.sii_document_number:
                domain = [
                    ("move_type", "=", r.move_type),
                    ("sii_document_number", "=", r.sii_document_number),
                    ("partner_id", "=", r.partner_id.id),
                    ("journal_document_class_id.sii_document_class_id", "=", r.document_class_id.id),
                    ("company_id", "=", r.company_id.id),
                    ("id", "!=", r.id),
                    ("state", "!=", "cancel"),
                ]
                move_ids = r.search(domain)
                if move_ids:
                    raise UserError(
                        u"El numero de factura debe ser unico por Proveedor.\n"
                        u"Ya existe otro documento con el numero: %s para el proveedor: %s"
                        % (r.sii_document_number, r.partner_id.display_name)
                    )

    @api.onchange("journal_document_class_id")
    def set_document_class_id(self):
        for r in self:
            r.document_class_id = r.journal_document_class_id.sii_document_class_id

    def _dte_compania_emisora(self):
        return self.company_id.parent_id or self.company_id

    def _validar_requisitos_emisor(self, company=None):
        """Datos de la compañía que exige el DTE (RUT, giro, resolución del SII): UserError claro
        antes de armar y firmar el XML (sin fecha de resolución, _emisor fallaba con
        AttributeError)."""
        company = company or self._dte_compania_emisora()
        faltan = []
        if company.partner_id.rut() == "66666666-6":
            faltan.append(_("RUT"))
        if not company.activity_description:
            faltan.append(_("giro (glosa de la actividad)"))
        if not company.dte_resolution_number:
            faltan.append(_("número de resolución del SII"))
        if not company.dte_resolution_date:
            faltan.append(_("fecha de resolución del SII"))
        if faltan:
            raise UserError(_(
                "No se puede emitir el documento %(doc)s: a la compañía %(company)s le falta %(faltan)s."
            ) % {"doc": self.display_name, "company": company.name, "faltan": ", ".join(faltan)})

    def _validaciones_uso_dte(self):
        self._validar_requisitos_emisor()
        super()._validaciones_uso_dte()
        if self.move_type in ["out_refund", "in_refund"] and not self.es_nc():
            raise UserError(_(
                "El tipo de documento %s, no es de tipo Rectificativo"
            ) % self.document_class_id.name)
        if self.move_type in ["out_invoice", "in_invoice"] and self.es_nc():
            raise UserError(_(
                "El tipo de documento %s, no es de tipo Documento"
            ) % self.document_class_id.name)
        for gd in self.global_descuentos_recargos:
            if gd.valor <= 0:
                raise UserError(_(
                    "No puede ir una línea igual o menor que 0, elimine la línea o verifique el valor ingresado"
                ))
        if self.company_id.tax_calculation_rounding_method != "round_globally":
            raise UserError(_("El método de redondeo debe ser Estríctamente Global"))

    def _recompute_global_gdr_lines(self):
        self.ensure_one()
        if self.state != 'draft':
            return
        def _apply_global_gdr(self, amount, amount_currency, global_gdr_line, gdr, taxes):
            if gdr.type == 'D':
                amount_currency *= -1
            gdr_line_vals = {
                'quantity': 1,
                'balance': amount_currency,
                'amount_currency': amount_currency,
                'partner_id': self.partner_id.id,
                'move_id': self.id,
                'currency_id': self.currency_id.id,
                'company_id': self.company_id.id,
                'company_currency_id': self.company_id.currency_id.id,
                'display_type': gdr.type,
                'name': gdr.name,
                'account_id': gdr.account_id.id,
                'tax_ids': [Command.set(taxes.ids)],
            }
            # Create or update the global gdr line.
            if global_gdr_line:
                global_gdr_line.write(gdr_line_vals)
            else:
                global_gdr_line = self.env['account.move.line'].create(
                    gdr_line_vals)
        total_gd = 0
        total_gr = 0
        total_gd_taxed = 0
        total_gr_taxed = 0
        gds = self.line_ids.filtered(lambda l: l.display_type=='D')
        grs = self.line_ids.filtered(lambda l: l.display_type=='R')

        for gdr in self.global_descuentos_recargos:
            if not gdr.account_id:
                continue
            gd = False
            taxes = self.env['account.tax']
            for line in self.invoice_line_ids:
                for t in line.tax_ids:
                    if gdr.impuesto == "afectos" and t.amount > 0 and t not in taxes:
                        taxes += t
                    elif gdr.impuesto != "afectos" and t not in taxes:
                        taxes += t
            for line in gds:
                if line.name == gdr.name:
                    gd = line
                    gds -= gd
            gdr_amount, gdr_amount_currency = gdr.amount_untaxed, gdr.amount_currency
            if self.currency_id.is_zero(gdr_amount):
                    continue
            if gdr.type=="D":
                total_gd += gdr_amount
                total_gd_taxed += gdr.amount
                _apply_global_gdr(self, gdr_amount, gdr_amount_currency, gd, gdr, taxes)
            gr = False
            for line in grs:
                if line.name == gdr.name:
                    gr = line
                    grs -= gr
            if gdr.type=="R":
                total_gr += gdr_amount
                total_gr_taxed += gdr.amount
                _apply_global_gdr(self, gdr_amount, gdr_amount_currency, gr, gdr, taxes)
        gds.unlink()
        grs.unlink()

    def _recompute_comisiones_lines(self):
        self.ensure_one()
        if self.state != 'draft':
            return
        def _apply_comision(self, name, amount, amount_currency, comision_line, comision, taxes):
            comision_line_vals = {
                'quantity': 1,
                'balance': amount,
                'partner_id': self.partner_id.id,
                'move_id': self.id,
                'currency_id': self.currency_id.id,
                'company_id': self.company_id.id,
                'company_currency_id': self.company_id.currency_id.id,
                'display_type': 'C',
                'name': name,
                'account_id': comision.account_id.id,
                'tax_ids': [Command.set(taxes.ids)],
            }
            # Create or update the comision line.
            if comision_line:
                comision_line.write(comision_line_vals)
            else:
                comision_line = self.env['account.move.line'].create(
                    comision_line_vals)
        comisiones = self.line_ids.filtered(lambda l: l.display_type=='C')
        exento = self.env['account.tax'].search([('amount', '=', 0), ('sii_code','=', 0), ('type_tax_use', '=', 'sale'), ('activo_fijo', '=', False) ], limit=1)
        for c in self.comision_ids:
            #if not c.account_id:
            #    continue
            comision_line = False
            name = c.name + '- Afecto'
            for line in comisiones:
                if line.name == name:
                    comision_line = line
                    comisiones -= comision_line

            _apply_comision(self, name, c.valor_neto_comision, c.valor_neto_comision_currency, comision_line, c, c.iva)
            if c.valor_exento_comision:
                comision_line = False
                name = c.name + '- Exento'
                for line in comisiones:
                    if line.name == name:
                        comision_line = line
                        comisiones -= comision_line
                _apply_comision(self, name, c.valor_exento_comision, c.valor_exento_comision_currency, comision_line, c, exento)
        comisiones.unlink()

    def time_stamp(self, formato="%Y-%m-%dT%H:%M:%S"):
        tz = pytz.timezone("America/Santiago")
        return datetime.now(tz_stgo).strftime(formato)

    def _create_attachment_respuesta_cliente(self, att):
        if not att or not self._dte_requires_response_persistence():
            return
        self.env["sii.respuesta.cliente"].create({
            "exchange_id": att.id,
            "type": "RecepcionEnvio",
            "recep_envio": "no_revisado",
        })


    def action_invoice_sent(self):
        result = super(AccountMove, self).action_invoice_sent()
        if self.sii_xml_dte:
            att = self._create_attachment()
            result["context"].update(
                {"default_attachment_ids": att.ids,}
            )
        return result


    def _xml_download_path(self):
        return "invoice"

    def get_xml_exchange_file(self):
        url_path = "/download/xml/invoice_exchange/%s" % (self.id)
        return {
            "type": "ir.actions.act_url",
            "url": url_path,
            "target": "self",
        }


    def format_vat(self, value, con_cero=False):
        ''' Se Elimina el 0 para prevenir problemas con el sii, ya que las muestras no las toma si va con
        el 0 , y tambien internamente se generan problemas, se mantiene el 0 delante, para cosultas, o sino retorna "error de datos"'''
        if not value or value == "" or value == 0:
            value = "CL666666666"
            # @TODO opción de crear código de cliente en vez de rut genérico
        rut = value[:10] + "-" + value[10:]
        if not con_cero:
            rut = rut.replace("CL0", "")
        rut = rut.replace("CL", "")
        return rut

    def get_related_invoices_data(self):
        """
        List related invoice information to fill CbtesAsoc.
        """
        self.ensure_one()
        rel_invoices = self.search(
            [("number", "=", self.origin), ("state", "not in", ["draft", "proforma", "proforma2", "cancel"])]
        )
        return rel_invoices

    def _dte_requires_response_persistence(self):
        self.ensure_one()
        return (
            self.forma_pago == '2'
            and bool(self.partner_id)
            and bool(self.partner_id.dte_email)
        )

    def action_convert_to_dte(self):
        return self.env["ir.actions.actions"]._for_xml_id("l10n_cl_fe.action_convert_to_dte")

    def _get_name_invoice_report(self):
        self.ensure_one()
        if self.document_class_id:
            if self.ticket:
                return 'l10n_cl_fe.report_ticket'
            return 'l10n_cl_fe.dte_external_layout'
        return super()._get_name_invoice_report()

    def _actecos_emisor(self):
        if not self.journal_id.journal_activities_ids:
            raise UserError("El Diario no tiene ACTECOS asignados")
        return [a.code for a in self.journal_id.journal_activities_ids]

    def _dte_fch_emis(self):
        return self.invoice_date.strftime("%Y-%m-%d")

    def _dte_tpo_impresion(self):
        return "T" if self.ticket and not self.es_boleta() else None

    def _dte_forma_pago(self):
        return self.forma_pago or 1

    def _id_doc(self, resumen):
        IdDoc = super()._id_doc(resumen)
        if not self.es_boleta() and self.invoice_date_due:
            IdDoc["FchVenc"] = self.invoice_date_due.strftime("%Y-%m-%d")
        if self.ind_servicio:
            IdDoc["IndServicio"] = self.ind_servicio
        return IdDoc

    def _emisor(self):
        Emisor = {}
        company = self.company_id.parent_id or self.company_id
        Emisor["RUTEmisor"] = company.partner_id.rut()
        if self.es_boleta():
            Emisor["RznSocEmisor"] = self._acortar_str(company.partner_id.name, 100)
            Emisor["GiroEmisor"] = self._acortar_str(company.activity_description.name, 80)
        else:
            Emisor["RznSoc"] = self._acortar_str(company.partner_id.name, 100)
            Emisor["GiroEmis"] = self._acortar_str(company.activity_description.name, 80)
            if company.phone:
                Emisor["Telefono"] = self._acortar_str(company.phone, 20)
            if company.dte_email_id:
                Emisor["CorreoEmisor"] = company.dte_email_id.display_name
            Emisor["Actecos"] = self._actecos_emisor()
        dir_origen = company
        if self.journal_id.sucursal_id:
            Emisor['Sucursal'] = self._acortar_str(self.journal_id.sucursal_id.partner_id.name, 20)
            Emisor["CdgSIISucur"] = self._acortar_str(self.journal_id.sucursal_id.sii_code, 9)
            dir_origen = self.journal_id.sucursal_id.partner_id
        Emisor['DirOrigen'] = self._acortar_str(dir_origen.street + ' ' + (dir_origen.street2 or ''), 70)
        if not dir_origen.city_id:
            raise UserError("Debe ingresar la Comuna de compañía emisora")
        Emisor['CmnaOrigen'] = dir_origen.city_id.name
        if not dir_origen.city:
            raise UserError("Debe ingresar la Ciudad de compañía emisora")
        Emisor["CiudadOrigen"] = company.city
        Emisor["Modo"] = "produccion" if company.dte_service_provider == "SII" else "certificacion"
        self._validar_requisitos_emisor(company)
        Emisor["NroResol"] = company.dte_resolution_number
        Emisor["FchResol"] = company.dte_resolution_date.strftime("%Y-%m-%d")
        Emisor["ValorIva"] = 19
        return Emisor

    def _dte_emisor_sucursal(self):
        suc = self.journal_id.sucursal_id
        if not suc:
            return None
        return {'name': suc.partner_id.name, 'sii_code': suc.sii_code, 'partner': suc.partner_id}

    def _dte_partner_recep(self):
        return self.commercial_partner_id or self.partner_id.commercial_partner_id

    def _dte_recep_giro(self, partner):
        return (
            self.acteco_id.name
            or (partner.activity_description.name if partner.activity_description else False)
            or (self.acteco_ids and self.acteco_ids[0].name)
            or False
        )

    def _dte_recep_correo(self, partner):
        return (
            partner.dte_email
            or self.partner_id.dte_email
            or partner.email
            or self.partner_id.email
            or False
        )

    def _dte_recep_telefono(self, partner):
        return self.partner_id.phone or partner.phone or self.partner_id.email or False

    def _dte_recep_validar_obligatorios(self, partner):
        return (
            not self.es_boleta()
            and not self.es_nc_boleta()
            and self.move_type not in ("in_invoice", "in_refund")
        )

    def _receptor(self):
        commercial = self._dte_partner_recep()
        Receptor = super()._receptor()
        if not self.partner_id or Receptor["RUTRecep"] == "66666666-6":
            return {k: Receptor[k] for k in ('RUTRecep', 'RznSocRecep') if k in Receptor}
        street_recep = self.partner_id.street or commercial.street or False
        street2_recep = self.partner_id.street2 or commercial.street2 or False
        if street_recep or street2_recep:
            Receptor["DirRecep"] = self._acortar_str(
                (street_recep or "") + (" " + street2_recep if street2_recep else ""), 70
            )
        cmna_recep = self.partner_id.city_id.name or commercial.city_id.name
        if cmna_recep:
            Receptor["CmnaRecep"] = cmna_recep
        ciudad_recep = self.partner_id.city or commercial.city
        if ciudad_recep:
            Receptor["CiudadRecep"] = ciudad_recep
        return Receptor

    def _comisiones(self):
        Comisiones = []
        sequence = 0
        for c in self.comision_ids:
            sequence = (c.sequence or sequence ) +1
            Comisiones.append({
                'NroLinCom': sequence,
                'TipoMovim': c.tipo_movimiento,
                'Glosa': c.name,
                'TasaComision': c.tasa_comision,
                'ValComNeto': self.currency_id.round(c.valor_neto_comision),
                'ValComExe': self.currency_id.round(c.valor_exento_comision),
                'ValComIVA': self.currency_id.round(c.valor_iva_comision),
            })
        return Comisiones

    def _totales_intermedio(self, resumen):
        totales = dict(MntExe=0, MntNeto=0, MntIVA=0, TasaIVA=0,
                       MntTotal=0, MntBase=0, MntRet=0, MontoNF=0, OtrosImp=0,
                       CredEC=0)
        totales["MontoNF"] = resumen.get('MontoNF', 0)
        totales['MntExe'] = resumen.get('MntExe', 0)
        if self.move_type == 'entry' or self.is_outbound():
            sign = 1
        else:
            sign = -1
        currency_base = self.currency_base()
        another_currency_id = self.currency_target()
        if self._es_exento():
            totales['MntExe'] = self.amount_total
            if self.amount_tax > 0:
                raise UserError(_("NO pueden ir productos afectos en documentos exentos"))
        elif self.amount_untaxed and self.amount_untaxed != 0:
            for t in self.line_ids:
                if t.display_type == 'rounding' or t.ind_exe:
                    continue
                if another_currency_id and currency_base != another_currency_id:
                    balance = sign * t.amount_currency
                else:
                    balance = sign * t.balance
                tax_line = t.tax_line_id
                sii_code = tax_line.sii_code
                sii_type = t.tax_repartition_line_id.sii_type
                if tax_line and sii_type == 'R':
                    totales['MntRet'] -= balance
                elif tax_line and tax_line.es_iva():
                    if totales['TasaIVA'] == 0:
                        totales['TasaIVA'] = round(tax_line.amount, 2)
                    totales['MntIVA'] += balance
                    if t.tax_repartition_line_id.credec:
                        totales['CredEC'] += balance
                    elif sii_type == 'A':
                        totales['MntRet'] += balance
                elif sii_code:
                    totales['OtrosImp'] += balance
                if any(tl.es_iva() for tl in t.tax_ids):
                    totales['MntNeto'] += balance
                if any(tl.sii_code in [17] for tl in t.tax_ids):
                    totales['MntBase'] += balance  # @TODO Buscar forma de calcular la base para faenamiento
        if totales['MntIVA'] == 0 and totales['MntExe'] > 0 and not \
                self._es_exento() and self.document_class_id.sii_code not in [
                                                                60, 61, 55, 56]:
            raise UserError(_("Debe ir almenos un producto afecto"))
        val_com_neto = 0
        val_com_exe = 0
        val_com_iva = 0
        iva = False
        if self.comision_ids:
            for c in self.comision_ids:
                val_com_neto += c.valor_neto_comision
                val_com_exe += c.valor_exento_comision
                val_com_iva += c.valor_iva_comision
            if val_com_neto:
                totales['ValComNeto'] = val_com_neto
                totales['MntNeto'] += val_com_neto
                iva = c.iva
            if val_com_exe:
                totales['ValComExe'] = val_com_exe
                totales['MntExe'] += val_com_exe
        if val_com_neto:
            taxes = iva.compute_all(
                val_com_neto,
                quantity=1,
                currency=self.currency_id,
                is_refund=self.move_type in ('out_refund', 'in_refund'),
                handle_price_include=True,
            )
            val_com_iva = taxes['taxes'][0]['amount']
            totales['ValComIVA'] = val_com_iva
            totales['MntIVA'] += val_com_iva
        totales['MntTotal'] = totales['MntNeto'] + totales['MntExe'] + \
            totales['MntIVA'] + totales['OtrosImp'] - totales['CredEC'] \
            - totales['MntRet']  - val_com_neto \
            - val_com_exe - val_com_iva
        if totales['MontoNF']:
            if self.es_boleta():
                totales['TotalPeriodo'] = totales['MntTotal'] + totales['MontoNF']
            else:
                totales['MontoPeriodo'] = totales['MntTotal'] + totales['MontoNF']
        if not self.document_class_id.es_exportacion():
            totales['VlrPagar'] = totales['MntTotal']
        return totales

    def _monto_cuadratura(self):
        lineas = self.line_ids.filtered(
            lambda l: not l.product_id and not l.tax_ids and not l.tax_line_id
            and l.name and l.name.endswith('(rounding)')
        )
        return sum(lineas.mapped('amount_currency'))

    def _totales(self, resumen):
        Totales = super()._totales(resumen)
        cuadr = self.currency_id.round(self._monto_cuadratura())
        if cuadr:
            Totales['IVA'] = Totales.get('IVA', 0) + cuadr
            Totales['MntTotal'] += cuadr
            if 'VlrPagar' in Totales:
                Totales['VlrPagar'] += cuadr
        return Totales

    def _validaciones_caf(self, caf):
        fecha_timbre = fields.Date.context_today(self.with_context(tz=tz_stgo))
        if (self.document_class_id.es_factura_afecta()
                or self.document_class_id.es_nc()
                or self.document_class_id.es_nd()) \
                and fecha_timbre >= caf.expiration_date:
            raise UserError(_(
                "CAF para %s a utilizar ya vencido, por favor anular este folio %s y anular en el SII, luego retimbrar con un nuevo folio no vencido."
            ) % (caf.document_class_id.name, self.sii_document_number))
        super()._validaciones_caf(caf)

    def is_price_included(self):
        if not self.invoice_line_ids or not self.invoice_line_ids[0].tax_ids:
            return False
        tax = self.invoice_line_ids[0].tax_ids[0]
        if tax.price_include or (not tax.sii_detailed and (self.es_boleta() or self.es_nc_boleta())):
            return True
        return False

    def _get_lineas(self):
        return self.with_context(lang="es_CL").invoice_line_ids

    def _get_referencias_dte(self):
        return self.referencias

    def _lineas_para_dte(self):
        lineas = []
        desglose = self.env.context.get('force_desglose', False)
        for line in self._get_lineas():
            if not line.tpo_doc_liq and (not line.account_id or not line.product_id):
                continue
            if not line.name and not line.product_id:
                raise UserError(_("Debe ingrear un producto o una descripción/etiqueta de la línea"))
            no_product = line.product_id.default_code == "NO_PRODUCT"
            details = line.get_tax_detail(desglose)
            tax_include = details.get('taxInclude', False)
            price_unit = details['price_unit']
            MontoItem = line.price_total if tax_include else line.price_subtotal
            descuento_amount = 0
            disc_pct = line.discount or 0
            if disc_pct and self.currency_id == self.currency_base():
                # En pesos, el descuento (o recargo) del XML es la diferencia entre el bruto de la
                # línea y el MontoItem del asiento: así MontoItem = PrcItem x QtyItem - DescuentoMonto
                # y los totales del XML son los del asiento peso por peso. (El SII redondea el
                # descuento a pesos; Odoo redondea el subtotal: difieren en 1 peso cuando el
                # descuento termina en ,5.)
                bruto = self.currency_id.round(line.quantity * price_unit)
                descuento_amount = abs(bruto - MontoItem)
            elif disc_pct:
                base_amt = line.discount_amount if disc_pct > 0 else line.discount_amount * -1
                if details.get('desglose'):
                    taxes_res = line._get_price_total_and_subtotal_model(
                        base_amt, 1, 0,
                        self.currency_base(), line.product_id,
                        self.partner_id, line.tax_ids, self.move_type,
                    )
                    base_amt = taxes_res.get('price_subtotal', 0.0)
                descuento_amount = base_amt
            nombre = (
                line.product_id.with_context(display_default_code=False).name
                if line.product_id else line.name
            )
            descripcion = False
            if line.product_id and line.product_id.name != line.name:
                descripcion = line.name.replace(nombre or '', '')
            lineas.append({
                'product': line.product_id or False,
                'name': nombre,
                'description': descripcion,
                'qty': line.quantity,
                'uom_id': line.product_uom_id,
                'price_unit': price_unit,
                'discount_pct': disc_pct,
                'discount_amount': descuento_amount,
                'subtotal': MontoItem,
                'subtotal_exento': details.get('MntExe', 0),
                'tax_include': tax_include,
                'taxes': details.get('impuestos') or [],
                'cod_imp_adic': details.get('cod_imp_adic'),
                'ind_exe': details.get('IndExe'),
                'no_product': no_product,
                'tpo_doc_liq': line.tpo_doc_liq.sii_code if line.tpo_doc_liq else None,
                'needs_desglose': bool(details.get('desglose')),
                'nro_lin_det': line.sequence,
            })
        if self.invoice_cash_rounding_id:
            cash_rounding = self.line_ids.filtered(lambda l: l.display_type == 'rounding')
            if cash_rounding:
                sign = self.direction_sign
                MontoItem = cash_rounding.balance * sign
                lineas.append({
                    'product': False,
                    'name': cash_rounding.name,
                    'description': False,
                    'qty': 1,
                    'uom_id': False,
                    'price_unit': 0,
                    'discount_pct': 0,
                    'discount_amount': 0,
                    'subtotal': MontoItem if MontoItem > 0 else MontoItem * -1,
                    'subtotal_exento': MontoItem,
                    'tax_include': False,
                    'taxes': [],
                    'cod_imp_adic': None,
                    'ind_exe': "2" if MontoItem > 0 else "6",
                    'no_product': True,
                    'tpo_doc_liq': None,
                    'needs_desglose': False,
                    'nro_lin_det': len(self.invoice_line_ids) + 1,
                })
        return lineas

    def _gdr_lines(self):
        ind_exe = None
        if self.document_class_id.sii_code in [34] and (
            self.referencias and self.referencias[0].ref_id.sii_code == "34"
        ):
            ind_exe = 1
        result = []
        for dr in self.global_descuentos_recargos:
            result.append({
                'type': dr.type,
                'valor': dr.valor,
                'tipo_valor': '$' if dr.gdr_type == 'amount' else '%',
                'glosa': dr.gdr_detail or False,
                'ind_exe': ind_exe,
            })
        return result

    def _referencia_a_dict(self, ref, lin_ref):
        ref_line = super()._referencia_a_dict(ref, lin_ref)
        if self.es_boleta():
            ref_line["CodVndor"] = self.user_id.id
            # account.journal no tiene point_of_sale_id (tampoco en 16.0);
            # se emite CodCaja solo si algún módulo agrega el campo
            caja = getattr(self.journal_id, "point_of_sale_id", False)
            if caja:
                ref_line["CodCaja"] = caja.name
        return ref_line

    def _dte_set_pruebas_ref(self, lin_ref):
        if not self.env.context.get("set_pruebas", False):
            return None
        razon = "CASO"
        n_atencion = self.env.context.get('n_atencion')
        if not self.es_boleta() and n_atencion:
            razon += " " + n_atencion
        razon += "-" + str(self.sii_batch_number)
        ref_line = {"NroLinRef": lin_ref, "RazonRef": razon}
        if self.es_boleta():
            ref_line["CodRef"] = "SET"
        else:
            ref_line["TpoDocRef"] = "SET"
            ref_line["FolioRef"] = self.get_folio()
            ref_line["FchRef"] = datetime.strftime(datetime.now(), "%Y-%m-%d")
        return ref_line

    def _dte(self):
        dte = super()._dte()
        if self.comision_ids:
            dte["Comisiones"] = self._comisiones()
        dte["CodIVANoRec"] = self.no_rec_code
        dte["IVAUsoComun"] = self.iva_uso_comun
        dte["moneda_decimales"] = self.currency_id.decimal_places
        return dte

    def _prepare_dte_data(self):
        self.ensure_one()

        if self.env.context.get("set_pruebas", False) or self.sii_result == "Rechazado" or not self.sii_xml_dte:
            self._timbrar()

        if self.sii_result in ["Rechazado"] or (
            self.env.context.get("set_pruebas", False) and
            self.sii_xml_request and
            self.sii_xml_request.state in ["", "draft", "NoEnviado"]
        ):
            if self.sii_xml_request and self.sii_xml_request.state in ["Aceptado", "Enviado", "EnProceso"]:
                self.sii_xml_request = False
            elif self.sii_xml_request:
                if len(self.sii_xml_request.move_ids) == 1:
                    self.sii_xml_request.unlink()
                else:
                    self.sii_xml_request = False
            self.sii_message = ""

        return {
            'TipoDTE': self.document_class_id.sii_code,
            'sii_xml_request': self.sii_xml_dte,
            'Folio': self.get_folio(),
        }

    def _post_dte_status_notify(self):
        for r in self:
            mess = False
            if r.sii_result == "Rechazado":
                mess = {
                    "title": "Documento Rechazado",
                    "message": "%s" % r.name,
                    "type": "dte_notif",
                }
            if r.sii_result == "Anulado":
                r.canceled = True
                try:
                    r.button_cancel()
                except Exception:
                    _logger.warning("Error al cancelar Documento", exc_info=True)
                mess = {
                    "title": "Documento Anulado",
                    "message": "%s" % r.name,
                    "type": "dte_notif",
                }
            if mess:
                self.env["bus.bus"]._sendone(
                    self.env.user.partner_id,
                    'account.move/display_notification', mess)

    def set_dte_claim(self, claim=False):
        if self.document_class_id.sii_code not in [33, 34, 43]:
            self.claim = claim
            return
        tipo_dte = self.document_class_id.sii_code
        datos = self._get_datos_empresa()
        partner_id = self.commercial_partner_id or self.partner_id.commercial_partner_id
        rut_emisor = partner_id.rut()
        datos["DTEClaim"] = [
            {
                "RUTEmisor": rut_emisor,
                "TipoDTE": tipo_dte,
                "Folio": str(self.sii_document_number),
                "Claim": claim,
            }
        ]
        key = "RUT%sT%sF%s" %(rut_emisor,
                              tipo_dte, str(self.sii_document_number))
        try:
            respuesta = fe.ingreso_reclamo_documento(datos)
            self.claim_description = respuesta[key]
        except Exception as e:
            msg = "Error al ingresar Reclamo DTE"
            _logger.warning("{}: {}".format(msg, str(e)), exc_info=True)
            if e.args[0][0] == 503:
                raise UserError(
                    "%s: Conexión al SII caída/rechazada o el SII está temporalmente fuera de línea, reintente la acción"
                    % (msg)
                )
            raise UserError("{}: {}".format(msg, str(e)))
        self.claim_description = respuesta
        if respuesta.get(key,
                         {'respuesta': {'codResp': 9}})['respuesta']["codResp"] in [0, 7]:
            self.claim = claim


    def get_dte_claim(self):
        tipo_dte = self.document_class_id.sii_code
        datos = self._get_datos_empresa()
        company = self.company_id.parent_id or self.company_id
        rut_emisor = company.partner_id.rut()
        if self.move_type in ["in_invoice", "in_refund"]:
            partner_id = self.commercial_partner_id or self.partner_id.commercial_partner_id
            rut_emisor = partner_id.rut()
        datos["DTEClaim"] = [
            {
                "RUTEmisor": rut_emisor,
                "TipoDTE": tipo_dte,
                "Folio": str(self.sii_document_number),
            }
        ]
        try:
            respuesta = fe.consulta_reclamo_documento(datos)
            key = "RUT%sT%sF%s" %(rut_emisor,
                                  tipo_dte, str(self.sii_document_number))
            self.claim_description = respuesta[key]
        except Exception as e:
            if e.args[0][0] == 503:
                raise UserError(
                    "%s: Conexión al SII caída/rechazada o el SII está temporalmente fuera de línea, reintente la acción"
                    % (tools.ustr(e))
                )
            raise UserError(tools.ustr(e))


    def wizard_upload(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": "sii.dte.upload_xml.wizard",
            "src_model": "account.move",
            "view_mode": "form",
            "view_type": "form",
            "views": [(False, "form")],
            "target": "new",
            "tag": "action_upload_xml_wizard",
        }


    def print_cedible(self):
        """ Print Cedible
        """
        return self.env.ref("l10n_cl_fe.action_print_cedible").report_action(self)


    def print_copy_cedible(self):
        """ Print Copy and Cedible
        """
        return self.env.ref("l10n_cl_fe.action_print_copy_cedible").report_action(self)

    def send_exchange(self):
        commercial_partner_id = self.commercial_partner_id or self.partner_id.commercial_partner_id
        att = self._create_attachment()
        if commercial_partner_id.es_mipyme:
            return
        body = "XML de Intercambio DTE: %s" % (self.name)
        subject = "XML de Intercambio DTE: %s" % (self.name)
        dte_email_id = self.company_id.parent_id.dte_email_id or self.company_id.dte_email_id or self.env.company.parent_id.dte_email_id or self.env.company.dte_email_id
        dte_receptors = commercial_partner_id.child_ids + commercial_partner_id
        email_to = commercial_partner_id.dte_email + "," if commercial_partner_id.dte_email else  ""
        for dte_email in dte_receptors:
            if not dte_email.send_dte or not dte_email.email:
                continue
            if dte_email.email in ["facturacionmipyme2@sii.cl", "facturacionmipyme@sii.cl"]:
                resp = self.env["sii.respuesta.cliente"].sudo().search([("exchange_id", "=", att.id)])
                resp.recep_envio = "0"
                continue
            if not dte_email.email in email_to:
                email_to += dte_email.email + ","
        if email_to == "":
            return
        values = {
            "res_id": self.id,
            "email_from": dte_email_id.display_name,
            "email_to": email_to[:-1],
            "auto_delete": False,
            "model": "account.move",
            "body": body,
            "subject": subject,
            "attachment_ids": [[6, 0, att.ids]],
        }
        send_mail = self.env["mail.mail"].sudo().create(values)
        send_mail.send()


    def manual_send_exchange(self):
        self.send_exchange()


    def _get_report_base_filename(self):
        self.ensure_one()
        if self.document_class_id:
            string_state = ""
            if self.state == "draft":
                string_state = "en borrador "
            report_string = "{} {} {}".format(
                self.document_class_id.report_name or self.document_class_id.name,
                string_state,
                self.sii_document_number or "",
            )
        else:
            report_string = super(AccountMove, self)._get_report_base_filename()
        return report_string


    def exento(self):
        exento = 0
        for l in self.invoice_line_ids:
            if l.tax_ids[0].amount == 0:
                exento += l.price_subtotal
        return exento if exento > 0 else (exento * -1)

    def comisiones(self):
        total = 0
        for c in self.comision_ids:
            total += c.valor_neto_comision + c.valor_iva_comision + c.valor_exento_comision
        return total

    def getTotalDiscount(self):
        total_discount = 0
        for l in self.invoice_line_ids:
            if not l.account_id:
                continue
            total_discount += l.discount_amount
        return self.currency_id.round(total_discount)


    def sii_header(self):
        W, H = (560, 255)
        img = Image.new("RGB", (W, H), color=(255, 255, 255))

        d = ImageDraw.Draw(img)
        w, h = (0, 0)
        for _i in range(10):
            d.rectangle(((w, h), (550 + w, 220 + h)), outline="black")
            w += 1
            h += 1
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
        company = self.company_id.parent_id or self.company_id
        d.text((50, 30), "R.U.T.: %s" % company.document_number, fill=(0, 0, 0), font=font)
        d.text((50, 90), self.document_class_id.name, fill=(0, 0, 0), font=font)
        d.text((220, 150), "N° %s" % self.sii_document_number, fill=(0, 0, 0), font=font)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        d.text((200, 235), "SII %s" % company.sii_regional_office_id.name, fill=(0, 0, 0), font=font)

        buffered = BytesIO()
        img.save(buffered, format="PNG")
        imm = base64.b64encode(buffered.getvalue()).decode()
        return imm


    def currency_format(self, val, application='Product Price'):
        Lang = self.env['res.lang']
        # Odoo 20: res.lang.format() sin `monetary`; solo idiomas activos (_lang_get).
        lang = (Lang._lang_get(self.env.context.get('lang') or self.partner_id.lang or '')
                or Lang._lang_get(self.env.lang or '') or Lang._lang_get('en_US'))
        precision = self.env['decimal.precision'].precision_get(application)
        string_digits = '%.{}f'.format(precision)
        res = lang.format(string_digits, val, grouping=True)
        if self.currency_id.symbol:
            if self.currency_id.position == 'after':
                res = '%s %s' % (res, self.currency_id.symbol)
            elif self.currency_id.position == 'before':
                res = '%s %s' % (self.currency_id.symbol, res)
        return res
