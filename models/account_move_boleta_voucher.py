# -*- coding: utf-8 -*-
from odoo import fields, models, api
from odoo.tools.translate import _
from odoo.exceptions import UserError
from datetime import datetime
import dateutil.relativedelta as relativedelta
import logging
_logger = logging.getLogger(__name__)

try:
    import pdf417gen
except ImportError:
    _logger.warning('Cannot import pdf417gen library')
try:
    import base64
except ImportError:
    _logger.warning('Cannot import base64 library')


class SIIResumenBoletaVoucher(models.Model):
    _name = 'account.move.boleta_voucher'
    _inherit = ['l10n_cl_fe.dte.mixin']
    _description = "Resumen Mensual para crear NC de Boletas Electrónicas con pagos Electrónicos"

    _dte_cola_field = 'boleta_voucher_ids'
    _dte_xml_envio_field = 'boleta_voucher_ids'

    move_ids = fields.Many2many(
        'account.move',
    	readonly=True,
    )
    fecha_emision = fields.Date(
        string="Fecha Emisión",
        readonly=True,
        default=lambda self: fields.Date.context_today(self),
    )
    total_neto = fields.Monetary(
        string="Total Neto",
        readonly=True,
    )
    total_iva = fields.Monetary(
        string="Total IVA",
        readonly=True,
    )
    total_exento = fields.Monetary(
        string="Total Exento",
        readonly=True,
    )
    total = fields.Monetary(
        string="Monto Total",
        readonly=True,
    )
    total_neto_calculado = fields.Monetary(
        string="Total Neto Calculado",
        store=True,
        compute='set_totales',
    )
    total_iva_calculado = fields.Monetary(
        string="Total IVA Calculado",
        store=True,
        compute='set_totales',
    )
    total_exento_calculado = fields.Monetary(
        string="Total Exento Calculado",
        store=True,
        compute='set_totales',
    )
    total_calculado = fields.Monetary(
        string="Monto Total Calculado",
        store=True,
        compute='set_totales',
    )
    total_boletas = fields.Integer(
        string="Total Boletas",
        store=True,
        compute='set_totales',
    )
    company_id = fields.Many2one(
        'res.company',
        required=True,
        default=lambda self: self.env.company.id,
    	readonly=True,
    )
    name = fields.Char(
        string="Detalle" ,
        required=True,
    	readonly=True,
    )
    currency_id = fields.Many2one(
        'res.currency',
        string='Moneda',
        default=lambda self: self.env.company.currency_id,
        required=True,
    	readonly=True,
    )
    sequence_id = fields.Many2one(
        'ir.sequence',
        required=True,
        readonly=True,
    )
    document_class_id = fields.Many2one(
        'sii.document_class',
        related="sequence_id.sii_document_class_id",
        string='Document Type',
        readonly=True,
    )
    folio_anular = fields.Integer(
        string="Folio que será anulado",
        required=True,
        readonly=True,
    )
    periodo = fields.Char(
        string='Periodo a generar',
        required=True,
        readonly=True,
        default=lambda *a: (datetime.now() - relativedelta.relativedelta(months=1)).strftime('%Y-%m'),
    )
    medios_de_pago = fields.Many2many(
        'account.journal',
        string="Medios de pago Electrónicos",
        readonly=True,
        default=lambda self: self.company_id.medios_de_pago_electronico.ids,
    )

    _order = 'fecha_emision desc'

    @api.onchange('company_id')
    def set_medios_pago_company(self):
        if self.company_id:
            self.medios_de_pago += self.company_id.medios_de_pago_electronico

    @api.onchange('periodo')
    def set_name(self):
        current = datetime.strptime(self.periodo + '-01', '%Y-%m-%d' )
        last_day = current + relativedelta.relativedelta(day=31)
        self.fecha_emision = str(last_day)

    def _get_moves(self):
        recs = []
        for r in self.move_ids:
            query = [
                ('sii_document_number', '=', r.sii_document_number),
                ('document_class_id', '=', r.document_class_id.id),
                ('move_type', '=', 'out_invoice'),
            ]
            ref = self.env['account.move'].search(query)
            recs.append(ref)
        return recs

    @api.onchange('move_ids')
    @api.depends('move_ids')
    def set_totales(self):
        for rec in self:
            monto_total = 0
            monto_neto = 0
            monto_iva = 0
            moves = rec._get_moves()
            for r in moves:
                monto_neto += (r.amount_total - r.amount_tax)
                monto_iva += r.amount_tax
                monto_total += r.amount_total
            rec.folio_anular = moves[-1].sii_document_number if moves else 0
            rec.total_neto_calculado = rec.total_neto = monto_neto
            rec.total_iva_calculado = rec.total_iva = monto_iva
            rec.total_calculado = rec.total = monto_total
            rec.total_boletas = len(moves)

    @api.onchange('periodo', 'company_id', 'medios_de_pago')
    def set_movimientos(self):
        if not self.medios_de_pago:
            return
        current = datetime.strptime( self.periodo + '-01', '%Y-%m-%d' )
        next_month = current + relativedelta.relativedelta(months=1)
        query = [
            ('ref_id.sii_code', 'in', [39, 41]),
            ('cod_ref', 'in', ['1', '3']),
            ('move_id.invoice_date' , '>=', current),
            ('move_id.invoice_date' , '<=', next_month),
            ('move_id.journal_id', 'in', self.medios_de_pago.ids),
            ('move_id.company_id', '=', self.company_id.id),
        ]
        ncs = self.env['account.move.referencias'].search(query)
        query = [
            ('reconciled_line_ids.company_id', '=', self.company_id.id),
            ('reconciled_line_ids.move_id.invoice_date' , '>=', current),
            ('reconciled_line_ids.move_id.invoice_date' , '<=', next_month),
            ('reconciled_line_ids.journal_id', 'in', self.medios_de_pago.ids),
            ('reconciled_line_ids.move_id.document_class_id.sii_code', 'in', [39, 41]),
            ('reconciled_line_ids.move_id.move_type', '=', 'out_invoice'),
        ]
        nc_invs = self.env['account.move']
        for nc in ncs:
            o = self.env['account.move.referencias'].search([
                ('sii_document_number', '=', nc.folio),
                ('document_class_id', '=', nc.ref_id.id),
            ])
            nc_invs += o
        if nc_invs:
            query.append(('reconciled_line_ids.move_id.id', 'not in', nc_invs.ids))
        moves = self.env['account.move']
        for r in self.env['account.full.reconcile'].search(query):
            for aml in r.reconciled_line_ids:
                if aml.debit > 0:
                    moves += aml.move_id
        self.move_ids = moves



    def _emisor(self):
        Emisor = super()._emisor()
        Emisor['RUTEmisor'] = self.company_id.document_number
        return Emisor

    def _dte_partner_recep(self):
        return self.company_id.partner_id.commercial_partner_id

    def _dte_recep_giro(self, partner):
        if not self.company_id.activity_description:
            raise UserError(_('Seleccione giro del partner'))
        return self.company_id.activity_description.name

    def _dte_recep_correo(self, partner):
        return partner.email or partner.dte_email or False


    def _dte_fch_emis(self):
        return self.fecha_emision.strftime("%Y-%m-%d")

    def _id_doc(self, resumen):
        IdDoc = super()._id_doc(resumen)
        IdDoc['MntBruto'] = 1
        IdDoc['FchVenc'] = self.fecha_emision.strftime("%Y-%m-%d")
        return IdDoc

    def currency_base(self):
        return self.env.ref('base.CLP').with_context(date=self.fecha_emision)

    def currency_target(self):
        if self.currency_id != self.currency_base():
            return self.currency_id.with_context(date=self.fecha_emision)
        return False

    def _encabezado_incluye_emisor(self):
        return False

    def _totales_intermedio(self, resumen):
        return {
            'MntExe': 0,
            'MntNeto': self.total_neto,
            'MntIVA': self.total_iva,
            'TasaIVA': self.env['account.tax'].search([
                ('sii_code', '=', 14),
                ('type_tax_use', '=', 'sale')]).amount,
            'MntTotal': self.total,
        }

    def _detalle_sii(self):
        return {
            'Detalle': [{
                'NroLinDet': 1,
                'NmbItem': 'Anula Doucmento',
                'DscItem': 'Anula ventas con comprobante electrónico',
                'QtyItem': 1,
                'MontoItem': self.total,
                'IndExe': 1,
            }],
            'MntExe': 0,
            'MontoNF': 0,
            'tax_include': False,
            'no_product': False,
            'TasaIVA': 0,
        }

    def _lineas_para_dte(self):
        return []

    def _get_referencias_dte(self):
        return self.env['account.move.boleta_voucher']

    def _referencias_dte(self):
        return [{
            'NroLinRef': 1,
            'TpoDocRef': 39,
            'FolioRef': self.folio_anular,
            'FchRef': self.fecha_emision.strftime("%Y-%m-%d"),
            'RazonRef': "Rebajo IVA ventas Boletas Voucher",
            'CodRef': 1,
        }]

    def _dte(self):
        dte = super()._dte()
        dte['moneda_decimales'] = self.currency_id.decimal_places
        return dte

    def _get_caf(self, folio):
        return self.sequence_id

    def _caf_file(self, caf, folio):
        return caf.get_caf_file(folio, decoded=False).decode()

    def _validaciones_caf(self, caf):
        return

    def validar_boleta_voucher(self):
        if not self.sii_document_number:
            if not self.sequence_id:
                raise UserError("Debe seleccionar secuencia para de timbraje")
            if not self.document_class_id:
                raise UserError("La secuencia debe ser de tipo timbraje")
            self.sii_document_number = self.sequence_id.next_by_id()
        prefix = self.document_class_id.doc_code_prefix
        self.name = (prefix + str(self.sii_document_number)).replace(' ', '')
        self._timbrar()
        self.queue({
            'company_id': self.company_id.id,
            'tipo_trabajo': 'envio',
            'send_email': False,
        })
        self.sii_result = 'EnCola'

    def _prepare_dte_data(self):
        self.ensure_one()
        if self.sii_result not in ["", "NoEnviado", "Rechazado", "EnCola"]:
            return False

        if self.sii_result == "Rechazado" or not self.sii_xml_dte:
            self._timbrar()

        if self.sii_result in ['Rechazado'] or (
            self.sii_xml_request and
            self.sii_xml_request.state in ["", "draft", "NoEnviado"]
        ):
            if self.sii_xml_request and self.sii_xml_request.state in ["Aceptado", "Enviado", "EnProceso"]:
                self.sii_xml_request = False
            elif self.sii_xml_request:
                self.sii_xml_request.unlink()
            self.sii_result = 'NoEnviado'
            self.sii_message = ''

        return {
            'TipoDTE': self.document_class_id.sii_code,
            'sii_xml_request': self.sii_xml_dte,
            'Folio': self.get_folio(),
        }

    def _dte_status_folio_key(self, record):
        return record.get_folio()

    def _dte_status_apply(self, record, result):
        record.sii_result = result['status']
        if result.get('xml_resp'):
            record.sii_message = result['xml_resp']

    def _post_dte_status_notify(self):
        for r in self:
            mess = False
            if r.sii_result == 'Rechazado':
                mess = {'title': "Documento Rechazado", 'message': r.name, 'type': 'dte_notif'}
            if r.sii_result == 'Anulado':
                r.canceled = True
                try:
                    r.action_invoice_cancel()
                except Exception:
                    _logger.warning("Error al cancelar Documento")
                mess = {'title': "Documento Anulado", 'message': r.name, 'type': 'dte_notif'}
            if mess:
                self.env['bus.bus'].sendone(
                    (self._cr.dbname, 'account.move.boleta_voucher', r.env.user.partner_id.id),
                    mess,
                )

    def set_draft(self):
        if self.sii_result in ['Rechazado'] or self.sii_xml_request.state in ["", "draft", "NoEnviado"]:
            if self.sii_xml_request and self.sii_xml_request.state in ["Aceptado", "Enviado", "EnProceso"]:
                self.sii_xml_request = False
            elif self.sii_xml_request:
                self.sii_xml_request.unlink()
            self.sii_result = 'draft'
            self.sii_message = ''


