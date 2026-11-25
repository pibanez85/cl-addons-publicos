import base64
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import urllib3

    urllib3.disable_warnings()
    pool = urllib3.PoolManager(timeout=10.0)
except ImportError:
    _logger.warning("Problemas con urllib3")


class APICAFDocs(models.TransientModel):
    _name = "dte.caf.apicaf.docs"
    _description = "Líneas de archivos disponibles para remitir folios"

    def get_name(self):
        for r in self:
            name = "{}, {}, {}-{}".format(r.fecha, r.cantidad, r.inicial, r.final)
            r.name = name

    apicaf_id = fields.Many2one("dte.caf.apicaf")
    name = fields.Char(compute="get_name")
    fecha = fields.Date(string="Fecha de Emisión CAF",)
    cantidad = fields.Integer(string="Cantidad Folios del CAF",)
    inicial = fields.Integer(string="Folio Inicial",)
    final = fields.Integer(string="Folio Final",)
    form_name = fields.Char(string="Form Name")
    cod_docto = fields.Many2one("sii.document_class", string="Código Documento",)
    selected = fields.Boolean(string="Seleccionar")
    sequence = fields.Integer(string="secuencia")
    caf_id = fields.Many2one(
        'dte.caf',
        string="CAF en Odoo")

    @api.onchange('selected')
    def _onchange_selected(self):
        if self.selected:
            for line in self.apicaf_id.lineas_disponibles:
                if line != self:
                    line.selected = False


