import logging
from datetime import date
import pytz
import ast
from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.translate import _

from .binario import BinaryBytes
from .sii_firma import GRUPOS_SECRETOS

_logger = logging.getLogger(__name__)

CAF_DIAS_AVISO_DEFECTO = 30
CAF_DIAS_AVISO_INTENSIVO_DEFECTO = 3

try:
    import base64
except ImportError:
    pass


class CAF(models.Model):
    _name = "dte.caf"
    _inherit = ['mail.thread']
    _description = "Archivo CAF"

    @api.onchange('start_nm', 'final_nm', 'folio_actual')
    @api.depends('start_nm', 'final_nm', 'folio_actual')
    def _get_qty_available(self):
        for r in self:
            if r.state not in ['draft', 'spent']:
                folio = r._peek_folio_actual()
                qty_available = max(0, (r.final_nm + 1) - max(folio, r.start_nm))
                if qty_available != r.qty_available:
                    r.qty_available = qty_available
                continue
            r.qty_available = 0

    def _get_tables(self):
        return ['account_move']

    def _company_folios(self):
        company = self.company_id or self.sequence_id.company_id
        if not company or not self.rut_n:
            return self.env['res.company']
        if company.partner_id.rut() != self.rut_n:
            _logger.warning(
                "CAF %s: el RUT %s no es el de la compañía %s, los folios se cuentan sin filtrar",
                self.id, self.rut_n, company.display_name)
            return self.env['res.company']
        return company

    def _company_where_string_and_param(self, alias=''):
        company = self._company_folios()
        if not company:
            return '', {}
        return (
            '            AND %scompany_id = %%(company_id)s\n' % (alias and alias + '.' or ''),
            {'company_id': company.id},
        )

    def _account_move_where_string_and_param(self):
        where_string = """WHERE
            state IN ('posted', 'cancel')
            AND document_class_id = %(document_class_id)s
            AND use_documents
        """
        param = {
            'document_class_id': self.document_class_id.id
        }
        company_where, company_param = self._company_where_string_and_param()
        where_string += company_where
        param.update(company_param)
        return where_string, param

    def _peek_folio_actual(self):
        """Calcula el siguiente folio según el último documento usado.

        Pure: solo lee account_move/folios_anulados/folios_vencidos.
        No escribe state ni folio_actual. Retorna self.final_nm + 1 cuando el
        rango está agotado, anulado-overflow o vencido (sentinela > final_nm).
        Cualquier valor en [start_nm, final_nm] es un folio usable."""
        folio = 0
        if not self.document_class_id:
            return folio
        tables = self._get_tables()
        where_clauses = []
        param = {}
        for table in tables:
            where_string, params = getattr(self, "_%s_where_string_and_param" % table)()
            param.update(params)
            where_clauses.append("""
                    SELECT MAX({field}) AS folio FROM {table}
                    {where_string}
                    AND {field} >= {start_nm} AND {field} <= {final_nm}
            """.format(
                table=table,
                where_string=where_string,
                field='sii_document_number',
                start_nm=self.start_nm,
                final_nm=self.final_nm,
            ))
        if not where_clauses:
            return 0
        union = 'UNION ALL '.join(where_clauses)
        query = 'SELECT MAX(folio) FROM ({union}) AS combined'.format(
            union=union,
        )
        self.env.cr.execute(query, param)
        folio = int((self.env.cr.fetchone() or [None])[0] or 0)
        exhausted = self.final_nm + 1
        if self.start_nm <= folio < self.final_nm:
            folios_anulados = ast.literal_eval(self.folios_anulados or '[]')
            def check_anulado(f):
                f += 1
                if f in folios_anulados:
                    return check_anulado(f)
                if f > self.final_nm:
                    return exhausted
                return f
            folio = check_anulado(folio)
            if folio > self.final_nm:
                return exhausted
            if self.document_class_id.es_factura_afecta() or self.document_class_id.es_nc() or self.document_class_id.es_nd():
                folios_vencidos = ast.literal_eval(self.folios_vencidos or '[]')
                tz = pytz.timezone("America/Santiago")
                if folio in folios_vencidos or fields.Date.context_today(self.with_context(tz=tz)) >= self.expiration_date:
                    return exhausted
            return folio
        if folio > 0:
            return exhausted
        return self.start_nm

    def _get_folio_actual(self):
        folio = self._peek_folio_actual()
        if folio > self.final_nm:
            self._mark_spent()
            return self.final_nm
        return folio

    def _param_dias(self, clave, defecto):
        valor = self.env["ir.config_parameter"].sudo().get_str(clave, defecto)
        try:
            return max(0, int(valor))
        except (TypeError, ValueError):
            _logger.warning("Valor inválido en %s: %r", clave, valor)
            return defecto

    def _debe_notificar_vencimiento(self, days_left):
        self.ensure_one()
        if days_left <= self._param_dias(
                "l10n_cl_fe.caf_dias_aviso_intensivo", CAF_DIAS_AVISO_INTENSIVO_DEFECTO):
            return True
        Param = self.env["ir.config_parameter"].sudo()
        clave = "l10n_cl_fe.caf_notif.%s.%s" % (self.id, self.env.uid)
        hoy = fields.Date.to_string(fields.Date.context_today(self))
        try:
            if Param.get_str(clave) == hoy:
                return False
        except Exception as e:
            _logger.debug("No se pudo leer %s: %s", clave, e)
        try:
            Param.set_str(clave, hoy)
        except Exception as e:
            _logger.debug("No se pudo registrar %s: %s", clave, e)
        return True

    def _mensaje_vencimiento(self, days_left):
        self.ensure_one()
        if days_left < 0:
            cuando = _("venció hace %s días") % abs(days_left)
        elif days_left == 0:
            cuando = _("vence hoy")
        else:
            cuando = _("vence en %s días") % days_left
        return _(
            "El CAF de %(doc)s (folios %(desde)s-%(hasta)s) %(cuando)s, el "
            "%(fecha)s. Quedan %(sin_usar)s folios sin usar. ⚠ Los folios no "
            "utilizados a esa fecha deben anularse en el SII y ya no podrán "
            "emitirse."
        ) % {
            "doc": self.document_class_id.name or "-",
            "desde": self.start_nm,
            "hasta": self.final_nm,
            "cuando": cuando,
            "fecha": self.expiration_date.strftime("%d-%m-%Y"),
            "sin_usar": self.cantidad_folios_sin_usar,
        }

    def notify_use_expiration(self):
        self.ensure_one()
        if not self.expiration_date:
            return
        days_left = (self.expiration_date - fields.Date.context_today(self)).days
        if days_left > self._param_dias(
                "l10n_cl_fe.caf_dias_aviso", CAF_DIAS_AVISO_DEFECTO):
            return
        if not self._debe_notificar_vencimiento(days_left):
            return
        intensivo = days_left <= self._param_dias(
            "l10n_cl_fe.caf_dias_aviso_intensivo", CAF_DIAS_AVISO_INTENSIVO_DEFECTO)
        try:
            self.env["bus.bus"]._sendone(
                self.env.user.partner_id,
                "simple_notification",
                {
                    "title": _("Folios por vencer"),
                    "message": self._mensaje_vencimiento(days_left),
                    "type": "danger" if intensivo else "warning",
                    "sticky": True,
                },
            )
        except Exception as e:
            _logger.warning("No se pudo enviar toast de CAF por vencer: %s", e)

    def _is_folio_used(self, folio):
        if not self.document_class_id or not (self.start_nm <= folio <= self.final_nm):
            return False
        tables = self._get_tables()
        where_clauses = []
        param = {}
        for table in tables:
            where_string, params = getattr(self, "_%s_where_string_and_param" % table)()
            where_clauses.append("""(
                SELECT 1 FROM {table}
                {where_string}
                AND {field} = %(folio)s
                LIMIT 1
            )""".format(table=table, where_string=where_string, field='sii_document_number'))
            param.update(params)
        if not where_clauses:
            return False
        param['folio'] = folio
        union = ' UNION ALL '.join(where_clauses)
        self.env.cr.execute('SELECT 1 FROM ({}) AS combined LIMIT 1'.format(union), param)
        return bool(self.env.cr.fetchone())

    def _mark_spent(self):
        if self.state == 'spent' and self.qty_available == 0:
            return
        self.write({"state": 'spent', 'qty_available': 0})

    def compute_folio_actual(self):
        folio_actual = self._get_folio_actual()
        if folio_actual != self.folio_actual:
            self.folio_actual = folio_actual

    @api.onchange('caf_file', 'caf_string')
    @api.depends('caf_file', 'caf_string')
    def _load_data(self):
        for r in self:
            r.issued_date = False
            r.expiration_date = False
            r.document_class_id = False
            r.start_nm = False
            r.final_nm = False
            r.rut_n = False
            if r.caf_file:
                r.load_caf()

    name = fields.Char(string="Nombre", readonly=True, related="filename",)
    filename = fields.Char(string="File Name", required=True,)
    # El CAF trae la llave privada RSA (RSASK) con la que se timbra el TED: solo contadores y
    # administradores lo leen; el timbraje lo lee con sudo (dte.mixin._caf_file, load_caf y el
    # _caf_file de la boleta voucher). decode_caf/ir.sequence.get_caf_file son públicos: sin sudo.
    caf_file = fields.Binary(string="CAF XML File", help="Upload the CAF XML File in this holder",
                             groups=GRUPOS_SECRETOS)
    caf_string = fields.Text(string="Archivo CAF", groups=GRUPOS_SECRETOS)
    issued_date = fields.Date(string="Issued Date", compute='_load_data', store=True)
    expiration_date = fields.Date(string="Expiration Date", compute='_load_data', store=True)
    document_class_id = fields.Many2one(
        'sii.document_class',
        string="SII Document Class",
        compute='_load_data', store=True
    )
    start_nm = fields.Integer(
        string="Start Number",
        help="CAF Starts from this number", compute='_load_data', store=True
    )
    final_nm = fields.Integer(
        string="End Number",
        help="CAF Ends to this number", compute='_load_data', store=True
    )
    state = fields.Selection(
        [("draft", "Draft"), ("in_use", "In Use"), ("spent", "Spent"),],
        string="Status",
        default="draft",
        help="""Draft: means it has not been used yet. You must put in in used
in order to make it available for use. Spent: means that the number interval
has been exhausted.""",
    )
    rut_n = fields.Char(string="RUT", compute='_load_data', store=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=False, default=lambda self: self.env.company,
    )
    sequence_id = fields.Many2one("ir.sequence", string="Sequence",
        domain="[('is_dte', '=', True)]")
    use_level = fields.Float(string="Use Level", compute="_used_level",)
    folio_actual = fields.Integer(
        string="Folio Actual",
        default=_get_folio_actual,
        )
    cantidad_folios = fields.Integer(
        string="Cantidad de Folios",
        compute='_get_cantidad_folios')
    qty_available = fields.Integer(
        string="Cantidad Disponible",
        compute="_get_qty_available",
        store=True,
        )
    cantidad_usados = fields.Integer(
        string="Cantidad de Folios Usados",
        compute='_get_usados')
    cantidad_folios_sin_usar = fields.Integer(
        string="Cantidad folios sin usar",
        default=0)
    folios_sin_usar = fields.Text(
        string="Folios sin usar")
    cantidad_folios_anulados = fields.Integer(
        string="Cantidad folios anulados",
        default=0)
    folios_anulados = fields.Text(
        string="Folios anulados")
    cantidad_folios_vencidos_sin_anular = fields.Integer(
        string="Cantidad folios vencidos sin anular",
        default=0)
    cantidad_folios_vencidos = fields.Integer(
        string="Cantidad folios vencidos",
        default=0)
    folios_vencidos = fields.Text(
        string="Folios Vencidos")
    _filename_unique = models.Constraint("unique(filename)", "Error! Filename Already Exist!")
    _order = "start_nm DESC"

    def obtener_folios_sin_usar(self):
        if not self.folios_sin_usar:
            return []
        return ast.literal_eval(self.folios_sin_usar)

    def ingresar_folio_sin_usar(self, folio):
        folios = self.obtener_folios_sin_usar()
        if folio not in folios:
            folios.append(folio)
        self.folios_sin_usar = str(sorted(folios))
        self.cantidad_folios_sin_usar += 1

    def eliminar_folio_sin_usar(self, folio):
        folios_sin_usar = self.obtener_folios_sin_usar()
        if folio in folios_sin_usar:
            folios_sin_usar.remove(folio)
        self.folios_sin_usar = str(folios_sin_usar)

    def _join_inspeccionar(self):
        join = f'LEFT JOIN account_move a ON s = a.sii_document_number AND a.document_class_id = {self.document_class_id.id} AND a.use_documents'
        company = self._company_folios()
        if company:
            join += f' AND a.company_id = {company.id}'
        return join

    def _where_inspeccionar(self):
        return 'a.sii_document_number is null'

    def inspeccionar_folios_sin_usar(self):
        joins = self._join_inspeccionar()
        wheres = self._where_inspeccionar()
        self.env.cr.execute("SELECT s FROM generate_series({0},{1},1) s {2} WHERE {3}".format(
            self.start_nm,
            self.folio_actual,
            joins,
            wheres,
        ))
        folios = sorted([x[0] for x in self.env.cr.fetchall()])
        if self.folio_actual in folios:
            folios.remove(self.folio_actual)
        self.write({
            'folios_sin_usar': str(folios),
            'cantidad_folios_sin_usar': len(folios)
            })

    def load_caf(self):
        secreto = self.sudo()
        if not secreto.caf_file and not secreto.caf_string:
            return
        if not self.sequence_id:
            context = dict(self.env.context or {})
            self.sequence_id = context.get("default_sequence_id", False)
        if not secreto.caf_file:
            secreto.caf_file = BinaryBytes(secreto.caf_string.encode('ISO-8859-1'))
        if not secreto.caf_string:
            secreto.caf_string = secreto.caf_file.content.decode("ISO-8859-1")
        result = secreto.decode_caf().find("CAF/DA")
        self.start_nm = result.find("RNG/D").text
        self.final_nm = result.find("RNG/H").text
        dc = self.env["sii.document_class"].search([("sii_code", "=", result.find("TD").text)])
        self.document_class_id = dc.id
        fa = result.find("FA").text
        self.issued_date = fa
        if dc.es_factura_afecta() or dc.es_nc() or dc.es_nd() or dc.es_liquidacion() or dc.es_factura_compra():
            self.expiration_date = date(int(fa[:4]), int(fa[5:7]), int(fa[8:10])) + relativedelta(months=6)
        self.rut_n = result.find("RE").text
        if self.rut_n != self.company_id.partner_id.rut():
            raise UserError(
                _("Company vat %s should be the same that assigned company's vat: %s!")
                % (self.rut_n, self.company_id.partner_id.rut())
            )
        elif dc != self.sequence_id.sii_document_class_id:
            raise UserError(
                _(
                    """SII Document Type for this CAF is %s and selected sequence
associated document class is %s. This values should be equal for DTE Invoicing
to work properly!"""
                )
                % (self.document_class_id.sii_code, self.sequence_id.sii_document_class_id.sii_code)
            )
        self.state = "in_use"
        self.compute_folio_actual()

    def _get_cantidad_folios(self):
        for r in self:
            if r.state == 'draft':
                r.cantidad_folios = 0
            else:
                r.cantidad_folios = ((r.final_nm +1) - r.start_nm)

    def _get_usados(self):
        for r in self:
            r.cantidad_usados = r.cantidad_folios - r.qty_available

    def _used_level(self):
        for r in self:
            r.use_level = 0.0
            if r.cantidad_folios > 0:
                r.use_level = float(100 * r.cantidad_usados) / r.cantidad_folios

    def decode_caf(self):
        # Sin sudo: es público (RPC). Quien timbra lo llama sobre el CAF con sudo.
        return etree.fromstring(self.caf_string)

    def _contenido_caf_b64(self):
        """Archivo CAF en base64 (lo que espera facturacion_electronica), leído con sudo."""
        self.ensure_one()
        caf = self.sudo()
        if caf.caf_file:
            return caf.caf_file.to_base64()
        return base64.b64encode((caf.caf_string or "").encode("ISO-8859-1")).decode()

    def expirar_folios(self):
        self.inspeccionar_folios_sin_usar()
        folios_vencidos = ast.literal_eval(self.folios_sin_usar)
        if not folios_vencidos:
            return
        self.write({
            'folios_sin_usar': '[]',
            'cantidad_folios_sin_usar': 0,
            'qty_available': 0,
            'folios_vencidos': str(folios_vencidos),
            'cantidad_folios_vencidos': len(folios_vencidos),
            'cantidad_folios_vencidos_sin_anular': len(folios_vencidos)
            })

    def obtener_folios_anulados(self):
        if not self.folios_anulados:
            return []
        return ast.literal_eval(self.folios_anulados)

    def ingresar_folio_anulado(self, folio):
        folios = self.obtener_folios_anulados()
        if folio not in folios:
            folios.append(folio)
        self.folios_anulados = str(sorted(folios))
        self.cantidad_folios_anulados += 1

    def eliminar_folio_anulado(self, folio):
        folios = self.obtener_folios_anulados()
        if folio in folios:
            folios.remove(folio)
        self.folios_anulados = str(sorted(folios))

    def anular_folio(self, desde, hasta=False):
        if not hasta:
            hasta = desde
        wiz = self.env["dte.caf.apicaf"].create({
            'operacion': 'anular',
            'company_id': self.company_id.id,
            'firma': self.env.user.get_digital_signature(self.company_id).id,
        })
        linea = False
        for l in wiz.lineas_disponibles:
            if l.inicial <= desde <= l.final:
                linea = l
                break
        if not linea:
            raise UserError(_("No se encontró una línea disponible para los folios especificados."))
        linea.selected = True
        wiz.write({
            'folio_ini': desde,
            'folio_fin': hasta,
            'motivo': "Vencido",
        })
        wiz.obtener_caf()
        wiz.confirmar()
        folios_anulados = hasta + 1 - desde
        self.cantidad_folios_anulados += folios_anulados

    def after_anular_folios(self, inicio, final):
        folios_vencidos = ast.literal_eval(self.folios_vencidos or '[]')
        folios_sin_usar = self.obtener_folios_sin_usar()
        for r in range(inicio, final+1):
            if r in folios_vencidos:
                folios_vencidos.remove(r)
            if r in folios_sin_usar:
                self.eliminar_folio_sin_usar(r)
        self.write({
            'folios_vencidos': str(folios_vencidos),
            'cantidad_folios_vencidos': len(folios_vencidos),
            'cantidad_folios_vencidos_sin_anular': len(folios_vencidos)
            })

    def auto_anular(self):
        for record in self:
            if record.folios_vencidos in [False, '[]']:
                _logger.info(f"No hay folios vencidos para el CAF {record.id}.")
                continue
            try:
                folios = ast.literal_eval(record.folios_vencidos)
            except Exception as e:
                _logger.error(f"Error al parsear folios vencidos para el CAF {record.id}: {e}")
                continue
            if not folios:
                _logger.info(f"La lista de folios vencidos está vacía para el CAF {record.id}.")
                continue
            folios = sorted(folios)
            ranges = []
            start = folios[0]
            end = start
            for folio in folios[1:]:
                if folio == end + 1:
                    end = folio
                else:
                    ranges.append((start, end))
                    start = folio
                    end = folio
            ranges.append((start, end))
            for (desde, hasta) in ranges:
                try:
                    record.anular_folio(desde, hasta)
                    _logger.info(
                        f"Folios anulados automáticamente desde {desde} hasta {hasta} para CAF {record.id}."
                    )
                    record.message_post(
                        body=_("Folios anulados automáticamente desde %s hasta %s.") % (desde, hasta),
                        message_type='notification',
                    )
                except Exception as e:
                    _logger.error(
                        f"Error al anular folios desde {desde} hasta {hasta} para CAF {record.id}: {e}"
                    )
                    record.message_post(
                        body=_("Error al anular folios desde %s hasta %s: %s") % (desde, hasta, str(e)),
                        message_type='notification',
                    )
