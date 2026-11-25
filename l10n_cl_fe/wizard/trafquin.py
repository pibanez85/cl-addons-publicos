import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import urllib3

    pool = urllib3.PoolManager(timeout=urllib3.Timeout(connect=10.0, read=900.0))
except ImportError:
    pool = None

MAX_DIAS = 31
MAX_LISTAR = 1000
PASOS_LISTAR = 10
PASOS_DESCARGAR = 20
MAX_ANCHO_FOLIOS = 100000
SALTO_FOLIOS = 20
ORIGEN_TRAFQUIN = {"recibidos": "compras", "emitidos": "ventas"}


class TrafquinError(UserError):

    def __init__(self, message, datos=None):
        super().__init__(message)
        self.datos = datos or {}


class TrafquinXmlWizard(models.TransientModel):
    _name = "sii.dte.trafquin.wizard"
    _description = "XML de DTE desde el SII (trafquin)"

    origen = fields.Selection(
        [("recibidos", "Recibidos (proveedores)"), ("emitidos", "Emitidos")],
        required=True, default="recibidos",
    )
    company_id = fields.Many2one(
        "res.company", string="Compañía", required=True, default=lambda self: self.env.company,
    )
    desde = fields.Date()
    hasta = fields.Date()
    folio_desde = fields.Integer(string="Folio desde")
    folio_hasta = fields.Integer(string="Folio hasta")
    document_class_id = fields.Many2one(
        "sii.document_class", string="Tipo de documento", domain=[("dte", "=", True)],
    )
    rut_contraparte = fields.Char(string="RUT contraparte")
    inicio_actividades = fields.Date(related="company_id.start_date", string="Inicio de actividades")
    resultado = fields.Text(readonly=True)
    cursor_listado = fields.Char(readonly=True)
    listado_completo = fields.Boolean(readonly=True)
    linea_ids = fields.One2many("sii.dte.trafquin.linea", "wizard_id", string="Documentos en el SII")

    def _validar_filtros(self):
        if self.desde and self.hasta and self.hasta < self.desde:
            raise UserError(_("La fecha final debe ser posterior a la inicial"))
        if self.folio_desde and not self.folio_hasta:
            raise UserError(_("Indique también el folio hasta"))
        if self.folio_desde and self.folio_hasta < self.folio_desde:
            raise UserError(_("El folio final debe ser mayor o igual al inicial"))
        if self.folio_hasta and self.folio_hasta - (self.folio_desde or 1) + 1 > MAX_ANCHO_FOLIOS:
            raise UserError(_("Pida como máximo %s folios por vez") % MAX_ANCHO_FOLIOS)
        if self.rut_contraparte and not self._rut_contraparte():
            raise UserError(_("El RUT de la contraparte no es válido: %s") % self.rut_contraparte)

    def _rut_contraparte(self):
        Partner = self.env["res.partner"]
        rut = Partner._rut_de_texto(self.rut_contraparte, minimo=2) if self.rut_contraparte else False
        return Partner._rut_sii(rut) if rut else ""

    def _filtros_listar(self):
        datos = {"origen": ORIGEN_TRAFQUIN[self.origen], "cantidad": MAX_LISTAR}
        if self.desde:
            datos["desde"] = self.desde.isoformat()
        if self.hasta:
            datos["hasta"] = self.hasta.isoformat()
        if self.document_class_id:
            datos["tipo_doc"] = self.document_class_id.sii_code
        if self.rut_contraparte:
            datos["rut_contraparte"] = self._rut_contraparte()
        if self.folio_desde and self.folio_desde == self.folio_hasta:
            datos["folio"] = self.folio_desde
            return datos
        if self.folio_desde:
            datos["folio_desde"] = self.folio_desde
        if self.folio_hasta:
            datos["folio_hasta"] = self.folio_hasta
        return datos

    def _en_rango_folio(self, folio):
        if self.folio_desde and folio < self.folio_desde:
            return False
        return not (self.folio_hasta and folio > self.folio_hasta)

    def listar(self, continuar=False):
        self.ensure_one()
        self._validar_filtros()
        self._exigir_produccion(self.company_id)
        if continuar and self.listado_completo:
            return {"total": 0, "nuevos": 0, "existentes": 0, "completo": True, "advertencias": []}
        datos = self._filtros_listar()
        if continuar and self.cursor_listado:
            datos = {"origen": ORIGEN_TRAFQUIN[self.origen], "cursor": self.cursor_listado, "cantidad": MAX_LISTAR}
        data = self._llamar_trafquin(self.company_id, "/api/xml/listar", datos)
        vistos = {(l.tipo_dte, l.folio, l.rut) for l in self.linea_ids}
        valores = []
        for doc in data.get("documentos") or []:
            linea = self._valores_linea(doc)
            clave = (linea["tipo_dte"], linea["folio"], linea["rut"])
            if clave in vistos or not self._en_rango_folio(int(linea["folio"] or 0)):
                continue
            vistos.add(clave)
            valores.append(dict(linea, wizard_id=self.id))
        nuevas = self.env["sii.dte.trafquin.linea"].create(valores)
        self.write({"cursor_listado": data.get("cursor") or False, "listado_completo": bool(data.get("completo"))})
        return {
            "total": len(nuevas),
            "nuevos": len(nuevas.filtered(lambda l: l.estado == "nuevo")),
            "existentes": len(nuevas.filtered(lambda l: l.estado == "existente")),
            "completo": self.listado_completo,
            "advertencias": self._avisos(data),
        }

    def restaurar(self, linea_ids=None):
        self.ensure_one()
        lineas = self.linea_ids.filtered(lambda l: l.estado == "nuevo")
        if linea_ids is not None:
            lineas = lineas.filtered(lambda l: l.id in linea_ids)
        res = {"total": len(lineas), "nuevos": 0, "existentes": 0, "document_ids": [], "move_ids": [],
               "advertencias": []}
        for grupo in self._grupos_descarga(lineas):
            documentos, avisos = self._descargar_grupo(grupo)
            res["advertencias"] += avisos
            for linea in grupo:
                linea._restaurar(res, documentos)
        return res

    def _clave_documento(self, tipo, folio, rut):
        rut = self.env["res.partner"]._rut_clave(rut) if self.origen == "recibidos" else ""
        return int(tipo or 0), int(folio or 0), rut

    def _grupos_descarga(self, lineas):
        por_clave = {}
        for linea in lineas.sorted(lambda l: int(l.folio or 0)):
            clave = (linea.tipo_dte, linea.rut if self.origen == "recibidos" else "")
            por_clave.setdefault(clave, []).append(linea)
        grupos = []
        for seguidas in por_clave.values():
            grupo = self.env["sii.dte.trafquin.linea"]
            for linea in seguidas:
                if grupo and int(linea.folio) - int(grupo[-1].folio) > SALTO_FOLIOS:
                    grupos.append(grupo)
                    grupo = self.env["sii.dte.trafquin.linea"]
                grupo |= linea
            grupos.append(grupo)
        return grupos

    def _descargar_grupo(self, grupo):
        folios = [int(f) for f in grupo.mapped("folio")]
        datos = {"origen": ORIGEN_TRAFQUIN[self.origen], "tipo_doc": grupo[0].tipo_dte, "cantidad": 200}
        if min(folios) == max(folios):
            datos["folio"] = min(folios)
        else:
            datos.update(folio_desde=min(folios), folio_hasta=max(folios))
        if self.origen == "recibidos" and grupo[0].rut:
            datos["rut_contraparte"] = grupo[0].rut
        documentos, avisos = {}, []
        for _paso in range(PASOS_DESCARGAR):
            data = self._llamar_trafquin(self.company_id, "/api/xml/descargar", datos)
            avisos += self._avisos(data)
            for doc in data.get("documentos") or []:
                if doc.get("xml_b64"):
                    documentos[self._clave_documento(doc.get("tipo_dte"), doc.get("folio"), doc.get("rut_emisor"))] = doc
            if data.get("completo") or not data.get("cursor"):
                break
            datos = {"origen": ORIGEN_TRAFQUIN[self.origen], "cursor": data["cursor"], "cantidad": 200}
        return documentos, avisos

    def obtener_respaldo(self):
        self.ensure_one()
        self._validar_filtros()
        if not self.desde or not self.hasta:
            raise UserError(_("Indique el rango de fechas a respaldar"))
        consulta = self._consultar_respaldo(
            self.company_id, self.origen, self.desde, self.hasta,
            tipo_dte=self.document_class_id.sii_code or 0, rut_contraparte=self._rut_contraparte(),
        )
        res = {"total": consulta["total"], "nuevos": 0, "existentes": 0, "document_ids": [], "move_ids": [],
               "advertencias": consulta["advertencias"]}
        for doc in consulta["documentos"]:
            if not doc.get("xml_b64"):
                res["advertencias"].append(_("El SII no entregó el XML de %s folio %s") % (
                    doc.get("tipo_dte"), doc.get("folio")))
                continue
            estado, registro, mensaje = self._registrar_documento(self.company_id, self.origen, doc)
            if registro:
                res["move_ids" if registro._name == "account.move" else "document_ids"] += registro.ids
            if estado == "restaurado":
                res["nuevos"] += 1
            elif estado == "existente":
                res["existentes"] += 1
            else:
                res["advertencias"].append("%s %s: %s" % (doc.get("tipo_dte"), doc.get("folio"), mensaje))
        return res

    def _reabrir(self, lineas):
        self.resultado = "\n".join(lineas)
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _listar_pasos(self, continuar):
        total = {"total": 0, "nuevos": 0, "existentes": 0, "advertencias": []}
        for paso in range(PASOS_LISTAR):
            parcial = self.listar(continuar=continuar or paso > 0)
            for clave in ("total", "nuevos", "existentes"):
                total[clave] += parcial[clave]
            total["advertencias"] += parcial["advertencias"]
            if parcial["completo"]:
                break
        lineas = [
            _("Documentos listados: %s") % total["total"],
            _("Nuevos: %s") % total["nuevos"],
            _("Ya registrados: %s") % total["existentes"],
        ]
        if not self.listado_completo:
            lineas.append(_("El SII tiene más documentos: use «Listar más» para seguir"))
        return self._reabrir(lineas + total["advertencias"])

    def action_listar(self):
        self.ensure_one()
        self.linea_ids.unlink()
        self.write({"cursor_listado": False, "listado_completo": False})
        return self._listar_pasos(False)

    def action_listar_mas(self):
        self.ensure_one()
        return self._listar_pasos(True)

    def action_restaurar(self):
        self.ensure_one()
        res = self.restaurar(self.linea_ids.filtered("seleccionado").ids)
        if not res["total"]:
            raise UserError(_("Seleccione al menos un documento nuevo para restaurar"))
        return self._reabrir([
            _("Restaurados: %s") % res["nuevos"],
            _("Ya registrados: %s") % res["existentes"],
        ] + res["advertencias"])

    def action_ver_documentos(self):
        if self.origen == "emitidos":
            return {
                "type": "ir.actions.act_window",
                "name": _("Facturas obtenidas del SII"),
                "res_model": "account.move",
                "view_mode": "list,form",
                "domain": [("id", "in", self.linea_ids.move_id.ids)],
                "target": "current",
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Documentos obtenidos del SII"),
            "res_model": "mail.message.dte.document",
            "view_mode": "list,form",
            "domain": [("id", "in", self.linea_ids.document_id.ids)],
            "target": "current",
        }

    def _valores_linea(self, doc):
        tipo = int(doc.get("tipo_dte") or 0)
        folio = str(doc.get("folio") or "")
        rut = doc.get("rut_contraparte") or ""
        try:
            fecha = fields.Date.to_date(doc.get("fecha"))
        except ValueError:
            fecha = False
        valores = {
            "tipo_dte": tipo,
            "document_class_id": self.env["sii.document_class"].search([("sii_code", "=", tipo)], limit=1).id,
            "folio": folio,
            "fecha": fecha,
            "rut": rut,
            "razon_social": doc.get("razon_social"),
            "total": doc.get("monto") or 0,
            "estado_sii": doc.get("estado"),
            "codigo": doc.get("codigo"),
        }
        if not tipo or not folio:
            valores.update(estado="error", seleccionado=False, mensaje=_("El SII no informó el tipo o el folio"))
            return valores
        existente = self._existente_datos(tipo, folio, rut)
        if existente:
            campo = "move_id" if existente._name == "account.move" else "document_id"
            valores.update({"estado": "existente", "seleccionado": False, campo: existente.id})
        return valores

    def _existente_datos(self, tipo, folio, rut):
        if self.origen == "emitidos":
            return self.env["account.move"].search([
                ("company_id", "=", self.company_id.id),
                ("move_type", "in", ["out_invoice", "out_refund"]),
                ("document_class_id.sii_code", "=", tipo),
                ("sii_document_number", "=", int(folio)),
                ("state", "!=", "cancel"),
            ], limit=1)
        if not rut:
            return self.env["mail.message.dte.document"]
        return self.env["mail.message.dte.document"].search([
            ("company_id", "=", self.company_id.id),
            ("number", "=", folio),
            ("document_class_id.sii_code", "=", tipo),
            ("source", "!=", "rcv"),
            "|", ("partner_id.vat", "in", self.env["res.partner"]._vats_busqueda(rut)),
            ("new_partner", "=like", "%s %%" % rut),
        ], limit=1)

    @api.model
    def _avisos(self, data):
        avisos = [str(a) for a in data.get("advertencias") or []]
        return avisos + [str(l.get("mensaje") or l.get("causa")) for l in data.get("limitaciones") or []]

    @api.model
    def _exigir_produccion(self, company):
        if company.dte_service_provider != "SII":
            raise UserError(_(
                "El respaldo de XML del SII solo existe en producción: %s está en certificación"
            ) % company.name)

    @api.model
    def _firma_trafquin(self, company):
        firma = self.env.user.get_digital_signature(company)
        if not firma:
            raise UserError(_(
                "El usuario '%s' no está autorizado a usar firma electrónica para %s."
            ) % (self.env.user.name, company.name))
        if firma.file_content and firma.password:
            contenido = firma.file_content
            return {
                "string_firma": contenido.to_base64(),
                "string_password": firma.password,
                "rut_firmante": firma.subject_serial_number,
            }
        return firma.parametros_firma()

    @api.model
    def _llamar_trafquin(self, company, ruta, datos, clave=False):
        if pool is None:
            raise UserError(_("Falta la librería urllib3 en el servidor"))
        if not company.url_trafquin:
            raise UserError(_("Falta la URL de trafquin en la configuración de %s") % company.name)
        clave_sii = clave and company.sudo().sii_clave_tributaria
        if clave_sii and company._clave_sii_rechazada():
            raise UserError(_("La clave SII de %s fue rechazada: revísela en Ajustes") % company.name)
        datos = dict(datos, rut=company.partner_id.rut(), entorno="produccion", token=company.sudo().token_trafquin or "")
        if clave_sii:
            datos.update(rut=company._rut_ingreso_sii(), clave_tributaria=clave_sii)
        else:
            datos["firma_electronica"] = self._firma_trafquin(company)
        url = company.url_trafquin.rstrip("/") + ruta
        try:
            resp = pool.request(
                "POST", url, body=json.dumps(datos), headers={"Content-Type": "application/json"},
            )
        except urllib3.exceptions.HTTPError as e:
            raise UserError(_("No se pudo conectar con trafquin (%s): %s") % (url, e))
        try:
            data = json.loads(resp.data.decode("utf-8"))
        except ValueError:
            data = {}
        if resp.status not in (200, 202):
            mensaje = data.get("message") or resp.data.decode("utf-8", "replace")[:500]
            if data.get("codigo"):
                mensaje = "%s: %s" % (data["codigo"], mensaje)
            if data.get("mensaje_portal"):
                mensaje += "\n%s" % data["mensaje_portal"]
            if clave_sii:
                mensaje = mensaje.replace(clave_sii, "***")
            raise TrafquinError(_("trafquin respondió %s\n%s") % (resp.status, mensaje), datos=data)
        return data

    @api.model
    def _consultar_respaldo(self, company, origen, desde, hasta, tipo_dte=0, rut_contraparte=""):
        self._exigir_produccion(company)
        res = {"total": 0, "documentos": [], "advertencias": []}
        inicio = desde
        while inicio <= hasta:
            fin = min(inicio + timedelta(days=MAX_DIAS - 1), hasta)
            data = self._llamar_trafquin(company, "/api/xml/%s" % origen, {
                "desde": inicio.isoformat(),
                "hasta": fin.isoformat(),
                "tipo_doc": tipo_dte or 0,
                "rut_contraparte": rut_contraparte or "",
                "formato": "json",
                "incluir_xml": True,
            })
            res["total"] += data.get("total") or len(data.get("documentos") or [])
            res["documentos"] += data.get("documentos") or []
            res["advertencias"] += [str(a) for a in data.get("advertencias") or []]
            inicio = fin + timedelta(days=1)
        return res

    @api.model
    def _obtener_xml(self, company, origen, desde, hasta):
        return self.create({
            "company_id": company.id,
            "origen": origen,
            "desde": desde,
            "hasta": hasta,
        }).obtener_respaldo()

    @api.model
    def _preparar_documento(self, company, origen, doc):
        nombre = "%s_T%s_F%s.xml" % (doc.get("rut_emisor") or doc.get("rut"), doc.get("tipo_dte"), doc.get("folio"))
        wiz = self.env["sii.dte.upload_xml.wizard"].with_company(company).create({
            "xml_file": doc["xml_b64"],
            "filename": nombre,
            "type": "compras" if origen == "recibidos" else "ventas",
            "pre_process": origen == "recibidos",
            "option": "upload",
            "action": "manual",
        })
        dtes = wiz._get_dtes()
        return wiz, dtes[0].find("Documento") if dtes else None

    @api.model
    def _existente(self, wiz, documento, origen):
        if origen == "emitidos":
            return wiz._inv_exist(documento)[:1]
        wiz._dte_exist(documento)
        if wiz.document_id.source == "rcv":
            wiz.document_id = False
        return wiz.document_id

    @api.model
    def _registrar_documento(self, company, origen, doc):
        wiz, documento = self._preparar_documento(company, origen, doc)
        if documento is None:
            return "error", None, _("No se pudo leer el XML")
        existente = self._existente(wiz, documento, origen)
        if existente:
            return "existente", existente, ""
        if origen == "emitidos":
            registro = self.env["account.move"].browse(wiz.with_context(restore_mode=True).do_create_inv() or [])
        else:
            registro = self.env["mail.message.dte.document"].browse(wiz.confirm(ret=True) or []) or \
                wiz.document_id.filtered(lambda d: d.source == "xml" and d.dte_id)
        if not registro:
            return "error", None, _("No se pudo registrar")
        return "restaurado", registro, ""

    @api.model
    def _cron_xml_recibidos(self, dias=3):
        hoy = fields.Date.context_today(self)
        # token_trafquin es solo para administradores: se filtra con sudo, se procesa sin sudo.
        companies = self.env["res.company"].browse(self.env["res.company"].sudo().search([
            ("url_trafquin", "!=", False),
            ("token_trafquin", "!=", False),
            ("dte_service_provider", "=", "SII"),
        ]).ids)
        for company in companies:
            try:
                with self.env.cr.savepoint():
                    res = self._obtener_xml(company, "recibidos", hoy - timedelta(days=dias), hoy)
                _logger.info("trafquin %s: %s nuevos, %s ya registrados", company.name,
                             res["nuevos"], res["existentes"])
                for advertencia in res["advertencias"]:
                    _logger.warning("trafquin %s: %s", company.name, advertencia)
            except UserError as e:
                _logger.warning("trafquin %s: %s", company.name, e)


