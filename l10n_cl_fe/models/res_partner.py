import json
import logging
import re
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.translate import _

_logger = logging.getLogger(__name__)

CORREOS_MIPYME_SII = ("facturacionmipyme2@sii.cl", "facturacionmipyme@sii.cl")
SRE_SESIONES = {}

try:
    import urllib3

    urllib3.disable_warnings()
    pool = urllib3.PoolManager()
    SRE_TIMEOUT = urllib3.Timeout(connect=3, read=5)
except ImportError:
    _logger.warning("no se ha cargado urllib3")


class ResPartner(models.Model):
    _inherit = "res.partner"
    # Odoo 20: la búsqueda por nombre usa _rec_names_search (ya no existe _name_search).
    _rec_names_search = ["complete_name", "email", "ref", "vat", "document_number"]

    def _get_default_tp_type(self):
        return self.env.ref("l10n_cl_fe.res_IVARI", raise_if_not_found=False) or self.env["sii.responsability"]

    def _get_default_doc_type(self):
        return self.env.ref("l10n_cl_fe.dt_RUT", raise_if_not_found=False) or self.env["sii.document_type"]

    @api.model
    def _get_default_country(self):
        return self.env.company.country_id.id or self.env.user.partner_id.country_id.id

    @api.depends("child_ids")
    def _compute_dte_email(self):
        for p in self:
            p.dte_email_id = self.env['res.partner']
            if p.dte_email == p.email:
                continue
            for dte in p.child_ids:
                if dte.type == "dte" and dte.principal:
                    p.dte_email_id = dte.id
                    p.dte_email = dte.email
                    break


    type = fields.Selection(selection_add=[("dte", "Contacto DTE"),])
    state_id = fields.Many2one("res.country.state", "Región",)
    responsability_id = fields.Many2one(
        "sii.responsability", string="Responsability", default=lambda self: self._get_default_tp_type(),
    )
    document_type_id = fields.Many2one(
        "sii.document_type", string="Document type", default=lambda self: self._get_default_doc_type(),
    )
    document_number = fields.Char(string="Document number", size=64,)
    start_date = fields.Date(string="Start-up Date",)
    tp_sii_code = fields.Char("Tax Payer SII Code", compute="_get_tp_sii_code", readonly=True,)
    activity_description = fields.Many2one("sii.activity.description", string="Glosa Giro", ondelete="restrict",)
    dte_email_id = fields.Many2one("res.partner", string="DTE Email Principal", compute="_compute_dte_email",)
    dte_email = fields.Char(
        string="DTE Email",
        # related='dte_email_id.name',
    )
    principal = fields.Boolean(string="Principal DTE", default=lambda self: self._verify_principal(),)
    send_dte = fields.Boolean(string="Auto Enviar DTE",)
    acteco_ids = fields.Many2many("partner.activities", string="Activities Names",)
    sync = fields.Boolean(string="syncred", default=False,)
    last_sync_update = fields.Datetime(string="Fecha Actualizado",)
    es_mipyme = fields.Boolean(string="Es MiPyme", help="Usa el sistema gratuito del SII")
    # Odoo 20 eliminó res.partner.company_type (is_company pasó a ser calculado desde el RUT/VAT).
    # Se conserva como alias de is_company para el código y las pruebas que lo usan.
    company_type = fields.Selection(
        [("person", "Individual"), ("company", "Company")],
        string="Company Type",
        compute="_compute_company_type",
        inverse="_inverse_company_type",
    )

    @api.depends("is_company")
    def _compute_company_type(self):
        for partner in self:
            partner.company_type = "company" if partner.is_company else "person"

    def _inverse_company_type(self):
        for partner in self:
            partner.is_company = partner.company_type == "company"

    def rut(self):
        rut = "66666666-6"
        if self.document_number and self.document_number not in [0, "0"]:
            d = re.sub("[^0-9Kk]", "", str(self.document_number)).upper()
            if len(d) > 1:
                rut = str(int(d[:-1])) + "-" + d[-1]
        return rut

    def _address_fields(self):
        vals = super()._address_fields()
        vals.append('city_id')
        return vals

    def _prepare_display_address(self, without_name=False):
        address_format, args = super()._prepare_display_address(without_name=without_name)
        args['comuna_name'] = self.city_id.name or ''
        return address_format, args

    @api.onchange("dte_email")
    def set_temporal_email_cambiar_a_related(self):
        """ Esto eliminar en la versión siguiente, es solamente para evitar
            problemas al actualizar """
        if self.dte_email == self.email:
            self.send_dte = True
            self.principal = True
            return
        if self.dte_email_id and self.dte_email_id.email != self.dte_email:
            __name = self.dte_email_id.name
            if __name == self.dte_email_id.email:
                __name = self.dte_email
            self.dte_email_id.name = __name
            self.dte_email_id.email = self.dte_email

    @api.onchange("principal")
    def verify_principal(self):
        self._verify_principal()

    def _verify_principal(self):
        another = False
        if self.type != "dte":
            return another
        check_id = self.id
        if self.parent_id:
            check_id = self.parent_id.id
        another = self.env["res.partner"].search([("parent_id", "=", check_id), ("principal", "=", True)])
        if another:
            raise UserError(_("Existe otro correo establecido como Principal"))
        return True

    # def create(self, vals):
    #    partner = super(ResPartner, self).create(vals)
    #    if vals.get('dte_email'):
    #        dte_email_id = self.env['res.partner'].create(
    #                              {
    #                                  'parent_id': self.id,
    #                                  'type': 'dte',
    #                                  'name': self.dte_email,
    #                                  'email': self.dte_email,
    #                                  'send_dte': True,
    #                                  'principal': True,
    #                              })
    #        self.dte_email_id = dte_email_id.id


    @api.onchange("responsability_id")
    def _get_tp_sii_code(self):
        for record in self:
            record.tp_sii_code = str(record.responsability_id.tp_sii_code)

    @api.onchange("document_number", "document_type_id")
    def onchange_document(self):
        if self.document_number and (self.document_type_id == self.env.ref("l10n_cl_fe.dt_RUT")
            or self.document_type_id == self.env.ref("l10n_cl_fe.dt_RUN")):
            document_number = (re.sub("[^1234567890Kk]", "", str(self.document_number))).zfill(9).upper()
            if not self.check_vat_cl(document_number):
                return {"warning": {"title": _("Rut Erróneo"), "message": _("Rut Erróneo"),}}
            vat = "CL%s" % document_number
            exist = self.env["res.partner"].search(
                [
                    ("vat", "=", vat),
                    ("vat", "!=", "CL555555555"),
                    ("commercial_partner_id", "!=", self.commercial_partner_id.id),
                ],
                limit=1,
            )
            if exist:
                self.vat = ""
                self.document_number = ""
                return {
                    "warning": {
                        "title": "Informacion para el Usuario",
                        "message": _("El usuario %s está utilizando este documento") % exist.name,
                    }
                }
            self.vat = vat
            self.document_number = self._formatear_rut(document_number)
        elif self.document_number and self.document_type_id == self.env.ref("l10n_cl_fe.dt_Sigd"):
            self.document_number = ""
        else:
            self.vat = ""
        self.fill_partner()

    @api.onchange("city_id")
    def _onchange_city_id(self):
        if self.city_id:
            self.country_id = self.city_id.state_id.country_id.id
            self.state_id = self.city_id.state_id.id
            self.city = self.city_id.name

    @api.model
    def _rut_clave(self, valor):
        return re.sub(r"[^0-9kK]", "", str(valor or "")).upper().lstrip("0")

    @api.model
    def _vats_busqueda(self, valor):
        clave = self._rut_clave(valor)
        if not clave:
            return []
        formas = ["CL%s" % clave.zfill(9), "CL%s" % clave]
        if clave.endswith("K"):
            formas += [forma[:-1] + "k" for forma in formas]
        return list(dict.fromkeys(formas))

    @api.model
    def _buscar_por_vat(self, valor, dominio=None):
        vats = self._vats_busqueda(valor)
        if not vats:
            return self.browse()
        socios = self.search([("vat", "in", vats)] + list(dominio or []))
        for vat in vats:
            socio = socios.filtered(lambda s: s.vat == vat)[:1]
            if socio:
                return socio
        return self.browse()

    @api.model
    def _formatear_rut(self, valor):
        """Forma canónica con que se guarda un RUT/RUN en ``document_number``: puntos de miles,
        guion y dígito verificador en mayúscula, sin ceros a la izquierda (``76.086.428-5``,
        ``10.000.013-K``, ``1.234.567-4``). Es la forma que ya dejaban el onchange y las pruebas de
        upstream; el XML del DTE no la usa tal cual: ``rut()`` (y ``formatear_rut`` de
        facturacion_electronica) la reducen a ``76086428-5``. ``False`` si no parece un RUT."""
        rut = re.sub(r"[^0-9K]", "", str(valor or "").upper())
        if len(rut) < 2 or not rut[:-1].isdigit() or not int(rut[:-1]):
            return False
        return "{:,}-{}".format(int(rut[:-1]), rut[-1]).replace(",", ".")

    @api.model
    def _tipos_documento_rut(self):
        return (self.env.ref("l10n_cl_fe.dt_RUT", raise_if_not_found=False) or self.env["sii.document_type"]) | (
            self.env.ref("l10n_cl_fe.dt_RUN", raise_if_not_found=False) or self.env["sii.document_type"])

    @api.model
    def _normalizar_document_number(self, valor, tipo_id):
        if valor and tipo_id in self._tipos_documento_rut().ids:
            return self._formatear_rut(valor) or valor
        return valor

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("document_number"):
                tipo = vals["document_type_id"] if "document_type_id" in vals else \
                    self.default_get(["document_type_id"]).get("document_type_id")
                vals["document_number"] = self._normalizar_document_number(vals["document_number"], tipo)
        return super().create(vals_list)

    def write(self, vals):
        if not vals.get("document_number"):
            return super().write(vals)
        if "document_type_id" in vals:
            return super().write(dict(vals, document_number=self._normalizar_document_number(
                vals["document_number"], vals["document_type_id"])))
        for tipo, socios in self.grouped("document_type_id").items():
            super(ResPartner, socios).write(dict(vals, document_number=self._normalizar_document_number(
                vals["document_number"], tipo.id)))
        return True

    @api.constrains("document_number", "document_type_id")
    def _check_document_number_cl(self):
        # _formatear_rut exige un número distinto de 0 antes del dígito verificador: texto sin
        # dígitos ("N/A") o ceros ("0-0") quedaban como 000000000, que tiene DV 0 y pasaba.
        # Sin RUT se deja vacío o se usa el tipo "Sin identificación" (par_cfa guarda "0").
        tipos = self._tipos_documento_rut()
        for r in self:
            if not r.document_number or r.document_type_id not in tipos:
                continue
            rut = self._formatear_rut(r.document_number)
            if not rut or not r.check_vat_cl(re.sub("[^0-9K]", "", rut).zfill(9)):
                raise ValidationError(_(
                    "RUT inválido: «%s». Ingrese el RUT con su dígito verificador, por ejemplo "
                    "76.086.428-5, o deje el campo vacío si el contacto no tiene RUT.",
                    r.document_number,
                ))

    @api.constrains("vat", "commercial_partner_id")
    def _rut_unique(self):
        for r in self.sudo():
            if not r.vat or r.parent_id:
                continue
            partner = self.env["res.partner"].sudo().search(
                [("vat", "=", r.vat), ("id", "!=", r.id), ("commercial_partner_id", "!=", r.commercial_partner_id.id),]
            )
            if r.vat != "CL555555555" and partner:
                raise UserError(_("El rut: %s debe ser único") % r.vat)
                return False

    def format_vat_cl(self, vat):
        """Odoo 20 normaliza el VAT al guardarlo (res.partner._inverse_vat) y para Chile lo deja como
        ``12345678-5``. Este módulo guarda y busca el RUT como ``CL`` + 9 dígitos (``CL123456785``)."""
        clave = re.sub(r"[^0-9K]", "", re.sub(r"^\s*CL", "", (vat or "").upper()))
        return "CL%s" % clave.zfill(9) if clave else vat

    def check_vat_cl(self, vat):
        if not vat:
            return False
        vat = vat.replace('.', '').replace('-', '').upper()
        if vat.startswith('CL'):
            vat = vat[2:]
        if len(vat) < 8 or len(vat) > 9:
            return False
        vat = vat.zfill(9)
        body, vdig = vat[:-1], vat[-1]
        try:
            vali = list(range(2, 8)) + [2, 3]
            operar = "0123456789K0"[11 - (sum([int(digit) * factor for digit, factor in zip(body[::-1], vali)]) % 11)]
            return operar == vdig
        except (IndexError, ValueError):
            return False

    @api.onchange('vat', 'document_number')
    def restrict_vat(self):
        if self._origin.vat != self.vat and self.env['account.move'].search([
            ('commercial_partner_id', '=', self.id),
            ('sii_xml_dte', '!=', False),
        ]):
            raise UserError("No se puede editar un RUT de este partner ya que tiene DTE emitidos. Por favor crear otra ficha de partner para este RUT")

    def _process_data(self, data=None):
        if data is None:
            data = {}
        self.es_mipyme = data.get("es_mipyme", False)
        if data.get("razon_social"):
            self.name = data["razon_social"]
        if data.get("dte_email") and data["dte_email"].lower() not in CORREOS_MIPYME_SII:
            self.dte_email = data["dte_email"]
        if data.get("email"):
            self.name = data["email"]
        if data.get("telefono"):
            self.name = data["phone"]
        if data.get("direccion"):
            self.street = data["direccion"]
        if data.get("actecos"):
            acs = self.env["partner.activities"].sudo().search([("code", "in", data["actecos"])])
            self.acteco_ids = acs
        if data.get("glosa_giro"):
            query = [("name", "=", data.get("glosa_giro"))]
            ad = self.env["sii.activity.description"].search(query)
            if not ad:
                ad = self.env["sii.activity.description"].create({"name": data.get("glosa_giro")})
            self.activity_description = ad.id
        if data.get("url"):
            self.website = data["url"]
        if data.get("logo"):
            self.image_1920 = data["logo"]
        self.sync = True
        if not self.document_number:
            self.document_number = data["rut"]
        self.last_sync_update = data["actualizado"]

    def get_data_to_put(self):
        if self.document_number in [False, 0, "0"]:
            return {}
        return {
            "rut": self.document_number,
            "glosa_giro": self.activity_description.name,
            "razon_social": self.name,
            "dte_email": self.dte_email,
            "email": self.email,
            "direccion": self.street,
            # 'comuna': self.
            "telefono": self.phone,
            "actecos": [ac.code for ac in self.acteco_ids],
            "url": self.website,
            "logo": self.image_1920.decode() if self.image_1920 else False,
        }

    def _sre_post(self, company, body):
        if not company.sync_remote_partners:
            raise UserError(_("La consulta a sre.cl no está habilitada para %s") % company.name)
        clave = (company.url_remote_partners, company.token_remote_partners)
        body = dict(body, token=company.token_remote_partners)
        if SRE_SESIONES.get(clave):
            body["sesion"] = SRE_SESIONES[clave]
        resp = pool.request(
            "POST",
            company.url_remote_partners,
            body=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            timeout=SRE_TIMEOUT,
            retries=False,
        )
        if resp.status == 200:
            data = json.loads(resp.data.decode("ISO-8859-1"))
            if isinstance(data, dict) and data.get("sesion"):
                SRE_SESIONES[clave] = data["sesion"]
        return resp

    def get_remote_user_data(self, to_check, process_data=True):
        company = self.company_id or self.env.company
        url = company.url_remote_partners
        token = company.token_remote_partners
        # Privacidad: el RUT del contacto sale a sre.cl solo si la compañía lo habilitó.
        if not company.sync_remote_partners or not url or not token:
            return
        if to_check in [False, 0, "0"]:
            return
        try:
            resp = self._sre_post(company, {"rut": to_check, "version": 1.0})
            message = ""
            title = "Advertencia"
            if resp.status != 200:
                _logger.info("Error en conexión al obtener partners %s" % resp.data)
                title = "Error en conexión al obtener partners"
                process_data = False
                if resp.status == 403:
                    data = json.loads(resp.data.decode("ISO-8859-1"))
                    message = data["message"]
                else:
                    message = str(resp.data)
            else:
                data = json.loads(resp.data.decode("ISO-8859-1"))
                message = data.get('message')
            if message:
                _logger.info(resp.data, exc_info=True)
                """
                self.env["bus.bus"].sendone(
                    (self.env.cr.dbname, "res.partner", self.env.user.partner_id.id),
                    {
                        "title": title,
                        "message": message,
                        "url": "res_config",
                        "type": "dte_notif",
                    },
                )"""
            if not process_data:
                return data
            if not data:
                self.sync = False
                return
            self._process_data(data)
        except:
            _logger.info("Error en get remote partner", exc_info=True)

    @api.model
    def _busqueda_es_rut(self, query):
        texto = (query or "").strip()
        return bool(texto) and not re.sub(r"[0-9kK.\- ]", "", texto)

    @api.model
    def _rut_formateado(self, query):
        rut = re.sub(r"[^0-9kK]", "", query or "").upper()
        if not 8 <= len(rut) <= 9:
            return False
        if not self.check_vat_cl(rut):
            return False
        return self._formatear_rut(rut)

    @api.onchange("name")
    def fill_partner(self):
        if self.sync:
            return
        if self.document_number and self.check_vat_cl(self.document_number.replace(".", "").replace("-", "")):
            self.get_remote_user_data(self.document_number)
        elif self.name and self.check_vat_cl(self.name.replace(".", "").replace("-", "")):
            self.get_remote_user_data(self.name)

    @api.model
    def _check_need_update(self, groups=100):
        to_sync = {}
        rut_sync = {}
        recs = self.search([("document_number", "not in", [False, 0]), ("parent_id", "=", False)])
        multi = len(recs) > 1
        i = 0
        for r in recs:
            company = r.company_id or self.env.company
            url = company.url_remote_partners
            token = company.token_remote_partners
            if not company.sync_remote_partners or not url or not token:
                continue
            to_sync.setdefault((url, token, i), {
                "token": token,
                "version": 2.0,
                "origen": self.env['ir.config_parameter'].sudo().get_str('web.base.url'),
            })
            if company.sync_remote_partners:
                data = r.get_data_to_put()
                if not data:
                    continue
                rut_sync[data['rut']] = r
                if multi:
                    to_sync[(url, token, i)].setdefault("ruts", {})
                    to_sync[(url, token, i)]["ruts"][r.document_number] = data
                    if len(to_sync[(url, token, i)]["ruts"]) == groups:
                        i+=1
                else:
                    to_sync[(url, token, i)].update(data)
        for k, data in to_sync.items():
            try:
                if multi:
                    ruts = {}
                    for r, rut_data in data['ruts'].items():
                        ruts[r] = {
                            "rut": rut_data['rut'],
                            "actualizado": str(rut_sync[rut_data['rut']].last_sync_update),
                        }
                    resp = pool.request(
                        "POST", k[0],
                        body=json.dumps({
                            "ruts": ruts,
                            "token": k[1],
                            "version": 2.0,
                        }).encode("utf-8"),
                        headers={"Content-Type": "application/json"}
                    )
                else:
                    resp = pool.request(
                        "GET", k[0], {
                            "token": k[1],
                            "version": 2.0,
                            "rut": data['rut'],
                            "actualizado": rut_sync[data['rut']].last_sync_update,
                        }
                    )
                message = ""
                title = "Advertencia"
                if resp.status != 200:
                    _logger.info("Error en conexión al consultar partners %s" % resp.data)
                    title = "Error en conexión al consultar partners"
                    if resp.status == 403:
                        data = json.loads(resp.data.decode("ISO-8859-1"))
                        message = data["message"]
                    else:
                        message = str(resp.data)
                else:
                    data = json.loads(resp.data.decode("ISO-8859-1"))
                    message = data.get('message')
                    result = data.get("result", False)
                    if result:
                        if multi:
                            for rut, valores in result['ruts'].items():
                                if valores['result']:
                                    r = rut_sync[rut]
                                    r._process_data(valores)
                        else:
                            r = rut_sync[data.get("rut", False)]
                            r._process_data(result)

                if message:
                    _logger.info(message, exc_info=True)
                    """
                    self.env["bus.bus"].sendone(
                        (self.env.cr.dbname, "res.partner", self.env.user.partner_id.id),
                        {
                            "title": title,
                            "message": message,
                            "url": "res_config",
                            "type": "dte_notif",
                        },
                    )
                    """
            except:
                _logger.info("Error en need_update", exc_info=True)
