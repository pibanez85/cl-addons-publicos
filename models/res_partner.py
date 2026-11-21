import json
import logging
import re
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.translate import _

_logger = logging.getLogger(__name__)

CORREOS_MIPYME_SII = ("facturacionmipyme2@sii.cl", "facturacionmipyme@sii.cl")
_DUNS_RUT_PREFIX = "CLRUT:"
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

    def _get_default_tp_type(self):
        try:
            return self.env.ref("l10n_cl_fe.res_IVARI")
        except Exception as ex:
            _logger.error(tools.ustr(ex))
            return self.env["sii.responsability"]

    def _get_default_doc_type(self):
        try:
            return self.env.ref("l10n_cl_fe.dt_RUT")
        except Exception as ex:
            _logger.error(tools.ustr(ex))
            return self.env["sii.document_type"]

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

    def rut(self):
        rut = "66666666-6"
        if self.document_number and self.document_number not in [0, "0"]:
            d = re.sub("[^0-9Kk]", "", str(self.document_number)).upper()
            if len(d) > 1:
                rut = str(int(d[:-1])) + "-" + d[-1]
        return rut

    @api.model
    def _name_search(self, name, domain=None, operator='ilike', limit=100, order=None):
        domain = list(domain or [])
        if name:
            domain = ['|', ('document_number', operator, name)] + domain
        return super()._name_search(name, domain, operator, limit, order)

    def _address_fields(self):
        vals = super()._address_fields()
        vals.append('city_id')
        return vals

    def _prepare_display_address(self, without_company=False):
        address_format, args = super()._prepare_display_address(without_company=without_company)
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
            self.document_number = "{}.{}.{}-{}".format(
                document_number[0:2], document_number[2:5], document_number[5:8], document_number[-1],
            )
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

    @api.constrains("document_number", "document_type_id")
    def _check_document_number_cl(self):
        tipos = (self.env.ref("l10n_cl_fe.dt_RUT", raise_if_not_found=False), self.env.ref("l10n_cl_fe.dt_RUN", raise_if_not_found=False))
        for r in self:
            if r.document_number and r.document_type_id and r.document_type_id in tipos and not r.check_vat_cl(re.sub("[^0-9Kk]", "", r.document_number).zfill(9).upper()):
                raise ValidationError(_("RUT inválido: %s") % r.document_number)

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

    def check_vat_cl(self, vat):
        if not vat:
            return False
        vat = vat.replace('.', '').replace('-', '').upper()
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
        if not url or not token:
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
                    (self._cr.dbname, "res.partner", self.env.user.partner_id.id),
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
        rut = rut.zfill(9)
        if not self.check_vat_cl(rut):
            return False
        return "{}.{}.{}-{}".format(rut[0:2], rut[2:5], rut[5:8], rut[-1])

    @api.model
    def _sugerencia_autocomplete_rut(self, document_number, data):
        sugerencia = {
            "name": data["razon_social"],
            "description": " · ".join(filter(None, [document_number, data.get("glosa_giro")])),
            "document_number": document_number,
            "vat": "CL%s" % re.sub(r"[^0-9kK]", "", document_number).upper(),
            "country_code": "CL",
            "duns": "%s%s" % (_DUNS_RUT_PREFIX, document_number),
        }
        for origen, campo in (("direccion", "street"), ("telefono", "phone"), ("url", "website")):
            if data.get(origen):
                sugerencia[campo] = data[origen]
        if data.get("dte_email") and data["dte_email"].lower() not in CORREOS_MIPYME_SII:
            sugerencia["dte_email"] = data["dte_email"]
        if data.get("es_mipyme"):
            sugerencia["es_mipyme"] = True
        return self._iap_replace_location_codes(sugerencia)

    @api.model
    def _autocomplete_rut(self, query):
        document_number = self._rut_formateado(query)
        if not document_number:
            return []
        data = self.get_remote_user_data(document_number, process_data=False)
        if not data or not data.get("razon_social"):
            return []
        return [self._sugerencia_autocomplete_rut(document_number, data)]

    @api.model
    def autocomplete_by_name(self, query, query_country_id, timeout=15):
        if not self._busqueda_es_rut(query):
            return super().autocomplete_by_name(query, query_country_id, timeout=timeout)
        return self._autocomplete_rut(query)

    @api.model
    def autocomplete_by_vat(self, vat, query_country_id, timeout=15):
        if not self._busqueda_es_rut(vat):
            return super().autocomplete_by_vat(vat, query_country_id, timeout=timeout)
        return self._autocomplete_rut(vat)

    @api.model
    def enrich_by_duns(self, duns, timeout=15):
        if not (duns or "").startswith(_DUNS_RUT_PREFIX):
            return super().enrich_by_duns(duns, timeout=timeout)
        return (self._autocomplete_rut(duns[len(_DUNS_RUT_PREFIX):]) or [{}])[0]

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
            if not url or not token:
                continue
            to_sync.setdefault((url, token, i), {
                "token": token,
                "version": 2.0,
                "origen": self.env['ir.config_parameter'].sudo().get_param('web.base.url'),
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
                        (self._cr.dbname, "res.partner", self.env.user.partner_id.id),
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
