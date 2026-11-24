# -*- coding: utf-8 -*-
import math
from odoo import _, api, fields, models
from odoo.exceptions import UserError
from .currency import float_round_custom
from odoo.tools.float_utils import float_round as round
import logging
_logger = logging.getLogger(__name__)


class AccountInvoiceLine(models.Model):
    _inherit = "account.move.line"

    sequence = fields.Integer(string="Sequence", default=-1,)

    def _line_size(self):
        if not self.name:
            return 1
        factor = self.move_id.company_id.dte_lines_factor or 50.0
        size = 0
        for l in self.name.split('\n'):
            size += math.ceil(len(l) / factor) or 1
        return size

    discount_amount = fields.Float(string="Monto Descuento", default=0.00, compute="set_discount_amount", store=True)
    is_gd_line = fields.Boolean(
        string="Es Línea descuento Global"
    )
    is_gr_line = fields.Boolean(
        string="Es Línea Recargo Global"
    )
    display_type = fields.Selection(
        selection_add=[
            ('D', 'Descuento Global'),
            ('R', 'Recargo Global'),
            ('C', 'Comisión u Otros Cargos'),
        ],
        ondelete={'D': 'set product', 'R': 'set product', 'C': 'set product'})
    ind_exe = fields.Selection([
            ('1', 'No afecto o exento de IVA (10)'),
            ('2', 'Producto o servicio no es facturable'),
            ('3', 'Garantía de depósito por envases (Cervezas, Jugos, Aguas Minerales, Bebidas Analcohólicas u otros autorizados por Resolución especial)'),
            ('4', 'Ítem No Venta. (Para facturas y guías de despacho (ésta última con Indicador Tipo de Traslado de Bienes igual a 1) y este ítem no será facturado.'),
            ('5', 'Ítem a rebajar. Para guías de despacho NO VENTA que rebajan guía anterior. En el área de referencias se debe indicar la guía anterior.'),
            ('6', 'Producto o servicio no facturable negativo (excepto en liquidaciones-factura)'),
        ],
        string="Indicador Exento"
    )
    tpo_doc_liq = fields.Many2one(
        'sii.document_class',
        string="Tipo de documento Liquidación"
    )

    @api.depends('balance', 'display_type')
    def _compute_totals(self):
        # Odoo 20 solo calcula price_subtotal/price_total en las líneas 'product'; las líneas de
        # descuento/recargo global ('D'/'R') y comisiones ('C') de este módulo los toman del saldo.
        super()._compute_totals()
        for line in self.filtered(lambda l: l.display_type in ('C', 'D', 'R')):
            price_unit = line.balance
            if line.display_type in ('D', 'C'):
                price_unit *= -1
            currency = line.currency_id or line.company_currency_id
            subtotal = line.quantity * price_unit
            subtotal -= currency.round(subtotal * (line.discount / 100.0))
            if line.tax_ids:
                taxes_res = line.tax_ids.compute_all(
                    subtotal / line.quantity if line.quantity else 0.0,
                    quantity=line.quantity,
                    currency=currency,
                    product=line.product_id,
                    partner=line.partner_id,
                    is_refund=line.is_refund,
                )
                line.price_subtotal = taxes_res['total_excluded']
                line.price_total = taxes_res['total_included']
            else:
                line.price_total = line.price_subtotal = subtotal

    @api.depends('debit', 'credit', 'amount_currency', 'account_id', 'currency_id', 'company_id',
                 'matched_debit_ids', 'matched_credit_ids')
    def _compute_amount_residual(self):
        for line in self:
            if line.tax_repartition_line_id.sii_type == 'A':
                self -= line
        super(AccountInvoiceLine, self)._compute_amount_residual()

    @api.onchange('tax_ids')
    def set_ind_exe(self):
        if len(self.tax_ids) == 1:
            self.ind_exe = self.tax_ids.ind_exe

    @api.onchange('quantity', 'discount', 'price_unit', 'tax_ids', 'currency_id')
    @api.depends('quantity', 'discount', 'price_unit', 'tax_ids', 'currency_id')
    def set_discount_amount(self):
        for r in self:
            total = r.currency_id.round(r.quantity * r.price_unit)
            r.discount_amount = float_round_custom(total * ((r.discount or 0.0) / 100.0), precision_digits=0)

    @api.model
    def _get_price_total_and_subtotal_model(self, price_unit, quantity, discount, currency, product, partner, taxes, move_type, uom_id=False):
        """Calcula 'price_subtotal' y 'price_total' de una línea (usado al armar el XML del DTE).

        Con impuestos del SII el descuento se redondea a pesos sobre el total bruto de la línea,
        como lo exige el SII; el resto lo calcula el motor de impuestos de Odoo.
        """
        res = {}
        if taxes and any(bool(t.sii_code) for t in taxes):
            total = currency.round(quantity * price_unit)
            discount_amount = float_round_custom(total * ((discount or 0.0) / 100.0), precision_digits=0)
            subtotal = quantity * price_unit - discount_amount
        else:
            subtotal = quantity * price_unit * (1 - ((discount or 0.0) / 100.0))
        if taxes:
            taxes_res = taxes._origin.compute_all(
                subtotal / quantity if quantity else 0.0,
                quantity=quantity, currency=currency, product=product,
                partner=partner,
                is_refund=move_type in ('out_refund', 'in_refund'),
            )
            res['price_subtotal'] = taxes_res['total_excluded']
            res['price_total'] = taxes_res['total_included']
        else:
            res['price_total'] = res['price_subtotal'] = subtotal
        #In case of multi currency, round before it's use for computing debit credit
        if currency:
            res = {k: currency.round(v) for k, v in res.items()}
        return res

    def get_tax_detail(self, force_desglose=False):
        boleta = self.move_id.document_class_id.es_boleta()
        nc_boleta = self.move_id.es_nc_boleta()
        details = dict(
            impuestos=[],
            taxInclude=False,
            MntExe=0,
            price_unit=self.price_unit,
            desglose=force_desglose,
            IndExe=False,
        )
        force_precio_neto = False
        currency_base = self.move_id.currency_base()
        i = 0
        for t in self.tax_ids:
            if self.move_id.use_documents and not self.move_id.es_nc() and \
            not self.move_id.es_nd() and self.move_id.document_class_id.sii_code \
            not in t.documentos_dte_admitidos():
                raise UserError("Tipo de documento {0} no admitido para el impuesto [{1}]{2}".format(
                    self.move_id.document_class_id.name,
                    t.sii_code,
                    t.name
                ))
            i += 1
            taxInclude = t.price_include
            details['taxInclude'] = taxInclude
            if taxInclude:
                details['desglose'] = t.sii_detailed or details['desglose']
            if taxInclude and details['desglose']:
                if i > 1 and not details['desglose'] and not force_precio_neto:
                    return self.get_tax_detail(force_desglose=True)
                force_precio_neto = True
            if t.es_adicional() or t.es_especifico():
                if boleta or nc_boleta:
                    if i > 1 and not details['desglose'] and not force_precio_neto:
                        return self.get_tax_detail(force_desglose=True)
                    force_precio_neto = True
                    continue
                details['cod_imp_adic'] = t.sii_code
            if self.ind_exe or t.ind_exe or t.amount == 0 or t.sii_code in [0]:
                details['IndExe'] = self.ind_exe or t.ind_exe or "1"
                if not self.ind_exe:
                    self.ind_exe = details['IndExe']
                details['MntExe'] += currency_base.round(self.price_subtotal)
            else:
                amount = t.amount
                if t.sii_code in [28, 35]:
                    amount = t.compute_factor(self.product_uom_id)
                details['impuestos'].append({
                            "CodImp": t.sii_code,
                            'price_include': taxInclude,
                            'TasaImp': amount,
                            'mepco': t.mepco,
                        }
                )

        if force_precio_neto:
            details['taxInclude'] = False
            taxes_res = self._get_price_total_and_subtotal_model(
                    self.price_unit,
                    1,
                    0,
                    self.move_id.currency_id,
                    self.product_id,
                    self.move_id.partner_id,
                    self.tax_ids,
                    self.move_id.move_type)
            has_extra = any(
                t.es_adicional() or t.es_especifico() for t in self.tax_ids
            )
            if (boleta or nc_boleta) and has_extra:
                iva_tax = self.tax_ids.filtered(lambda t: t.es_iva())
                iva_rate = (iva_tax[:1].amount if iva_tax else 19.0) or 19.0
                iva_factor = 1 + iva_rate / 100.0
                details['price_unit'] = taxes_res.get('price_total', 0.0) / iva_factor
            else:
                details['price_unit'] = taxes_res.get('price_subtotal', 0.0)
        return details
