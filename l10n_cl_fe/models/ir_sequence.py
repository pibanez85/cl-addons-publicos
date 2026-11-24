import base64
import logging
from datetime import datetime
import pytz
from psycopg2 import errors

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.translate import _

_logger = logging.getLogger(__name__)


def update_next_by_caf(self, folio, caf):
    if not self.sii_document_class_id:
        return 0
    if not caf:
        _logger.warning("No quedan folios disponibles CAFs para %s disponibles" % caf.name)
        return 0
    folio = caf.folio_actual
    if self.implementation == "no_gap":
        self.flush_recordset(['number_next'])
        number_next = self.number_next
        self.env.cr.execute("SELECT number_next FROM %s WHERE id=%%s FOR UPDATE NOWAIT" % self._table, [self.id])
        self.env.cr.execute("UPDATE %s SET number_next=%%s WHERE id=%%s " % self._table, (folio, self.id))
        self.invalidate_recordset(['number_next'])
    else:
        self.sudo().write({"number_next": folio})
    return folio

class IRSequence(models.Model):
    _inherit = "ir.sequence"

    def get_qty_available(self, folio=None):
        folio = int(folio or self.number_next_actual)
        try:
            cafs = self.get_caf_files(folio)
        except Exception as ex:
            _logger.warning("Problema al obtener cafs",
            exc_info=True)
            cafs = self.env["dte.caf"]
        available = 0
        if folio > 0:
            for c in cafs:
                available += min(c.qty_available, c.final_nm + 1 - max(folio, c.start_nm))
            if available <= self.nivel_minimo:
                alert_msg = "Nivel bajo de folios del CAF para {}, quedan {} folios. Recuerde verificar su token apicaf.cl".format(
                    self.sii_document_class_id.name, available,
                )
                #self.env["bus.bus"]._sendone(
                #    self.env.user.partner_id,
                #    'ir.sequence/display_notification',
                #    {"title": "Alerta sobre Folios", "message": alert_msg, "url": "res_config", "type": "dte_notif"}
                #)
        return available

    @api.onchange("dte_caf_ids", "number_next_actual")
    def _qty_available(self):
        for i in self:
            i.qty_available = 0
            if i.is_dte and i.sii_document_class_id:
                i.qty_available = i.get_qty_available()

    sii_document_class_id = fields.Many2one("sii.document_class", string="Tipo de Documento",)
    is_dte = fields.Boolean(string="IS DTE?", default=False)
    dte_caf_ids = fields.One2many("dte.caf", "sequence_id", string="DTE CAF",)
    qty_available = fields.Integer(string="Quantity Available", compute="_qty_available")
    nivel_minimo = fields.Integer(string="Nivel Mínimo de Folios", default=5,)  # @TODO hacerlo configurable
    autoreponer_caf = fields.Boolean(string="Reposición Automática de CAF", default=False)
    autoreponer_cantidad = fields.Integer(string="Cantidad de Folios a Reponer", default=2)
    caf_bajo_pedido = fields.Boolean(string="CAF Bajo Pedido", default=False,
        help="Solicita automáticamente un CAF cuando se requiere un folio y no hay disponibles")
    folios_sin_usar = fields.Integer(string="Folios sin usar", compute="set_folios_sin_usar", store=True)

    @api.model
    def check_cafs(self):
        self.env.cr.execute("SELECT id FROM dte_caf WHERE expiration_date <= NOW() and cantidad_folios_sin_usar > 0")
        for r in self.env["dte.caf"].sudo().browse([x[0] for x in self.env.cr.fetchall()]):
            try:
                r.expirar_folios()
            except:
                _logger.warning("no se pudo auto anular")
            try:
                r.auto_anular()
            except:
                _logger.warning("no se pudo auto anular")
        self.env.cr.commit()
        self.env.cr.execute("SELECT id FROM ir_sequence WHERE is_dte")
        for r in self.env["ir.sequence"].sudo().browse([x[0] for x in self.env.cr.fetchall()]):
            for caf in r.dte_caf_ids.filtered(lambda c: c.state == 'in_use'):
                self._refrescar_caf_cron(caf)
            if r.autoreponer_caf and r.qty_available < r.nivel_minimo:
                try:
                    r.solicitar_caf()
                    self.env.cr.commit()
                except Exception as e:
                    self.env.cr.rollback()
                    _logger.warning(
                        "Error al solictar folios a secuencia {}: {}".format(
                            r.sii_document_class_id.name, str(e)),
                        exc_info=True
                    )

    def _refrescar_caf_cron(self, caf):
        try:
            with self.env.cr.savepoint():
                self.env.cr.execute(
                    "SELECT id FROM dte_caf WHERE id = %s FOR UPDATE SKIP LOCKED",
                    [caf.id],
                )
                if not self.env.cr.fetchone():
                    return
                caf.invalidate_recordset()
                caf.compute_folio_actual()
                caf.flush_recordset()
        except (errors.SerializationFailure, errors.LockNotAvailable):
            caf.invalidate_recordset()
            return
        except Exception as e:
            caf.invalidate_recordset()
            _logger.warning(
                "Error refrescando folio_actual del CAF %s: %s",
                caf.id, str(e), exc_info=True,
            )
            return
        self.env.cr.commit()

    @api.onchange('sii_document_class_id')
    def _auto_set_is_dte(self):
        self.is_dte = self.sii_document_class_id.dte

    def _info_folios_dte(self):
        self.ensure_one()
        if not self.is_dte:
            return 0, 0
        try:
            folio = self.get_folio(readonly=True)
        except Exception:
            _logger.warning(
                "No se pudo leer el folio siguiente de la secuencia %s", self.name,
                exc_info=True)
            folio = 0
        try:
            quedan = self.qty_available
        except Exception:
            _logger.warning(
                "No se pudo leer los folios disponibles de la secuencia %s", self.name,
                exc_info=True)
            quedan = 0
        return folio, quedan

    def _etiqueta_folios(self):
        self.ensure_one()
        folio, quedan = self._info_folios_dte()
        if not folio:
            return _("sin folios")
        return _("folio %s, %s disponibles") % (folio, quedan)

    @api.depends_context("mostrar_folio")
    def _compute_display_name(self):
        super()._compute_display_name()
        if not self.env.context.get("mostrar_folio"):
            return
        for seq in self:
            if seq.is_dte:
                seq.display_name = "%s (%s)" % (seq.display_name, seq._etiqueta_folios())

    def solicitar_caf(self, cantidad=0):
        if self.qty_available == self.nivel_minimo:
            self.inspeccionar_folios_sin_usar()
            if self.qty_available >= self.nivel_minimo:
                return
        firma = self.env.user.sudo().get_digital_signature(self.company_id)
        wiz_caf = self.env["dte.caf.apicaf"].create(
            {"company_id": self.company_id.id, "sequence_id": self.id, "firma": firma.id,}
        )
        wiz_caf.conectar_api()
        alert_msg = False
        if not wiz_caf.id_peticion:
            alert_msg = "Problema al conectar con apicaf.cl"
            if wiz_caf.message:
                alert_msg = "%s: %s" % (alert_msg, wiz_caf.message)
        else:
            if not cantidad:
                cantidad = self.autoreponer_cantidad
            if wiz_caf.api_max_autor > 0 and cantidad > wiz_caf.api_max_autor:
                cantidad = wiz_caf.api_max_autor
            elif wiz_caf.api_max_autor == 0:
                self.autoreponer_caf = False
                alert_msg = (
                    "El SII no permite solicitar más CAFs para %s, consuma los %s folios disponibles o verifique situación tributaria en www.sii.cl"
                    % (self.sii_document_class_id.name, wiz_caf.api_folios_disp)
                )
        if alert_msg:
            _logger.warning(alert_msg)
            self.env["bus.bus"]._sendone(
                self.env.user.partner_id,
                'ir.sequence/display_notification',
                {"title": "Alerta sobre Folios", "message": alert_msg, "url": "res_config", "type": "dte_notif",},
            )
            return
        wiz_caf.cant_doctos = cantidad
        wiz_caf.obtener_caf()

    @api.depends('dte_caf_ids.cantidad_folios_sin_usar')
    def set_folios_sin_usar(self):
        for r in self:
            folios_sin_usar = 0
            for caf in r.dte_caf_ids:
                folios_sin_usar += caf.cantidad_folios_sin_usar
            r.folios_sin_usar = folios_sin_usar

    def obtener_folios_sin_usar(self):
        folios = []
        for caf in self.dte_caf_ids:
            folios += caf.obtener_folios_sin_usar()
        return folios

    #@api.onchange('qty_available')
    def inspeccionar_folios_sin_usar(self):
        for caf in self.dte_caf_ids:
            caf.inspeccionar_folios_sin_usar()

    def get_folio(self, number_next=False, readonly=False):
        requested = int(number_next or self.number_next)
        force = self.env.context.get('force_number_next')
        caf = self.dte_caf_ids.filtered(
            lambda c: c.start_nm <= requested <= c.final_nm and c.state != 'spent'
        ).sorted(key=lambda c: c.start_nm)[:1]
        range_match = bool(caf)
        if not caf:
            caf = self.env['dte.caf'].search([
                ('sequence_id', '=', self.id),
                ('folio_actual', '>=', requested),
                ('state', '!=', 'spent'),
            ],
            order='folio_actual ASC',
            limit=1)
        if not caf:
            if not self.dte_caf_ids:
                return 0
            caf = self.dte_caf_ids[0]
            if int(self.number_next) == caf.final_nm:
                return 0
        in_range = caf.start_nm <= requested <= caf.final_nm
        honor_requested = (force or range_match) and in_range
        if readonly:
            if honor_requested and not caf._is_folio_used(requested):
                folio_actual = requested
            else:
                folio_actual = caf._peek_folio_actual()
            if folio_actual > caf.final_nm:
                if caf == self.dte_caf_ids[0]:
                    return 0
                return self.get_folio(folio_actual + 1, readonly=True)
            return folio_actual
        caf.flush_recordset(['folio_actual'])
        self.env.cr.execute(
            "SELECT id FROM dte_caf WHERE id = %s FOR UPDATE NOWAIT",
            [caf.id],
        )
        caf.invalidate_recordset(['folio_actual', 'qty_available', 'cantidad_folios_sin_usar'])
        if honor_requested and not caf._is_folio_used(requested):
            if caf.folio_actual != requested:
                caf.folio_actual = requested
            folio_actual = requested
        else:
            caf.compute_folio_actual()
            folio_actual = caf.folio_actual
        if folio_actual != int(self.number_next):
            update_next_by_caf(self, folio_actual, caf)
        if caf.qty_available == 0:
            if caf == self.dte_caf_ids[0]:
                return 0
            return self.get_folio(folio_actual+1)
        if folio_actual == caf.final_nm:
            caf._mark_spent()
        try:
            caf.notify_use_expiration()
        except Exception:
            _logger.warning(
                "No se pudo avisar el vencimiento del CAF %s", caf.id, exc_info=True)
        return folio_actual

    def time_stamp(self, formato="%Y-%m-%dT%H:%M:%S"):
        tz = pytz.timezone("America/Santiago")
        return datetime.now(tz).strftime(formato)

    def get_caf_file(self, folio=False, decoded=True):
        folio = int(folio or self.number_next_actual)
        caffiles = self.get_caf_files(folio)
        msg = """No Hay caf para el documento: {}, está fuera de rango . \
Solicite un nuevo CAF en el sitio www.sii.cl""".format(
            folio
        )
        if not caffiles:
            raise UserError(
                _(
                    """No hay caf disponible para el documento %s folio %s. \
Por favor solicite y suba un CAF en el portal del SII o Utilice la opción \
obtener folios en la secuencia (usando apicaf.cl)."""
                    % (self.name, folio)
                )
            )
        for caffile in caffiles:
            if folio >= caffile.start_nm and folio <= caffile.final_nm:
                if caffile.expiration_date:
                    if fields.Date.context_today(self) > caffile.expiration_date:
                        msg = "CAF Vencido. %s" % msg
                        continue
                if decoded:
                    return caffile.decode_caf()
                return base64.b64encode(caffile.caf_string.encode("ISO-8859-1"))
        raise UserError(_(msg))

    def get_caf_files(self, folio=None):
        """
            Devuelvo caf actual y futuros
        """
        folio = int(folio or self.number_next_actual)
        if not self.dte_caf_ids:
            return self.dte_caf_ids
        cafs = self.dte_caf_ids.filtered(
                lambda caf: folio <= caf.final_nm
            ).sorted(key=lambda caf: caf.start_nm)
        return cafs

    def _next_do(self):
        if self.is_dte:
            folio = self.get_folio()
            if folio == 0:
                if self.caf_bajo_pedido:
                    self.solicitar_caf(cantidad=1)
                    folio = self.get_folio()
                if folio == 0:
                    raise UserError(
                        _("No hay más folios disponibles para el documento %s. "
                        "Por favor solicite y suba un CAF en el portal del SII o Utilice la opción "
                        "obtener folios en la secuencia (usando apicaf.cl).")
                        % (self.name)
                    )
            return self.get_next_char(folio)
        return super(IRSequence, self)._next_do()

    def _get_number_next_actual(self):
        '''Return number from ir_sequence row when no_gap implementation,
        and number from postgres sequence when standard implementation.'''
        seq_dtes = self.filtered('is_dte')
        for seq in seq_dtes:
            folio = seq.get_folio(readonly=True)
            if folio == 0:
                folio = seq.number_next
            seq.number_next_actual = folio
        super(IRSequence, (self-seq_dtes))._get_number_next_actual()
