# -*- coding: utf-8 -*-
from datetime import datetime
from dateutil.relativedelta import relativedelta
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.translate import _
from .binario import BinaryBytes
import logging
import re
from lxml import etree


_logger = logging.getLogger(__name__)
try:
    from facturacion_electronica import facturacion_electronica as fe
    from facturacion_electronica import clase_util as fe_util
except ImportError:
    _logger.warning("No se ha podido cargar fe")


def clave_rut(texto):
    return re.sub(r"[^0-9K]", "", (texto or "").upper().replace("CL", "", 1)).lstrip("0")


class ProcessMailsDocument(models.Model):
    _name = "mail.message.dte.document"
    _description = "Pre Documento Recibido"
    _inherit = ["mail.thread", "l10n_cl_fe.dte.mixin"]

    def _get_dtes(self, xml):
        if xml.tag == "SetDTE":
            return xml.findall("DTE")
        envio = xml.find("SetDTE")
        if envio is None:
            if xml.tag == "DTE":
                return [xml]
            return []
        return envio.findall("DTE")

    def _get_xml(self):
        xml = self.dte_id._parse_xml(self.dte_xml)
        for dte in self._get_dtes(xml):
            Documento = dte.find("Documento")
            if Documento.get("ID") == self.id_dte:
                return fe_util.xml_to_string(dte)

    dte_id = fields.Many2one("mail.message.dte", string="DTE", readonly=True, ondelete="cascade",)
    new_partner = fields.Char(string="Proveedor Nuevo", readonly=True,)
    partner_id = fields.Many2one("res.partner", string="Proveedor",)
    date = fields.Date(string="Fecha Emsisión", readonly=True,)
    number = fields.Char(string="Folio", readonly=True,)
    document_class_id = fields.Many2one(
        "sii.document_class", string="Tipo de Documento", readonly=True,)
    amount = fields.Monetary(string="Monto", readonly=True,)
    monto_no_facturable = fields.Monetary(string="Monto no Facturable", readonly=True,)
    currency_id = fields.Many2one(
        "res.currency", string="Moneda", readonly=True,
        default=lambda self: self.env.company.currency_id,
    )
    invoice_line_ids = fields.One2many("mail.message.dte.document.line", "document_id", string="Líneas del Documento",)
    global_descuentos_recargos = fields.One2many("mail.message.dte.document.gdr", "document_id", string="Líneas GDR",)
    company_id = fields.Many2one("res.company", string="Compañía", readonly=True,)
    state = fields.Selection(
        [("draft", "Recibido"), ("accepted", "Aceptado"), ("rejected", "Rechazado"),], default="draft",
    )
    move_id = fields.Many2one("account.move", string="Factura", readonly=True,)
    id_dte = fields.Char(string="ID DTE")
    dte_xml = fields.Binary(related="dte_id.xml_id.raw")
    xml = fields.Text(string="XML Documento")
    purchase_to_done = fields.Many2one(
        "purchase.order", string="Ordenes de Compra a validar", domain=[("state", "not in", ["accepted", "rejected"])],
    )
    claim = fields.Selection(
        [
            ("N/D", "No definido"),
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
        default="N/D",
    )
    claim_description = fields.Char(string="Detalle Reclamo",)
    claim_ids = fields.One2many("sii.dte.claim", "document_id", string="Historial de Reclamos")
    sii_message = fields.Text(
        string="Respuesta SII"
    )
    journal_id = fields.Many2one('account.journal', string="Journal Destino")
    action = fields.Selection([
            ('create_po', 'Crear Orden de Compra'),
            ('create_move', 'Crear Factura'),
            ('both', 'Crear Ambas')
        ],
        string="Acción al aceptar",
        default='both',
        required=True
    )
    en_sii = fields.Selection(
        [("si", "Está en el SII"), ("no", "No está en el SII")],
        string="Verificación SII", readonly=True, copy=False, index=True,
        help="Resultado de consultar el documento en el registro de reclamos del SII",
    )
    source = fields.Selection(
        [("xml", "XML DTE"), ("rcv", "Registro de Compras del SII")],
        string="Origen", default="xml", readonly=True,
        help="Los ingresados desde el Registro de Compras no tienen XML hasta que este llegue",
    )
    _order = "create_date DESC"

    @api.onchange('purchase_to_done')
    def auto_map_po_lines(self):
        if self.env.context.get('no_map_po_lines'):
            return
        if not self.purchase_to_done:
            for line in self.invoice_line_ids:
                line.purchase_line_id = False
            return
        lines = self.purchase_to_done.order_line
        usadas = self.env["purchase.order.line"]
        for line in self.invoice_line_ids:
            candidatas = lines.filtered(lambda l: line.product_id and l.product_id == line.product_id)
            if not candidatas:
                nombre = (line.name or line.description or "").strip().upper()
                candidatas = lines.filtered(lambda l: nombre and (l.name or "").strip().upper() == nombre)
            elegida = (candidatas - usadas)[:1] or candidatas[:1]
            usadas |= elegida
            line.purchase_line_id = elegida._origin.id if elegida else False

    def _detalles_xml(self):
        self.ensure_one()
        try:
            xml = self._get_xml()
        except Exception:
            xml = False
        if not xml:
            return []
        xml = re.sub(r"<\?xml[^>]*\?>", "", xml).replace(' xmlns="http://www.sii.cl/SiiDte"', "")
        try:
            return etree.fromstring(xml).findall(".//Detalle")
        except etree.XMLSyntaxError:
            return []

    def _aprender_productos(self):
        SupplierInfo = self.env["product.supplierinfo"]
        creados = SupplierInfo
        for doc in self.filtered(lambda d: d.move_id and d.partner_id):
            proveedor = doc.partner_id.commercial_partner_id
            lineas = doc.move_id.invoice_line_ids.filtered(lambda l: l.product_id and l.display_type == "product")
            usadas = self.env["account.move.line"]
            for detalle in doc._detalles_xml():
                nombre = (detalle.findtext("NmbItem") or "").strip()
                codigo = next(
                    (c.findtext("VlrCodigo").strip() for c in detalle.findall("CdgItem") if (c.findtext("VlrCodigo") or "").strip()),
                    False,
                )
                cantidad = float(detalle.findtext("QtyItem") or 1)
                precio = float(detalle.findtext("PrcItem") or detalle.findtext("MontoItem") or 0)
                candidatas = (lineas - usadas).filtered(
                    lambda l: abs(l.quantity - cantidad) < 0.001 and abs(l.price_unit - precio) <= max(1.0, abs(precio) * 0.01)
                )
                if len(candidatas.product_id) != 1 or not nombre:
                    continue
                palabras = {p for p in re.findall(r"[A-Z0-9]{3,}", nombre.upper())}
                if not palabras & set(re.findall(r"[A-Z0-9]{3,}", (candidatas.product_id.name or "").upper())):
                    continue
                usadas |= candidatas[:1]
                producto = candidatas.product_id
                dominio = [("partner_id", "=", proveedor.id)]
                dominio += [("product_code", "=", codigo)] if codigo else [("product_name", "=", nombre)]
                if SupplierInfo.search_count(dominio, limit=1):
                    continue
                existente = SupplierInfo.search([
                    ("partner_id", "=", proveedor.id),
                    ("product_tmpl_id", "=", producto.product_tmpl_id.id),
                ], limit=1)
                if existente:
                    if not existente.product_code and not existente.product_name:
                        existente.write({"product_code": codigo, "product_name": nombre})
                    continue
                creados |= SupplierInfo.create({
                    "partner_id": proveedor.id,
                    "product_name": nombre,
                    "product_code": codigo,
                    "product_tmpl_id": producto.product_tmpl_id.id,
                    "product_id": producto.id,
                    "price": precio,
                })
        return creados

    def _emisor(self):
        Emisor = super()._emisor()
        Emisor["RUTEmisor"] = self.company_id.document_number
        return Emisor

    def _id_doc(self):
        IdDoc = {}
        IdDoc["TipoDTE"] = self.document_class_id.sii_code
        IdDoc["Folio"] = self.number
        IdDoc["FchEmis"] = self.date.strftime("%Y-%m-%d")
        return IdDoc

    def get_doc_rut(self):
        if self.new_partner:
            p = self.new_partner.split(' ')
            return p[0]
        commercial_partner_id = self.partner_id.commercial_partner_id or self.partner_id
        return commercial_partner_id.rut()

    def _receptor(self):
        Receptor = {'RUTRecep': self.get_doc_rut()}
        if self.new_partner:
            p = self.new_partner.split(' ')
            Receptor['RznSocRecep'] = ' '
            for s in p[1:]:
                Receptor['RznSocRecep'] += s
        else:
            commercial_partner_id = self.partner_id.commercial_partner_id or self.partner_id
            Receptor['RznSocRecep'] = commercial_partner_id.name
        return Receptor

    def _totales(self):
        return {"MntTotal": self.amount}

    def _encabezado(self):
        Encabezado = {}
        Encabezado["IdDoc"] = self._id_doc()
        Encabezado["Emisor"] = {"RUTEmisor": self.get_doc_rut()}
        Encabezado["Receptor"] = self._receptor()
        Encabezado["Totales"] = self._totales()
        return Encabezado

    def _dte(self):
        if self.move_id:
            return self.move_id._dte()
        return {"Encabezado": self._encabezado()}

    @api.onchange("move_id")
    def update_claim(self):
        for r in self.claim_ids:
            r.move_id = self.move_id.id

    @api.model
    def auto_accept_documents(self, limit=50):
        dte_receipt_method = self.env["ir.config_parameter"].sudo().get_str(
            "account.dte_receipt_method", default='manual'
        )
        self.env.cr.execute(
            """
            select
                id
            from
                mail_message_dte_document
            where
                create_date + interval '8 days' < now()
                and
                state = 'draft'
            limit {}
            """.format(
                limit
            )
        )
        ids = [line.get("id") for line in self.env.cr.dictfetchall()]
        for d in self.browse(ids):
            d = d.with_company(d.company_id)
            if dte_receipt_method == 'manual' or (d.source == "rcv" and not d.move_id):
                try:
                    d.get_dte_claim()
                except Exception as e:
                    _logger.warning("Problema al obtener claim manual %s" % str(e), exc_info=True)
                continue
            if d.action != dte_receipt_method:
                d.sudo().action = dte_receipt_method
            d.accept_document()

    def _process_document(self):
        self = self.with_company(self.company_id)
        xml = self.xml
        filename = self.dte_id.name
        if self.dte_xml:
            xml = self._get_xml()
        if not xml:
            raise UserError(_(
                "El documento %s %s viene del Registro de Compras y aún no tiene XML"
            ) % (self.document_class_id.name, self.number))
        vals = {
            "xml_file": BinaryBytes(xml.encode("ISO-8859-1")),
            "filename": filename,
            "pre_process": False,
            "document_id": self.id,
            "option": "accept",
            "action": self.action,
        }
        val = self.env["sii.dte.upload_xml.wizard"].sudo().create(vals)
        return val.confirm(ret=True)

    def process_document(self):
        if self.action in ["both", "create_po"] and not any(aml.purchase_line_id or aml.create_pol for aml in self.invoice_line_ids):
            raise UserError("Debe marcar almenos una línea de pedido para crear")
        self._process_document()

    def accept_document(self):
        created = []
        for r in self:
            r = r.with_company(r.company_id)
            try:
                r.get_dte_claim()
            except Exception as e:
                _logger.warning("Problema al obtener claim desde accept %s" % str(e), exc_info=True)
            if r.move_id and r.claim not in ["N/D", False]:
                continue
            if r.move_id:
                resp = r.move_id.ids
                if r.claim in ["N/D", False]:
                    vals = {
                        "move_ids": [(6, 0, r.move_id.ids)],
                        "action": "ambas",
                        "claim": "ACD",
                        "estado_dte": "0",
                        "tipo": "account.move",
                    }
                    val = self.env["sii.dte.validar.wizard"].sudo().create(vals)
                    val.confirm()
            else:
                resp = r._process_document()
            created.extend(resp)
            if r.company_id.dte_service_provider == "SIICERT":
                r.state = "accepted"
                continue
            for i in self.env["account.move"].browse(resp):
                if i.claim in ["ACD", "ERM", "PAG"]:
                    r.state = "accepted"
        action = {
            'name': _('Accepted Moves'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'list,form',
        }
        if created:
            action['domain'] = [('id', 'in', created)]
        return action

    def reject_document(self):
        for r in self:
            if r.xml:
                vals = {
                    "document_ids": [(6, 0, r.ids)],
                    "estado_dte": "2",
                    "action": "validate",
                    "claim": "RCD",
                }
                val = self.env["sii.dte.validar.wizard"].sudo().create(vals)
                val.confirm()
            if r.claim in ["RCD"]:
                r.state = "rejected"

    def set_dte_claim(self, claim):
        if self.document_class_id.sii_code not in [33, 34, 43]:
            self.claim = claim
            return
        folio = self.number
        tipo_dte = self.document_class_id.sii_code
        datos = self._get_datos_empresa()
        rut_emisor = self.get_doc_rut()
        datos["DTEClaim"] = [
            {
                "RUTEmisor": rut_emisor,
                "TipoDTE": tipo_dte,
                "Folio": folio,
                "Claim": claim
            }
        ]
        key = "RUT%sT%sF%s" % (rut_emisor, tipo_dte, folio)
        try:
            respuesta = fe.ingreso_reclamo_documento(datos)[key]
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

    def _consulta_eventos_sii(self):
        folio = self.number
        tipo_dte = self.document_class_id.sii_code
        datos = self._get_datos_empresa()
        rut_emisor = self.get_doc_rut()
        datos["DTEClaim"] = [
            {
                "RUTEmisor": rut_emisor,
                "TipoDTE": tipo_dte,
                "Folio": folio,
            }
        ]
        key = "RUT%sT%sF%s" % (rut_emisor, tipo_dte, folio)
        respuesta = fe.consulta_reclamo_documento(datos)[key]
        resp_data = respuesta.get('respuesta', False)
        if resp_data and resp_data.codResp in [15, 16]:
            self.en_sii = "si"
        elif resp_data and resp_data.codResp in [10, 18]:
            self.en_sii = "no"
        return respuesta

    def get_dte_claim(self):
        try:
            respuesta = self._consulta_eventos_sii()
            self.claim_description = respuesta
            claim_anterior = self.claim
            resp_data = respuesta.get('respuesta', False)
            if resp_data and resp_data.codResp in [15]:
                for res in resp_data.listaEventosDoc:
                    if self.claim != "ACD":
                        if self.claim != "ERM":
                            self.claim = res.codEvento
            date_end = self.create_date + relativedelta(days=8)
            if self.claim in ["ACD", "ERM", "PAG"]:
                self.state = "accepted"
            elif self.claim in ["RCD", "RFP", "RFT"]:
                self.state = "rejected"
            elif date_end <= datetime.now() and self.claim == "N/D":
                self.state = "accepted"
            elif self.claim in ["RCD", "RFP", "RFT"]:
                self.state = "rejected"
        except Exception as e:
            _logger.warning("Error al obtener aceptación %s" % (str(e)), exc_info=True)
            if self.company_id.dte_service_provider == "SII":
                raise UserError("Error al obtener aceptación: %s" % str(e))

    def action_verificar_sii(self):
        for r in self.filtered(lambda d: d.source != "rcv" and d.document_class_id.dte):
            try:
                with self.env.cr.savepoint():
                    r._consulta_eventos_sii()
            except Exception as e:
                _logger.warning("No se pudo verificar en el SII el documento %s: %s", r.id, e)

    @api.model
    def _cron_verificar_sii(self, limit=50):
        self.search([
            ("en_sii", "!=", "si"),
            ("source", "!=", "rcv"),
            ("document_class_id.dte", "=", True),
            ("create_date", ">=", fields.Datetime.subtract(fields.Datetime.now(), days=90)),
        ], order="en_sii desc, id", limit=limit).action_verificar_sii()

    def get_claim(self):
        date_end = self.create_date + relativedelta(days=8)
        if date_end <= datetime.now() and self.claim == "N/D":
            return self.accept_document()
        self.get_dte_claim()


class ProcessMailsDocumentLines(models.Model):
    _name = "mail.message.dte.document.line"
    _description = "Pre Document Line"
    _order = "sequence, id"

    name = fields.Char(string="Nombre en xml")
    document_id = fields.Many2one("mail.message.dte.document", string="Documento", ondelete="cascade",)
    sequence = fields.Integer(string="Número de línea", default=1, readonly=True)
    product_id = fields.Many2one("product.product", string="Producto",)
    new_product = fields.Char(string="Nuevo Producto", readonly=True,)
    description = fields.Char(string="Descripción", readonly=True,)
    product_description = fields.Char(string="Descripción Producto", readonly=True,)
    quantity = fields.Float(string="Cantidad", readonly=True,)
    price_unit = fields.Monetary(string="Precio Unitario", readonly=True,)
    discount = fields.Float(string='Discount (%)', readonly=True, digits='Discount', default=0.0,)
    price_subtotal = fields.Monetary(string="Total", readonly=True,)
    product_uom_id = fields.Many2one('uom.uom', string='Unit of Measure', readonly=True)
    currency_id = fields.Many2one(
        "res.currency", string="Moneda", readonly=True, default=lambda self: self.env.company.currency_id,
    )
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
    company_id = fields.Many2one(
        related='document_id.company_id', store=True, readonly=True, precompute=True,
        index=True,
    )
    tax_ids = fields.Many2many(
        comodel_name='account.tax',
        string="Taxes",
        readonly=True,
        context={'active_test': False},
        check_company=True,
    )
    purchase_line_id = fields.Many2one('purchase.order.line', string="Línea de Orden de Compra")
    create_pol = fields.Boolean(
        string="Crear Línea Orden de Compra",
    )
    create_move_line = fields.Boolean(
        string="Crear Línea de Movimiento",
    )

    @api.onchange('purchase_line_id')
    def _onchange_purchase_line_id(self):
        if self.purchase_line_id:
            self.product_id = self.purchase_line_id.product_id

class MMDTEDGlobalDescuentoRecargo(models.Model):
    _name = "mail.message.dte.document.gdr"
    _description = "Linea de descuento global dte document"

    def _get_name(self):
        for g in self:
            type = "Descuento"
            if g.type == "R":
                type = "Recargo"
            calculo = "Porcentaje"
            if g.gdr_type == "amount":
                calculo = "Monto"
            g.name = type + "-" + calculo + ": " + (g.gdr_detail or "")

    name = fields.Char(compute="_get_name", string="Name")
    type = fields.Selection(
        [("D", "Descuento"), ("R", "Recargo"),],
        string="Seleccione Descuento/Recargo Global",
        default="D",
        required=True,
    )
    valor = fields.Float(
        string="Descuento/Recargo Global", default=0.00, required=True, digits="Global DR"
    )
    gdr_type = fields.Selection(
        [("amount", "Monto"), ("percent", "Porcentaje"),], string="Tipo de descuento", default="percent", required=True,
    )
    gdr_detail = fields.Char(string="Razón del descuento")
    amount_untaxed_global_dr = fields.Float(string="Descuento/Recargo Global", default=0.00)
    aplicacion = fields.Selection([("flete", "Flete"), ("seguro", "Seguro"),], string="Aplicación del Desc/Rec",)
    impuesto = fields.Selection(
        [("afectos", "Solo Afectos"), ("exentos", "Solo Exentos"), ("no_facturables", "Solo No Facturables")],
        default="afectos",
    )
    document_id = fields.Many2one("mail.message.dte.document", string="DTE", copy=False,)
    account_id = fields.Many2one('account.account', string='Account',
        company_dependent=True,
        domain="[('active', '=', True), ('company_ids', 'parent_of', current_company_id)]",
        #default=lambda self: self.move_id.journal_id.default_gd_account_id if self.type == 'D' else self.move_id.journal_id.default_gr_account_id
    )