class TrafquinXmlLinea(models.TransientModel):
    _name = "sii.dte.trafquin.linea"
    _description = "Documento del respaldo de XML del SII"
    _order = "fecha desc, tipo_dte, folio"

    wizard_id = fields.Many2one("sii.dte.trafquin.wizard", required=True, ondelete="cascade")
    seleccionado = fields.Boolean(string="Restaurar", default=True)
    tipo_dte = fields.Integer(string="Tipo")
    document_class_id = fields.Many2one("sii.document_class", string="Documento")
    folio = fields.Char()
    fecha = fields.Date()
    rut = fields.Char(string="RUT")
    razon_social = fields.Char(string="Razón social")
    total = fields.Float()
    estado_sii = fields.Char(string="Estado en el SII")
    codigo = fields.Char()
    estado = fields.Selection(
        [("nuevo", "Nuevo"), ("existente", "Ya registrado"), ("restaurado", "Restaurado"),
         ("error", "No se pudo restaurar")],
        default="nuevo",
    )
    mensaje = fields.Char()
    document_id = fields.Many2one("mail.message.dte.document", string="Documento recibido")
    move_id = fields.Many2one("account.move", string="Factura")

    def _restaurar(self, res, documentos):
        self.ensure_one()
        wizard = self.wizard_id
        doc = documentos.get(wizard._clave_documento(self.tipo_dte, self.folio, self.rut))
        if not doc:
            estado, registro, mensaje = "error", None, _("El SII no entregó el XML")
        else:
            estado, registro, mensaje = wizard._registrar_documento(wizard.company_id, wizard.origen, doc)
        valores = {"estado": estado, "mensaje": mensaje, "seleccionado": False}
        if registro:
            campo = "move_id" if registro._name == "account.move" else "document_id"
            valores[campo] = registro[:1].id
            res["move_ids" if campo == "move_id" else "document_ids"] += registro.ids
        self.write(valores)
        if estado == "restaurado":
            res["nuevos"] += 1
        elif estado == "existente":
            res["existentes"] += 1
        else:
            res["advertencias"].append(
                "%s %s: %s" % (self.document_class_id.name or self.tipo_dte, self.folio, mensaje))