class APICAF(models.TransientModel):
    _name = "dte.caf.apicaf"
    _description = "Asistente para la administración de CAF en el SII"

    @api.model
    def _selection_firmas(self):
        """Firmas vigentes del usuario interno actual (está en ``user_ids``) en sus compañías
        permitidas; vacío para usuarios portal o públicos.

        ``sii.firma`` no es legible para quien no es contador (clave, llave privada): el
        asistente no muestra un many2one al modelo, sino esta lista con solo el nombre de las
        firmas propias del usuario. La firma se vuelve a validar en ``conectar_api``.
        """
        user = self.env.user
        # Sin el reemplazo por el admin de _firma_autorizada: fields_get no revisa los ACL del
        # modelo, y un usuario portal o público recibiría los nombres de las firmas del admin.
        if not user._is_internal():
            return []
        firmas = self.env["sii.firma"].sudo().search([
            ("user_ids", "=", user.id),
            ("company_ids", "in", self.env.companies.ids),
            ("state", "=", "valid"),
        ], order="priority ASC, id")
        return [(str(firma.id), firma.name) for firma in firmas]

    @api.depends("firma_opcion")
    def _compute_firma(self):
        # Odoo no valida los valores de una selección dinámica al escribir por RPC: solo se
        # acepta una de las firmas que la propia selección ofrece al usuario.
        permitidas = dict(self._selection_firmas())
        for wizard in self:
            firma = self.env["sii.firma"]
            if wizard.firma_opcion and wizard.firma_opcion in permitidas:
                firma = firma.browse(int(wizard.firma_opcion))
            wizard.firma = firma

    @api.model
    def _check_apicaf_habilitado(self, company):
        """Privacidad: apicaf.cl recibe la firma electrónica (llave privada) y el RUT de la
        compañía. Solo se usa si la compañía lo habilitó en Ajustes."""
        if not company.use_apicaf:
            raise UserError(_(
                "La solicitud de folios con apicaf.cl no está habilitada para %s. "
                "Actívela en Ajustes > Facturación Electrónica > API Emisión de Folios.",
                company.name or self.env.company.name,
            ))

    @api.model
    def default_get(self, fields):
        res = super().default_get(fields)
        company = self.env["res.company"].browse(res.get("company_id")) if res.get("company_id") else self.env.company
        self._check_apicaf_habilitado(company)
        return res

    def _apicaf_post(self, url, datos):
        self._check_apicaf_habilitado(self.company_id)
        return pool.request("POST", url, body=json.dumps(datos))

    @api.onchange("firma_opcion", "firma", "operacion")
    def conectar_api(self):
        self._check_apicaf_habilitado(self.company_id)
        motivo = False
        # sii.firma solo es legible por contadores/administradores: la llave se lee con sudo,
        # pero solo si el usuario está autorizado en la firma para la compañía (vigente y activa).
        firma = self.firma._firma_autorizada(self.company_id)
        if not firma:
            motivo = "el usuario %s no tiene firma electrónica autorizada para %s" % (
                self.env.user.name, self.company_id.name)
        elif not self.company_id.url_apicaf or not self.company_id.token_apicaf:
            motivo = "falta la URL o el token de apicaf.cl en la configuración de %s" % (
                self.company_id.name)
        if motivo:
            self.message = motivo
            _logger.warning("No se pudo conectar con apicaf.cl: %s", motivo)
            return
        url = self.company_id.url_apicaf
        token = self.company_id.token_apicaf
        etapa = "conectar"
        if self.operacion == "reobtener":
            etapa = "reob_conectar"
        elif self.operacion == "anular":
            etapa = "an_conectar"
        params = {
            "firma_electronica": {
                "priv_key": firma.priv_key,
                "cert": firma.cert,
                "init_signature": False,
                "subject_serial_number": firma.subject_serial_number,
            },
            "token": token,
            "rut": self.company_id.document_number,
            "etapa": etapa,
            "entorno": "produccion" if self.company_id.dte_service_provider == "SII" else "certificacion",
        }
        resp = self._apicaf_post(url, params)
        if resp.status != 200:
            _logger.warning("Error en conexión con api apicaf %s" % resp.data)
            self.message = ""
            if resp.status == 403:
                data = json.loads(resp.data.decode("ISO-8859-1"))
                self.message = data["message"]
            else:
                self.message = str(resp.data)
            self.env["bus.bus"]._sendone(
                self.env.user.partner_id,
                'dte.caf.apicaf/display_notification',
                {
                    "title": "Error en conexión con apicaf",
                    "message": self.message,
                    "url": {"name": "ir a apicaf.cl", "uri": "https://apicaf.cl"},
                    "type": "dte_notif",
                },
            )
            return
        data = json.loads(resp.data.decode("ISO-8859-1"))
        if self.operacion == "obtener":
            self.etapa = data["etapa"]
        elif self.operacion == "reobtener":
            self.reob_etapa = data["etapa"]
        elif self.operacion == "anular":
            self.an_etapa = data["etapa"]
        self.id_peticion = data["id_peticion"]
        self.listar()
        if self.cod_docto:
            self.get_disp()

    @api.depends("api_folios_disp", "api_max_autor")
    def _details(self):
        self.folios_disp = self.api_folios_disp
        self.max_autor = self.api_max_autor

    documentos = fields.Many2many("account.journal.sii_document_class", string="Documentos disponibles",)
    jdc_id = fields.Many2one("account.journal.sii_document_class", string="Secuencia de diario")
    sequence_id = fields.Many2one("ir.sequence", string="Secuencia")
    cod_docto = fields.Many2one(
        "sii.document_class", related="sequence_id.sii_document_class_id", string="Código Documento", readonly=True,
    )
    etapa = fields.Selection(
        [
            ("conectar", "Conectar al SII"),
            ("listar", "Listar documentos"),
            ("disponibles", "Folios disponibles para el Tipo de Documento"),
            ("confirmar", "Confirmar Folios"),
            ("obtener", "Obtener Folios"),
            ("archivo", "Obtener archivo"),
        ],
        default="conectar",
        string="Etapa",
    )
    reob_etapa = fields.Selection(
        [
            ("reob_conectar", "Conectar al SII"),
            ("reob_listar", "Listar documentos"),
            ("reob_disponibles", "CAFs disponibles para el Tipo de Documento"),
            ("reob_confirmar", "Confirmar Folios"),
            ("reob_obtener", "Obtener Folios"),
            ("reob_archivo", "Obtener archivo"),
        ],
        default="reob_conectar",
        string="Etapa Reobtención",
    )
    an_etapa = fields.Selection(
        [
            ("an_conectar", "Conectar al SII"),
            ("an_listar", "Listar documentos"),
            ("an_disponibles", "CAFs disponibles para el Tipo de Documento"),
            ("an_confirmar", "Confirmar Folios"),
            ("an_motivo", "Motivo"),
            ("an_finalizar", "Anulación"),
        ],
        default="an_conectar",
        string="Etapa Anulación",
        required=True,
    )
    operacion = fields.Selection(
        [("obtener", "Solicitar Folios"), ("reobtener", "Reobtener Folios"), ("anular", "Anular Folios"),],
        string="Opción a realizar",
        default="obtener",
    )
    folios_disp = fields.Integer(
        string="Folios Emitidos SIN USAR",
        compute="_details",
        help="Debe Revisar si ha tenido algún salto de folios, "
        "si piensa que ya no debiera tener folios disponibles o puede que no se hayan enviado al SII "
        "o estén recahzados, por loque debe revisar el estado de facturas lo antes posible",
    )
    api_folios_disp = fields.Integer(string="Folios Emitidos SIN USAR", default=0,)
    max_autor = fields.Integer(string="Cantidad Máxima Autorizada para el Documento", compute="_details")
    api_max_autor = fields.Integer(string="Cantidad Máxima Autorizada para el Documento", default=0,)
    cant_doctos = fields.Integer(string="Cantidad de Folios a Solicitar", default=0,)
    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        default=lambda self: self.env.company,
    )
    firma_opcion = fields.Selection(selection="_selection_firmas", string="Firma Electrónica")
    firma = fields.Many2one("sii.firma", string="Firma Electrónica (registro)",
                            compute="_compute_firma", store=True, readonly=False)
    id_peticion = fields.Integer(string="ID Petición", default=0,)
    lineas_disponibles = fields.One2many("dte.caf.apicaf.docs", "apicaf_id", string="CAF disponibles",)
    form_name = fields.Char(string="Form Name",)
    folio_ini = fields.Integer(string="Folio inicial Anular",)
    folio_fin = fields.Integer(string="Folio Final Anular",)
    api_folio_ini = fields.Integer(string="Folio inicial CAF",)
    api_folio_fin = fields.Integer(string="Folio Final CAF",)
    motivo = fields.Char(string="Motivo de anulación",)
    message = fields.Text(string="Mensaje de respuestass")
    reob_page_number = fields.Integer(string="Número de página", default=1)
    reob_has_next_page = fields.Boolean(string="Tiene siguiente página", default=False)
    es_ban = fields.Boolean(string="Bloqueado por ban", compute="_compute_es_ban")

    @api.depends("message")
    def _compute_es_ban(self):
        for rec in self:
            rec.es_ban = "Baneado" in (rec.message or "")

    @api.onchange("lineas_disponibles")
    def selected_caf(self):
        if not self.lineas_disponibles.filtered('selected') or not self.id_peticion or self.operacion != "anular":
            return
        self.an_etapa = "an_confirmar"
        return self.obtener_caf()

    @api.onchange("jdc_id")
    def _set_cod_docto(self):
        if not self.documentos:
            return
        self.cod_docto = self.jdc_id.sii_document_class_id.id
        self.sequence_id = self.jdc_id.sequence_id.id

    def listar(self):
        if not self.id_peticion:
            return
        self.etapa = "listar"
        etapa = "listar"
        if self.operacion == "reobtener":
            etapa = "reob_" + etapa
            self.reob_etapa = etapa
        elif self.operacion == "anular":
            etapa = "an_" + etapa
            self.an_etapa = etapa
        url = self.company_id.url_apicaf
        token = self.company_id.token_apicaf
        params = {
            "token": token,
            "etapa": etapa,
            "id_peticion": self.id_peticion,
            "cod_docto": self.cod_docto.sii_code,
        }
        resp = self._apicaf_post(url, params)
        if resp.status != 200:
            _logger.warning("Error en conexión con api apicaf %s" % resp.data)
            self.message = ""
            if resp.status == 403:
                data = json.loads(resp.data.decode("ISO-8859-1"))
                self.message = data["message"]
                _logger.warning(self.message)
            else:
                self.message = str(resp.data)
            self.env["bus.bus"]._sendone(
                self.env.user.partner_id,
                'dte.caf.apicaf/display_notification',
                {
                    "title": "Error en conexión con apicaf",
                    "message": self.message,
                    "url": {"name": "ir a apicaf.cl", "uri": "https://apicaf.cl"},
                    "type": "dte_notif",
                },
            )
            return
        data = json.loads(resp.data.decode("ISO-8859-1"))

    @api.onchange("cod_docto")
    def get_disp(self):
        if not self.id_peticion:
            return
        url = self.company_id.url_apicaf
        token = self.company_id.token_apicaf
        self.etapa = "disponibles"
        etapa = "disponibles"
        if self.operacion == "reobtener":
            etapa = "reob_" + etapa
            self.reob_etapa = etapa
        elif self.operacion == "anular":
            etapa = "an_" + etapa
            self.an_etapa = etapa
        params = {
            "token": token,
            "etapa": etapa,
            "id_peticion": self.id_peticion,
            "cod_docto": self.cod_docto.sii_code,
        }
        if self.operacion in ["anular", "reobtener"]:
            params["pagina"] = self.reob_page_number
        resp = self._apicaf_post(url, params)
        if resp.status != 200:
            _logger.warning("Error en conexión con api apicaf %s" % resp.data)
            self.message = ""
            if resp.status == 403:
                data = json.loads(resp.data.decode("ISO-8859-1"))
                self.message = data["message"]
                _logger.warning(self.message)
            else:
                self.message = str(resp.data)
            self.env["bus.bus"]._sendone(
                self.env.user.partner_id,
                'dte.caf.apicaf/display_notification',
                {
                    "title": "Error en conexión con apicaf",
                    "message": self.message,
                    "url": {"name": "ir a apicaf.cl", "uri": "https://apicaf.cl"},
                    "type": "dte_notif",
                },
            )
            return
        data = json.loads(resp.data.decode("ISO-8859-1"))
        if self.operacion == "obtener":
            max_autor = int(data.get("max_autor", 0))
            self.api_folios_disp = data.get("folios_disp", 0)
            self.api_max_autor = max_autor
            self.cant_doctos = max_autor if max_autor >= 0 else 1
            self.etapa = data["etapa"]
        elif self.operacion in ["anular", "reobtener"]:
            if self.operacion == "anular":
                self.an_etapa = data["etapa"]
            else:
                self.reob_etapa = data["etapa"]
            if "pagina_actual" in data:
                self.reob_page_number = data.get("pagina_actual", 1)
            if "tiene_siguiente" in data:
                self.reob_has_next_page = data.get("tiene_siguiente", False)
            folios = []
            for f in data.get("folios", []):
                linea_vals = dict(
                    fecha="{}-{}-{}".format(f["ano"], f["mes"], f["dia"]),
                    cantidad=f["cantidad"],
                    inicial=f["folio_inicial"],
                    final=f["folio_final"],
                    form_name=f["form_name"],
                    cod_docto=self.cod_docto.id,
                )
                if self.operacion == 'anular':
                    caf = self.env['dte.caf'].search([
                        ('start_nm', '=', f['folio_inicial']),
                        ('final_nm', '=', f['folio_final']),
                        ('document_class_id', '=', self.cod_docto.id),
                        ('rut_n', '=', self.company_id.partner_id.rut())
                    ], limit=1)
                    linea_vals['caf_id'] = caf.id
                folios.append(
                    (
                        0,
                        0,
                        linea_vals,
                    )
                )
            self.lineas_disponibles = [(5, 0, 0)] + folios


    def obtener_caf(self):
        if not self.id_peticion:
            return
        url = self.company_id.url_apicaf
        token = self.company_id.token_apicaf
        peticion = {
            "token": token,
            "id_peticion": self.id_peticion,
        }
        etapa = "confirmar"
        caf_line = self.lineas_disponibles.filtered('selected')
        if self.operacion == "obtener":
            peticion.update(
                id_peticion=self.id_peticion, cant_doctos=self.cant_doctos,
            )
            if self.api_max_autor == 0:
                raise UserError("No tiene folios disponibles")
            if (self.api_max_autor > -1 and self.cant_doctos > self.api_max_autor) or self.cant_doctos < 1:
                raise UserError("Debe ingresar una cantidad mayor a 0 y hasta la cantidad máxima autorizada")
        elif self.operacion in ["anular", "reobtener"]:
            etapa = "reob_confirmar"
            if self.operacion == "anular":
                etapa = "an_confirmar"
            if not caf_line:
                raise UserError("Debe seleccionar Uno")
            peticion.update(form_name=caf_line.form_name)
        peticion["etapa"] = etapa
        resp = self._apicaf_post(url, peticion)
        if resp.status != 200:
            _logger.warning("Error en conexión con api apicaf %s" % resp.data)
            self.message = ""
            if resp.status == 403:
                data = json.loads(resp.data.decode("ISO-8859-1"))
                self.message = data["message"]
                _logger.warning(self.message)
            else:
                self.message = str(resp.data)
            self.env["bus.bus"]._sendone(
                self.env.user.partner_id,
                'dte.caf.apicaf/display_notification',
                {
                    "title": "Error en conexión con apicaf",
                    "message": self.message,
                    "url": {"name": "ir a apicaf.cl", "uri": "https://apicaf.cl"},
                    "type": "dte_notif",
                },
            )
            return
        data = json.loads(resp.data.decode("ISO-8859-1"))
        if self.operacion == "obtener":
            self.etapa = data["etapa"]
        elif self.operacion == "reobtener":
            self.reob_etapa = data["etapa"]
        elif self.operacion == "anular":
            self.an_etapa = data["etapa"]
            self.api_folio_ini = data["folio_ini"]
            self.api_folio_fin = data["folio_fin"]
            return
        return self.confirmar()


    def confirmar(self):
        if not self.id_peticion:
            return
        url = self.company_id.url_apicaf
        token = self.company_id.token_apicaf
        peticion = {
            "token": token,
            "id_peticion": self.id_peticion,
        }
        etapa = "obtener"
        if self.operacion == "reobtener":
            etapa = "reob_obtener"
        elif self.operacion == "anular":
            etapa = "an_motivo"
            peticion.update(folio_ini_a=self.folio_ini, folio_fin_a=self.folio_fin, motivo=self.motivo)
        peticion["etapa"] = etapa
        resp = self._apicaf_post(url, peticion)
        if resp.status != 200:
            _logger.warning("Error en conexión con api apicaf %s" % resp.data)
            self.message = ""
            if resp.status == 403:
                data = json.loads(resp.data.decode("ISO-8859-1"))
                self.message = data["message"]
                _logger.warning(self.message)
            else:
                self.message = str(resp.data)
            self.env["bus.bus"]._sendone(
                self.env.user.partner_id,
                'dte.caf.apicaf/display_notification',
                {
                    "title": "Error en conexión con apicaf",
                    "message": self.message,
                    "url": {"name": "ir a apicaf.cl", "uri": "https://apicaf.cl"},
                    "type": "dte_notif",
                },
            )
            return

        caf_line = self.lineas_disponibles.filtered('selected')
        data = json.loads(resp.data.decode("ISO-8859-1"))
        nombre = data.get("nombre")
        peticion2 = {
            "token": token,
            "id_peticion": self.id_peticion,
            "etapa": data["etapa"],
        }
        if self.operacion == "obtener":
            self.etapa = data["etapa"]
        elif self.operacion == "reobtener":
            self.reob_etapa = data["etapa"]
        elif self.operacion == "anular":
            self.an_etapa = data["etapa"]
            if caf_line.caf_id:
                caf_line.caf_id.after_anular_folios(
                    self.folio_ini,
                    self.folio_fin
                )
            return
        resp = self._apicaf_post(url, peticion2)
        if resp.status != 200:
            _logger.warning("Error en conexión con api apicaf %s" % resp.data)
            self.message = ""
            if resp.status == 403:
                data = json.loads(resp.data.decode("ISO-8859-1"))
                self.message = data["message"]
                _logger.warning(self.message)
            else:
                self.message = str(resp.data)
            self.env["bus.bus"]._sendone(
                self.env.user.partner_id,
                'dte.caf.apicaf/display_notification',
                {
                    "title": "Error en conexión con apicaf",
                    "message": self.message,
                    "url": {"name": "ir a apicaf.cl", "uri": "https://apicaf.cl"},
                    "type": "dte_notif",
                },
            )
            return

        data = json.loads(resp.data.decode("ISO-8859-1"))
        # caf_file/caf_string solo los escriben contadores: el usuario ya fue validado contra la firma.
        caf = self.env["dte.caf"].sudo().create(
            {
                "caf_file": data["archivo_caf"],
                "caf_string": base64.b64decode(data["archivo_caf"]).decode("ISO-8859-1"),
                "sequence_id": self.sequence_id.id,
                "company_id": self.company_id.id,
                "filename": nombre,
            }
        )
        caf.load_caf()


    def next_page_reob(self):
        if self.operacion in ["anular", "reobtener"] and self.reob_has_next_page:
            self.reob_page_number += 1
            self.get_disp()
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def delist(self):
        self._check_apicaf_habilitado(self.company_id)
        url = self.company_id.url_apicaf.rsplit("/", 1)[0] + "/delist"
        token = self.company_id.token_apicaf
        peticion = {
            "token": token,
        }
        resp = self._apicaf_post(url, peticion)
        try:
            data = json.loads(resp.data.decode("ISO-8859-1"))
        except Exception:
            data = {}
        if resp.status != 200:
            _logger.warning("Error en conexión con api apicaf delist %s" % resp.data)
            self.message = data.get("message") or str(resp.data)
            self.env["bus.bus"]._sendone(
                self.env.user.partner_id,
                'dte.caf.apicaf/display_notification',
                {
                    "title": "Error al remover el bloqueo",
                    "message": self.message,
                    "url": {"name": "ir a apicaf.cl", "uri": "https://apicaf.cl"},
                    "type": "dte_notif",
                },
            )
            return
        self.message = ""
        self.env["bus.bus"]._sendone(
            self.env.user.partner_id,
            'dte.caf.apicaf/display_notification',
            {
                "title": "Bloqueo removido",
                "message": data.get("message") or "Bloqueo removido, reintentando conexión.",
                "type": "dte_notif",
            },
        )
        self.conectar_api()
