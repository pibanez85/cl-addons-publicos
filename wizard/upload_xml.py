import base64
import logging

from facturacion_electronica import facturacion_electronica as fe
from lxml import etree

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.translate import _

from ..models.mail_message_dte_document import clave_rut

TASAS_IMPUESTO_ADICIONAL = {"24": 31.5, "25": 20.5, "26": 20.5, "27": 10.0, "271": 18.0}

_logger = logging.getLogger(__name__)


class UploadXMLWizard(models.TransientModel):
    _name = "sii.dte.upload_xml.wizard"
    _description = "SII XML from Provider"

    action = fields.Selection(
        [('manual', 'Manual'),("create_po", "Crear Orden de Pedido"), ("create_move", "Crear Solamente Factura"), ("both", "Crear Ambas")],
        string="Acción",
        default="create_move",
    )
    xml_file = fields.Binary(string="XML File", store=True, help="Upload the XML File in this holder",)
    filename = fields.Char(string="File Name",)
    pre_process = fields.Boolean(default=True,)
    crear_po = fields.Boolean(default=False,)
    dte_id = fields.Many2one("mail.message.dte", string="DTE",)
    document_id = fields.Many2one("mail.message.dte.document", string="Documento",)
    option = fields.Selection(
        [("upload", "Solo Subir"), ("accept", "Aceptar"), ("reject", "Rechazar"),], string="Opción", default='upload'
    )
    num_dtes = fields.Integer(string="Número de DTES", readonly=True,)
    type = fields.Selection(
        [("ventas", "Ventas"), ("compras", "Compras"),], string="Tipo de Operación", default="compras",
    )
    purchase_to_done = fields.Many2one('purchase.order', 'Orden de Compra a Validar')

    @api.onchange("xml_file")
    def get_num_dtes(self):
        if self.xml_file:
            self.num_dtes = len(self._get_dtes())

    @api.model
    def create_from_attachment(self, b64, filename, journal_id=False):
        wiz = self.create({
            "xml_file": b64,
            "filename": filename,
            "type": "compras",
        })
        wiz.get_num_dtes()
        ctx = dict(self.env.context)
        if journal_id:
            ctx["default_journal_id"] = journal_id
        return {
            "type": "ir.actions.act_window",
            "name": _("Recepción XML DTE"),
            "res_model": "sii.dte.upload_xml.wizard",
            "res_id": wiz.id,
            "view_mode": "form",
            "target": "new",
            "context": ctx,
        }

    def _con_companias_del_usuario(self):
        return self.with_context(allowed_company_ids=(self.env.companies | self.env.user.company_ids).ids)

    def confirm(self, ret=False):
        self = self._con_companias_del_usuario()
        bhe = self._bhe()
        if bhe is not None:
            created = self._crear_bhe(bhe).ids
            if ret:
                return created
            return {
                "type": "ir.actions.act_window",
                "name": _("List of Results"),
                "view_mode": "tree,form",
                "res_model": "account.move",
                "domain": str([("id", "in", created)]),
                "target": "current",
            }
        created = []
        if self.document_id:
            self.dte_id = self.document_id.dte_id.id
        if not self.dte_id:
            dte_id = self.env["mail.message.dte"].search([("name", "=", self.filename),], limit=1,)
            if not dte_id:
                dte = {
                    "name": self.filename,
                }
                dte_id = self.env["mail.message.dte"].create(dte)
                att = self._create_attachment(self._get_xml(), self.filename, dte_id.id, "mail.message.dte", False)
                dte_id.xml_id = att
            self.dte_id = dte_id
        if self.type == "ventas":
            self.pre_process = False
            created = self.do_create_inv()
            xml_id = "account.view_invoice_tree"
            target_model = "account.move"
        else:
            if self.option == "reject":
                self.do_reject()
                return
            if not self.document_id:
                self.pre_process = True
                self.crear_po = False
                created_pre = self.do_create_pre()
            if self.action != 'manual' and self.option == 'accept':
                self.pre_process = False
                if self.action in ["create_po", "both"]:
                    self.crear_po = True
                    created = self.do_create_po()
                    xml_id = "purchase.purchase_order_tree"
                    target_model = "purchase.order"
                if self.action in ["create_move", "both"]:
                    self.crear_po = False
                    created = self.do_create_inv()
                    xml_id = "account.view_invoice_tree"
                    target_model = "account.move"
            if not self.document_id and self.action in ['manual', 'both', 'create_move']:
                created = created_pre
                xml_id = "l10n_cl_fe.dte_document_view_tree"
                target_model = "mail.message.dte.document"
        if ret:
            return created

        return {
            "type": "ir.actions.act_window",
            "name": _("List of Results"),
            "view_mode": "tree",
            "res_model": target_model,
            "domain": str([("id", "in", created)]),
            "views": [(self.env.ref("%s" % xml_id).id, "tree")],
            "target": "current",
        }

    def format_rut(self, RUTEmisor=None):
        rut = RUTEmisor.replace("-", "")
        if len(rut) < 9:
            return "CL0" + rut
        return "CL" + rut

    def _get_xml(self):
        return base64.b64decode(self.xml_file).decode("ISO-8859-1")\
        .replace('<ds:','<')\
        .replace('</ds:','</')\
        .replace(':ds', '')

    def _get_xml_name(self):
        return self.filename or self.dte_id.name

    def _read_xml(self, mode="text", check=False):
        xml = (
            self._get_xml()
            .replace('<?xml version="1.0" encoding="ISO-8859-1"?>', "")
            .replace('<?xml version="1.0" encoding="ISO-8859-1" ?>', "")
        )
        if check:
            return xml
        xml = xml.replace('xmlns="http://www.sii.cl/SiiDte"', "")
        if mode == "etree":
            parser = etree.XMLParser(remove_blank_text=True)
            return etree.fromstring(xml, parser=parser)
        return xml

    def _resolve_firma(self, company_id):
        firma_id = self.env.context.get('firma_id')
        if firma_id:
            return self.env['sii.firma'].sudo().browse(firma_id)
        firma = self.env.user.get_digital_signature(company_id)
        if not firma:
            raise UserError(_(
                "El usuario '%s' no está autorizado a usar firma electrónica para %s."
            ) % (self.env.user.name, company_id.name))
        return firma

    def _get_datos_empresa(self, company_id):
        firma = self._resolve_firma(company_id)
        return {
            "Emisor": {
                "RUTEmisor": company_id.partner_id.rut(),
                "Modo": "produccion" if company_id.dte_service_provider == "SII" else "certificacion",
            },
            "firma_electronica": firma.parametros_firma(),
        }

    def _create_attachment(self, xml, name, id=False, model="account.move", url=True):
        data = base64.b64encode(xml.encode("ISO-8859-1"))
        filename = (name + ".xml").replace(" ", "")
        if url:
            url_path = "/download/xml/resp/%s" % (id)
        att = self.env["ir.attachment"].search(
            [("name", "=", filename), ("res_id", "=", id), ("res_model", "=", model)], limit=1
        )
        if att:
            return att
        values = dict(
            name=filename, res_model=model, res_id=id, type="binary", datas=data,
        )
        if url:
             values["url"] = url_path
        att = self.env["ir.attachment"].create(values)
        return att

    def do_receipt_deliver(self):
        envio = self._read_xml("etree")
        if envio.find("SetDTE") is None or envio.find("SetDTE/Caratula") is None:
            return True
        company_id = self.env["res.company"].search(
            [("vat", "=", self.format_rut(envio.find("SetDTE/Caratula/RutReceptor").text))], limit=1
        )
        IdRespuesta = self.env.ref("l10n_cl_fe.response_sequence").next_by_id()
        try:
            vals = self._get_datos_empresa(company_id)
        except UserError as e:
            if not self.dte_id:
                raise
            self.dte_id.message_post(body=str(e))
            return
        vals.update(
            {
                "Recepciones": [
                    {
                        "IdRespuesta": IdRespuesta,
                        "RutResponde": company_id.partner_id.rut(),
                        "NmbContacto": self.env.user.partner_id.name,
                        "FonoContacto": self.env.user.partner_id.phone,
                        "MailContacto": self.env.user.partner_id.email,
                        "xml_nombre": self._get_xml_name(),
                        "xml_envio": self._get_xml(),
                    }
                ]
            }
        )
        respuesta = fe.recepcion_xml(vals)
        if type(respuesta) is dict:
            error = respuesta.get('error') or "No se pudo generar la respuesta de recepción"
            if not self.dte_id:
                raise UserError(error)
            self.dte_id.message_post(body=error)
            return
        if self.dte_id:
            for r in respuesta:
                att = self._create_attachment(r["respuesta_xml"], r["nombre_xml"], self.dte_id.id, "mail.message.dte")
                dte_email_id = self.dte_id.company_id.dte_email_id or self.env.company.dte_email_id
                email_to = self.sudo().dte_id.mail_id.email_from
                if envio is not None:
                    RUT = envio.find("SetDTE/Caratula/RutEmisor").text
                    partner_id = self.env["res.partner"].search(
                        [("active", "=", True), ("parent_id", "=", False), ("vat", "=", self.format_rut(RUT))]
                    )
                    if partner_id.dte_email:
                        email_to = partner_id.dte_email
                values = {
                    "res_id": self.dte_id.id,
                    "email_from": dte_email_id.display_name,
                    "email_to": email_to,
                    "auto_delete": False,
                    "model": "mail.message.dte",
                    "body": "XML de Respuesta Envío, Estado: %s , Glosa: %s "
                    % (r["EstadoRecepEnv"], r["RecepEnvGlosa"]),
                    "subject": "XML de Respuesta Envío",
                    "attachment_ids": [[6, 0, att.ids]],
                }
                send_mail = self.env["mail.mail"].sudo().create(values)
                send_mail.send()

    def _get_data_partner(self, data):
        if self.pre_process and self.type == "compras":
            return False
        type = "Emis"
        if self.type == "ventas":
            type = "Recep"
            if data.find("RUT%s" % type).text in [False, "66666666-6", "00000000-0"]:
                return self.env.ref("l10n_cl_fe.par_cfa")
        el = data.find("Giro%s" % type)
        if el is None:
            giro = "Boleta"
        else:
            giro = el.text
        giro_id = self.env["sii.activity.description"].search([("name", "=", giro)])
        if not giro_id:
            giro_id = self.env["sii.activity.description"].create({"name": giro,})
        type = "Emisor"
        dest = "Origen"
        rut_path = "RUTEmisor"
        if self.type == "ventas":
            type = "Receptor"
            dest = "Recep"
            rut_path = "RUTRecep"
        rut = self.format_rut(data.find(rut_path).text)
        name = (
            (data.find("RznSoc").text or data.find("RznSocEmisor").text)
            if self.type == "compras"
            else data.find("RznSocRecep").text
        )
        city_id = self.env["res.city"].search([("name", "=", data.find("Cmna%s" % dest).text.title())])
        ciudad = data.find("Ciudad%s" % dest)
        partner = {
            "name": name,
            "activity_description": giro_id.id,
            "vat": rut,
            "document_type_id": self.env.ref("l10n_cl_fe.dt_RUT").id,
            "responsability_id": self.env.ref("l10n_cl_fe.res_IVARI").id,
            "document_number": data.find(rut_path).text,
            "street": data.find("Dir%s" % dest).text,
            "city": ciudad.text if ciudad is not None else city_id.name,
            "company_type": "company",
            "city_id": city_id.id,
            "country_id": self.env.ref('base.cl').id,
            "es_mipyme": False,
        }
        if data.find("CorreoEmisor") is not None or data.find("CorreoRecep") is not None:
            partner.update(
                {
                    "email": data.find("CorreoEmisor").text
                    if self.type == "compras"
                    else data.find("CorreoRecep").text,
                    "dte_email": data.find("CorreoEmisor").text
                    if self.type == "compras"
                    else data.find("CorreoRecep").text,
                }
            )
            if '@sii.cl' in partner['dte_email'].lower():
                del partner['dte_email']
                partner['es_mipyme'] = True
        return partner

    def _create_partner(self, data):
        partner_id = False
        partner = self._get_data_partner(data)
        if partner:
            partner_id = self.env["res.partner"].create(partner)
        return partner_id

    def _default_category(self):
        res = False
        try:
            res = self.env.ref("product.product_category_all").id
        except ValueError:
            res = False
        return res

    def _crear_impuesto_plantilla(self, type, amount, sii_code, company_id):
        if company_id.chart_template != 'cl':
            return False
        CT = self.env['account.chart.template'].sudo().with_company(company_id).with_context(
            default_company_id=company_id.id, allowed_company_ids=[company_id.id],
            lang='en_US', tracking_disable=True)
        data = CT._get_chart_template_data('cl')
        xmlid = next((x for x, v in data['account.tax'].items()
                      if v.get('type_tax_use') == type and str(v.get('sii_code')) == str(sii_code)
                      and float(v.get('amount') or 0) == float(amount)), None)
        if not xmlid or CT.ref(xmlid, raise_if_not_found=False):
            return False
        vals = data['account.tax'][xmlid]
        carga = {'account.tax.group': {}, 'account.account': {}, 'account.tax': {xmlid: vals}}
        grupo = vals.get('tax_group_id')
        if grupo and not CT.ref(grupo, raise_if_not_found=False):
            carga['account.tax.group'][grupo] = data['account.tax.group'][grupo]
        for linea in vals.get('repartition_line_ids') or []:
            cuenta = linea[2].get('account_id') if len(linea) > 2 and isinstance(linea[2], dict) else False
            if not cuenta or cuenta in carga['account.account'] or CT.ref(cuenta, raise_if_not_found=False):
                continue
            existente = self.env['account.account'].sudo().with_company(company_id).search(
                [('company_id', '=', company_id.id), ('code', '=', data['account.account'][cuenta]['code'])], limit=1)
            if existente:
                self.env['ir.model.data'].sudo()._update_xmlids([{
                    'xml_id': 'account.%s_%s' % (company_id.id, cuenta), 'record': existente, 'noupdate': True}])
            else:
                carga['account.account'][cuenta] = data['account.account'][cuenta]
        CT._load_data(carga)
        return CT.ref(xmlid)

    def _buscar_impuesto(self, type="purchase", name="Impuesto", amount=0,
                        sii_code=0, ind_exe=False, company_id=False, refund=False):
        target = 'tax_id.'
        query = [
            ("document_type", "=", "refund" if refund else "invoice"),
            (target + "amount", "=", amount),
            (target + "sii_code", "=", sii_code),
            (target + "type_tax_use", "=", type),
            (target + "activo_fijo", "=", False),
            (target + "company_id", "=", company_id.id),
            (target + "ind_exe", '=', ind_exe),
            ("credec", '=', False),
        ]
        if amount == 0 and sii_code == 0:
            name = "Exento Venta"
            if type == "purchase":
                name = "Exento Compra"
            query.append(( target + "name", "=", name))
        elif amount != 19 and sii_code not in [14, 15] and str(sii_code) not in TASAS_IMPUESTO_ADICIONAL:
            query.append((target + "name", "=", name))
        imp = self.env["account.tax.repartition.line"].search(
            query + [(target + "active", "=", True), ("account_id", "!=", False)], limit=1
        ) or self.env["account.tax.repartition.line"].search(query + [(target + "active", "=", True)], limit=1)
        if not imp:
            imp = self._crear_impuesto_plantilla(type, amount, sii_code, company_id)
            if imp:
                return imp
        if not imp and type == "purchase" and str(sii_code) in TASAS_IMPUESTO_ADICIONAL:
            raise UserError(_("No existe un impuesto de compra activo con código SII %s y tasa %s%%") % (sii_code, amount))
        if not imp:
            return self.env["account.tax"].sudo().create(
                    {
                        "amount": amount,
                        "name": name,
                        "sii_code": sii_code,
                        "type_tax_use": type,
                        "ind_exe": ind_exe,
                        "company_id": company_id.id,
                    }
                )
        return imp.tax_id

    def get_product_values(self, line, company_id, price_included=False,
                           exenta=False, refund=False):
        IndExe = line.find("IndExe")
        amount = 0
        sii_code = 0
        if IndExe is None and not exenta:
            amount = 19
            sii_code = 14
        ind_exe = IndExe.text if IndExe is not None else False
        imp = self._buscar_impuesto(amount=amount,
                                    type="purchase",
                                    sii_code=sii_code,
                                    ind_exe=ind_exe,
                                    company_id=company_id,
                                    refund=refund)
        imp_sale = self._buscar_impuesto(amount=amount,
                                    type="sale",
                                    sii_code=sii_code,
                                    ind_exe=ind_exe,
                                    company_id=company_id,
                                    refund=refund)
        uom = 'UnmdItem'
        price = float(line.find("PrcItem").text if line.find("PrcItem") is not None else line.find("MontoItem").text)
        if price_included:
            price = imp.compute_all(price, company_id.currency_id, 1)["total_excluded"]
        values = {
            "sale_ok": self.type == "ventas",
            "name": line.find("NmbItem").text,
            "lst_price": price,
            "categ_id": self._default_category(),
            "taxes_id": [(6, 0, imp_sale.ids)],
            "supplier_taxes_id": [(6, 0, imp.ids)],
            "purchase_ok": self.type != "ventas",
        }
        for c in line.findall("CdgItem"):
            VlrCodigo = c.find("VlrCodigo").text
            if c.find("TpoCodigo").text == "ean13":
                values["barcode"] = VlrCodigo
            else:
                values["default_code"] = VlrCodigo
        return values

    def _create_prod(self, data, company_id, price_included=False, exenta=False,
                     refund=False):
        product_id = self.env["product.product"].create(
            self.get_product_values(data, company_id, price_included, exenta,
                                    refund)
        )
        return product_id

    def _buscar_producto(self, line, company_id,
                        price_included=False, exenta=False, refund=False):
        default_code = False
        CdgItem = line.find("CdgItem")
        NmbItem = line.find("NmbItem").text
        if NmbItem.isspace():
            NmbItem = "Producto Genérico"
        query = False
        product_id = self.env["product.product"]
        if CdgItem is not None:
            for c in line.findall("CdgItem"):
                VlrCodigo = c.find("VlrCodigo")
                if VlrCodigo is None or VlrCodigo.text is None or VlrCodigo.text.isspace():
                    continue
                TpoCodigo = c.find("TpoCodigo").text
                if TpoCodigo == "ean13":
                    query = [("barcode", "=", VlrCodigo.text)]
                elif TpoCodigo == "INT1":
                    query = [("default_code", "=", VlrCodigo.text)]
                default_code = VlrCodigo.text
        product_supplier = False
        proveedor = self.document_id.partner_id.commercial_partner_id
        if self.type == "compras" and proveedor:
            SupplierInfo = self.env["product.supplierinfo"]
            dominio = [("partner_id", "in", (proveedor | proveedor.child_ids).ids)]
            if default_code:
                product_supplier = SupplierInfo.search(dominio + [("product_code", "=", default_code)], limit=1)
            if not product_supplier:
                product_supplier = SupplierInfo.search(dominio + [("product_name", "=", NmbItem)], limit=1)
            if product_supplier and not product_supplier.product_tmpl_id.active:
                raise UserError(_("Plantilla Producto para el proveedor marcado como archivado"))
            product_id = product_supplier.product_id or product_supplier.product_tmpl_id.product_variant_id
        if not product_id:
            product_id = self.env["product.product"].search(query or [("name", "=", NmbItem)], limit=1)
        if not product_id and self.type == "compras":
            code = ""
            coma = ""
            for c in line.findall("CdgItem"):
                if c.find("TpoCodigo").text:
                    code += coma + c.find("TpoCodigo").text + " "
                if c.find("VlrCodigo").text:
                    code += c.find("VlrCodigo").text
                coma = ", "
            return NmbItem + code if code else NmbItem
        elif self.type == "ventas" and not product_id:
            product_id = self._create_prod(line, company_id, price_included,
                                           exenta, refund)
        if not product_supplier and proveedor and self.type == "compras":
            product_supplier = self.env["product.supplierinfo"].search([
                ("partner_id", "=", proveedor.id),
                ("product_tmpl_id", "=", product_id.product_tmpl_id.id),
            ], limit=1)
            if product_supplier and not product_supplier.product_code and not product_supplier.product_name:
                product_supplier.write({"product_code": default_code, "product_name": NmbItem})
        if not product_supplier and proveedor and self.type == "compras":
            price = float(
                line.find("PrcItem").text if line.find("PrcItem") is not None else line.find("MontoItem").text
            )
            if price_included:
                price = product_id.supplier_taxes_id.compute_all(price, company_id.currency_id, 1)[
                    "total_excluded"
                ]
            supplier_info = {
                "partner_id": proveedor.id,
                "product_name": NmbItem,
                "product_code": default_code,
                "product_tmpl_id": product_id.product_tmpl_id.id,
                "price": price,
                "product_id": product_id.id,
            }
            self.env["product.supplierinfo"].create(supplier_info)
        if not product_id.active:
            raise UserError(_("Producto para el proveedor marcado como archivado"))
        return product_id

    def _buscar_purchase_line_id(self, line_new, product=False):
        if not self.purchase_to_done:
            return self.env['purchase.order.line']
        lines = self.purchase_to_done.order_line
        if product:
            del_producto = lines.filtered(lambda l: l.product_id == product)
            if del_producto:
                return (del_producto.filtered(lambda l: l.qty_invoiced < l.product_qty) or del_producto)[:1]
        DscItem = line_new.find("DscItem")
        name = DscItem.text if DscItem is not None else line_new.find("NmbItem").text
        for line in lines:
            if (line.name or "").strip().upper() == (name or "").strip().upper():
                return line
        return self.env['purchase.order.line']

    def _prepare_line(self, line, move_type, company_id, fpos_id,
                      price_included=False, exenta=False):
        line_id = self.env["mail.message.dte.document.line"]
        create_line = True
        if self.document_id:
            line_id = line_id.search(
                [
                    ("sequence", "=", line.find("NroLinDet").text),
                    ("document_id", "=", self.document_id.id),
                ],
                order="id",
                limit=1,
            )
            if not self.crear_po and not line_id.create_move_line:
                create_line = False
            if self.crear_po and not line_id.create_pol:
                create_line = False
        if not create_line:
            return False
        no_facturable = self.type == "compras" and not self.crear_po and not self.pre_process and line.findtext("IndExe") in ("2", "6")
        if self.type == "compras" and not self.crear_po and not self.pre_process and line.findtext("IndExe") in ("4", "5"):
            return False
        refund = move_type in ['out_refund', 'in_refund']
        data = {}
        product_id = line_id.product_id or self._buscar_producto(
                                        line, company_id,
                                        price_included, exenta, refund)
        uom_id = False
        if not isinstance(product_id, str):
            data.update(
                {"product_id": product_id.id,}
            )
            uom_id = product_id.uom_id.id
        elif not product_id:
            return False
        price_subtotal = float(line.find("MontoItem").text)
        price = float(line.find("PrcItem").text) if line.find("PrcItem") is not None else price_subtotal
        DscItem = line.find("DscItem")
        IndExe = line.find("IndExe")
        ind_exe = IndExe.text if IndExe is not None else False
        qty_field = "product_qty" if self.crear_po else "quantity"
        data.update(
            {
                "sequence": line.find("NroLinDet").text,
                "price_unit": price,
                qty_field: line.find("QtyItem").text if line.find("QtyItem") is not None else 1,
                "price_subtotal": price_subtotal,
            }
        )
        if self.pre_process:
            if isinstance(product_id, str):
                data.update({
                    "new_product": product_id,
                    "product_description": DscItem.text if DscItem is not None else "",
                })
            data.update(
                {
                    "create_move_line": self.action in ['create_move', 'both'] or self.pre_process,
                    "create_pol": self.action in ['create_po', 'both'] or self.pre_process,
                }
            )
        amount = 0
        sii_code = 0
        tax_ids = self.env["account.tax"]
        if IndExe is None and not exenta:
            amount = 19
            sii_code = 14
        tax_ids += self._buscar_impuesto(
            type="purchase" if self.type == "compras" else "sale",
            amount=amount, sii_code=sii_code, ind_exe=ind_exe,
            company_id=company_id,
            refund=refund
        )
        if line.find("CodImpAdic") is not None:
            amount = TASAS_IMPUESTO_ADICIONAL.get(line.find("CodImpAdic").text, 19)
            tax_ids += self._buscar_impuesto(
                type="purchase" if self.type == "compras" else "sale",
                amount=amount, sii_code=line.find("CodImpAdic").text,
                company_id=company_id,
                refund=refund
            )
        if IndExe is None:
            tax_include = False
            for t in tax_ids:
                if not tax_include:
                    tax_include = t.price_include
            if price_included and not tax_include:
                base = price
                price = 0
                base_subtotal = price_subtotal
                price_subtotal = 0
                for t in tax_ids:
                    if t.amount > 0:
                        price += base / (1 + (t.amount / 100.0))
                        price_subtotal += base_subtotal / (1 + (t.amount / 100.0))
            elif not price_included and tax_include:
                price = tax_ids.compute_all(price, company_id.currency_id, 1)["total_included"]
                price_subtotal = tax_ids.compute_all(price_subtotal, company_id.currency_id, 1)[
                    "total_included"
                ]
        data.update(
            {
                "name": DscItem.text if DscItem is not None else (line.find("NmbItem").text if line.find("NmbItem") is not None else "Línea %s" % line.find("NroLinDet").text),
                "price_unit": price,
                "price_subtotal": price_subtotal,
            }
        )
        if self.crear_po:
            data.update({
                "product_uom": uom_id,
                "taxes_id": [(6, 0, tax_ids.ids)],
            })
        else:
            discount = 0
            if line.find("DescuentoPct") is not None:
                discount = float(line.find("DescuentoPct").text)
            cantidad = float(line.find("QtyItem").text) if line.find("QtyItem") is not None else 1.0
            bruto = cantidad * float(line.find("PrcItem").text) if line.find("PrcItem") is not None else 0.0
            monto = float(line.find("MontoItem").text)
            if bruto and monto <= bruto and abs(bruto * (1 - discount / 100) - monto) >= 0.5:
                discount = 0
                data["price_unit"] = price_subtotal / cantidad
            purchase_line_id = line_id.purchase_line_id
            if not purchase_line_id:
                purchase_line_id = self._buscar_purchase_line_id(
                    line, product_id if not isinstance(product_id, str) else False
                )
            data.update({
                "tax_ids": [(6, 0, tax_ids.ids)],
                "product_uom_id": uom_id,
                "discount": discount,
                "purchase_line_id": purchase_line_id.id,
                "ind_exe": ind_exe,
            })
            if no_facturable:
                signo = -1 if line.findtext("IndExe") == "6" else 1
                data.update({
                    "product_id": False,
                    "product_uom_id": False,
                    "purchase_line_id": False,
                    "tax_ids": [(6, 0, [])],
                    "discount": 0,
                    "price_unit": signo * abs(float(line.findtext("MontoItem"))),
                    "price_subtotal": signo * abs(float(line.findtext("MontoItem"))),
                    "quantity": 1,
                })
        return data

    def _create_tpo_doc(self, TpoDocRef, RazonRef=None):
        vals = dict(name=str(TpoDocRef), dte=False)
        if RazonRef is not None:
            vals["name"] = "{} {}".format(vals["name"], RazonRef.text)
        if str(TpoDocRef).isdigit():
            vals.update(
                {"sii_code": TpoDocRef,}
            )
        else:
            vals.update(
                {"doc_code_prefix": TpoDocRef, "sii_code": 801, "use_prefix": True,}
            )
        return self.env["sii.document_class"].create(vals)

    def _procesar_po_to_done(self, vals, company_id):
        seq = self.env['ir.sequence'].search([('code', '=', 'purchase.order'), ('company_id', 'in', [company_id.id, False])], order='company_id')
        self.purchase_to_done = self.env['purchase.order'].search([
            ('name', '=', seq.get_next_char(
                int(vals['folio'].upper().replace(seq.prefix, '').replace(' ', ''))))
        ])

    def _prepare_ref(self, ref, company_id=False):
        query = []
        TpoDocRef = ref.find("TpoDocRef").text
        RazonRef = ref.find("RazonRef")
        if str(TpoDocRef).isdigit():
            query.append(("sii_code", "=", TpoDocRef))
            query.append(("use_prefix", "=", False))
        else:
            query.append(("doc_code_prefix", "=", TpoDocRef))
        tpo = self.env["sii.document_class"].search(query, limit=1)
        if not tpo:
            tpo = self._create_tpo_doc(TpoDocRef, RazonRef)
        vals = {
            "folio": ref.find("FolioRef").text,
            "ref_id": tpo.id,
            "cod_ref": ref.find("CodRef").text if ref.find("CodRef") is not None else None,
            "motivo": RazonRef.text if RazonRef is not None else None,
            "fecha_documento": ref.find("FchRef").text if ref.find("FchRef") is not None else None,
        }
        if tpo.doc_code_prefix == 'OC':
            self._procesar_po_to_done(vals, company_id)
        return [0,0, vals]

    def process_dr(self, dr, journal_id=False):
        data = {
            "type": dr.find("TpoMov").text,
        }
        disc_type = "percent"
        if dr.find("TpoValor").text == "$":
            disc_type = "amount"
        data["gdr_type"] = disc_type
        data["valor"] = dr.find("ValorDR").text
        data["gdr_detail"] = dr.find("GlosaDR").text if dr.find("GlosaDR") is not None else "Descuento global"
        if journal_id:
            data['account_id'] = journal_id.default_gd_account_id.id if data['type'] == 'D' else journal_id.default_gr_account_id.id
            if not data['account_id']:
                raise UserError("No tiene cuenta contable seleccionada para  %s" % data["gdr_detail"])
        return data

    def _purchase_partner_ref(self, dc_id, Folio):
        return  "%s%s" % (dc_id.doc_code_prefix, Folio)

    def _prepare_data(self, documento, company_id, journal_id):
        type = "Emisor"
        rut_path = "RUTEmisor"
        if self.type == "ventas":
            type = "Receptor"
            rut_path = "RUTRecep"
        Encabezado = documento.find("Encabezado")
        IdDoc = Encabezado.find("IdDoc")
        dc_id = self.env["sii.document_class"].search([("sii_code", "=", IdDoc.find("TipoDTE").text)])
        Emisor = Encabezado.find(type)
        RUT = Emisor.find(rut_path).text
        data = {}
        partner_id = self.env["res.partner"].search(
            [("active", "=", True), ("parent_id", "=", False), ("vat", "=", self.format_rut(RUT))]
        )
        if not partner_id:
            partner_id = self._create_partner(Encabezado.find("%s" % type))
        if self.crear_po and not partner_id:
            raise UserError("No se encuentra el partner ")
        if not self.crear_po and not self.pre_process:
            data["move_type"] = "in_invoice"
            if self.type == "ventas":
                data["move_type"] = "out_invoice"
            if dc_id.es_nc() or dc_id.es_nd():
                data["move_type"] = "in_refund"
                if self.type == "ventas":
                    data["move_type"] = "out_refund"
        if partner_id:
            partner_id = partner_id.id
        try:
            name = self.filename.decode("ISO-8859-1").encode("UTF-8")
        except:
            name = self.filename.encode("UTF-8")
        ted_string = b""
        if documento.find("TED") is not None:
            ted_string = etree.tostring(documento.find("TED"), method="c14n", pretty_print=False)
        FchEmis = IdDoc.find("FchEmis").text
        data.update(
            {
                "partner_id": partner_id,
                "company_id": company_id.id,
            }
        )
        if self.crear_po:
            data["date_order"] = FchEmis
        else:
            data["date"] = FchEmis
        if not self.pre_process and not self.crear_po:
            data.update({
                "sii_xml_dte": "<DTE>%s</DTE>" % etree.tostring(documento).decode('ISO-8859-1'),
                "invoice_origin": "XML Envío: " + name.decode(),
                "sii_barcode": ted_string.decode(),
                "invoice_date": FchEmis,
                "use_documents": self.type=='ventas',
            })
        if journal_id:
            data["journal_id"] = journal_id.id
        if not self.crear_po:
            DscRcgGlobal = documento.findall("DscRcgGlobal")
            if DscRcgGlobal:
                drs = [(5,)]
                for dr in DscRcgGlobal:
                    drs.append((0, 0, self.process_dr(dr, journal_id)))
                data.update(
                    {"global_descuentos_recargos": drs,}
                )
        Folio = IdDoc.find("Folio").text
        if self.crear_po:
            data["partner_ref"] = self._purchase_partner_ref(dc_id, Folio)
            return data
        data["document_class_id"] = dc_id.id
        if not self.pre_process:
            data["sii_document_number"] =  Folio
        if self.pre_process:
            RznSoc = Emisor.find("RznSoc")
            if RznSoc is None:
                RznSoc = Emisor.find("RznSocEmisor")
            monto_no_facturable = Encabezado.find("Totales/MontoNF").text if Encabezado.find("Totales/MontoNF") is not None else 0
            action = "both" if self.action == "manual" else self.action
            data.update(
                {
                    "number": Folio,
                    "new_partner": RUT + " " + RznSoc.text,
                    "amount": Encabezado.find("Totales/MntTotal").text,
                    "monto_no_facturable": monto_no_facturable,
                    "action": action,
                }
            )
        Referencias = documento.findall("Referencia")
        if not self.pre_process and Referencias:
            refs = [(5,)]
            for ref in Referencias:
                refs.append(self._prepare_ref(ref, company_id))
            data["referencias"] = refs
        return data

    def _get_journal(self, sii_code, company_id, ignore_journal=False):
        if self.crear_po:
            return self.env["account.journal"]
        if self.document_id and self.document_id.journal_id:
            return self.document_id.journal_id
        dc_id = self.env["sii.document_class"].search([("sii_code", "=", sii_code)])
        type = "purchase"
        query = [("company_id", "=", company_id.id),]
        if self.type == "ventas":
            type = "sale"
            query.append(("journal_document_class_ids.sii_document_class_id", "=", dc_id.id))
        else:
            query.append(("document_class_ids", "=", dc_id.id))
        query.append(("type", "=", type))
        journal_id = self.env["account.journal"].search(
                query, limit=1,
            )
        if not journal_id and not ignore_journal:
            raise UserError(
                "No existe Diario para el tipo de documento %s, por favor añada uno primero, o ignore el documento"
                % dc_id.name.encode("UTF-8")
            )
        return journal_id

    def _get_data_lines(self, xml_lines, data, price_included, company_id):
        dc = self.env["sii.document_class"].browse(data.get('document_class_id', False))
        exenta = dc.es_factura_exenta() or dc.es_boleta_exenta()
        lines = []
        for line in xml_lines:
            new_line = self._prepare_line(line,
                                        data.get("move_type", False),
                                        company_id,
                                        data.get("fiscal_position_id", False),
                                        price_included,
                                        exenta)
            if new_line:
                if self.crear_po:
                    new_line["date_planned"] = data['date_order']
                lines.append([0,0, new_line])
        return lines

    def _get_data(self, documento, company_id, ignore_journal=False):
        Encabezado = documento.find("Encabezado")
        IdDoc = Encabezado.find("IdDoc")
        price_included = IdDoc.find("MntBruto") is not None
        journal_id = self._get_journal(IdDoc.find("TipoDTE").text, company_id, ignore_journal)
        data = self._prepare_data(documento, company_id, journal_id)
        lines = [(5,)]
        if self.type != "ventas":
            self._dte_exist(documento)
        lines.extend(
            self._get_data_lines(
                documento.findall("Detalle"),
                data,
                price_included,
                company_id,
            )
        )
        product_id = (
            self.env["product.product"].search([("product_tmpl_id", "=", self.env.ref("l10n_cl_fe.product_imp").id)]).id
        )
        if not self.crear_po and Encabezado.find("Totales/ImptoReten") is not None:
            ImptoReten = Encabezado.findall("Totales/ImptoReten")
            refund = self.env["sii.document_class"].browse(
                data['document_class_id']).es_nc()
            en_lineas = {d.findtext("CodImpAdic") for d in documento.findall("Detalle")}
            for i in ImptoReten:
                if i.find("TipoImp").text in en_lineas:
                    continue
                tax_amount = 0
                if i.find("TasaImp") is not None:
                    tax_amount = float(i.find("TasaImp").text)
                imp = self._buscar_impuesto(
                    type="purchase" if self.type == "compras" else "sale",
                    name="OtrosImps_" + i.find("TipoImp").text,
                    amount=tax_amount,
                    sii_code=i.find("TipoImp").text,
                    company_id=company_id,
                    refund=refund)
                price = float(i.find("MontoImp").text)
                price_subtotal = float(i.find("MontoImp").text)
                if price_included:
                    price = imp.compute_all(price, company_id.currency_id, 1)["total_excluded"]
                    price_subtotal = imp.compute_all(price_subtotal, company_id.currency_id, 1)[
                        "total_excluded"
                    ]
                lines.append(
                    [
                        0,
                        0,
                        {
                            "tax_ids": [(6, 0, imp.ids)],
                            "product_id": product_id,
                            "name": "MontoImpuesto %s" % i.find("TipoImp").text,
                            "price_unit": price,
                            "quantity": 1,
                            "price_subtotal": price_subtotal,
                            # 'account_id':
                        },
                    ]
                )
        # if 'IVATerc' in dte['Encabezado']['Totales']:
        #    imp = self._buscar_impuesto(name="IVATerc" )
        #    lines.append([0,0,{
        #        'tax_ids': [ imp ],
        #        'product_id': product_id,
        #        'name': 'MontoImpuesto IVATerc' ,
        #        'price_unit': dte['Encabezado']['Totales']['IVATerc'],
        #        'quantity': 1,
        #        'price_subtotal': dte['Encabezado']['Totales']['IVATerc'],
        #        'account_id':  journal_document_class_id.journal_id.default_debit_account_id.id
        #        }]
        #    )
        if self.crear_po:
            data["order_line"] = lines
            return data
        data["invoice_line_ids"] = lines
        MntNeto = Encabezado.find("Totales/MntNeto")
        mnt_neto = 0
        if MntNeto is not None:
            mnt_neto = int(MntNeto.text or 0)
        MntExe = Encabezado.find("Totales/MntExe")
        if MntExe is not None:
            mnt_neto += int(MntExe.text or 0)
        if self.pre_process:
            purchase_to_done = self.purchase_to_done
            if self.document_id and not purchase_to_done:
                purchase_to_done = self.document_id.purchase_to_done
            data["purchase_to_done"] = purchase_to_done.id
        return data

    def _inv_exist(self, documento):
        encabezado = documento.find("Encabezado")
        IdDoc = encabezado.find("IdDoc")
        query = [
            ("sii_document_number", "=", IdDoc.find("Folio").text),
            ("document_class_id.sii_code", "=", IdDoc.find("TipoDTE").text),
        ]
        if self.type == "ventas":
            query.append(("move_type", "in", ["out_invoice", "out_refund"]))
            Receptor = encabezado.find("Receptor")
            query.append(("partner_id.vat", "=", self.format_rut(Receptor.find("RUTRecep").text)))
        else:
            Emisor = encabezado.find("Emisor")
            rut = Emisor.find("RUTEmisor").text
            query.append(("move_type", "in", ["in_invoice", "in_refund"]))
            inv = self.env["account.move"].search(query + [("partner_id.vat", "=", self.format_rut(rut))])
            if inv:
                return inv
            return self.env["account.move"].search(query).filtered(
                lambda m: clave_rut(rut) in (clave_rut(m.partner_id.vat), clave_rut(m.partner_id.document_number))
            )
        return self.env["account.move"].search(query)

    def _create_inv(self, documento, company_id):
        inv = self._inv_exist(documento)
        if inv:
            return inv
        data = self._get_data(documento, company_id)
        inv = self.env["account.move"].create(data)
        return inv

    def _dte_exist(self, documento):
        encabezado = documento.find("Encabezado")
        Emisor = encabezado.find("Emisor")
        IdDoc = encabezado.find("IdDoc")
        clave = clave_rut(Emisor.find("RUTEmisor").text)
        vats = ["CL%s" % clave.zfill(9), "CL%s" % clave]
        vats += [v[:-1] + "k" for v in vats if v.endswith("K")]
        self.document_id = self.env["mail.message.dte.document"].search(
            [
                ("number", "=", IdDoc.find("Folio").text),
                ("document_class_id.sii_code", "=", IdDoc.find("TipoDTE").text),
                "|",
                ("partner_id.vat", "in", vats),
                ("new_partner", "=like", "%s %%" % Emisor.find("RUTEmisor").text),
            ],
            limit=1,
        )

    def _create_pre(self, documento, company_id):
        self._dte_exist(documento)
        if self.document_id.source == "rcv":
            self.pre_process = True
            data = self._get_data(documento, company_id, ignore_journal=True)
            data.update({"dte_id": self.dte_id.id, "source": "xml"})
            self.document_id.with_context(no_map_po_lines=True).write(data)
            return self.document_id
        if self.document_id:
            msg = _("El documento {} {} ya se encuentra registrado".format(
                self.document_id.number,
                self.document_id.document_class_id.name)
            )
            _logger.warning(msg)
            self.dte_id.message_post(body=msg)
            return self.document_id
        self.pre_process = True
        data = self._get_data(documento, company_id, ignore_journal=True)
        data.update(
            {"dte_id": self.dte_id.id,}
        )
        return self.env["mail.message.dte.document"].with_context(no_map_po_lines=True).create(data)

    def _bhe(self):
        try:
            xml = etree.fromstring(base64.b64decode(self.xml_file))
        except Exception:
            return None
        if xml.tag == "datos" and xml.findtext("numeroBoleta"):
            return xml
        return None

    def _impuesto_retencion_honorarios(self, tasa, company):
        Tax = self.env["account.tax"]
        impuesto = Tax.search([
            ("company_id", "=", company.id),
            ("type_tax_use", "=", "purchase"),
            ("amount_type", "=", "percent"),
            ("amount", "<", 0),
            ("invoice_repartition_line_ids.account_id.code", "=", "210704"),
        ], order="id", limit=1)
        if impuesto:
            return impuesto
        cuenta = self.env["account.account"].search([("company_id", "=", company.id), ("code", "=", "210704")], limit=1)
        if not cuenta:
            raise UserError(_("No existe la cuenta 210704 de retención de segunda categoría en %s") % company.name)
        reparto = [(0, 0, {"repartition_type": "base"}), (0, 0, {"repartition_type": "tax", "account_id": cuenta.id})]
        return Tax.create({
            "name": _("Retención Honorarios"),
            "amount": -tasa,
            "amount_type": "percent",
            "type_tax_use": "purchase",
            "company_id": company.id,
            "country_id": company.account_fiscal_country_id.id or self.env.ref("base.cl").id,
            "invoice_repartition_line_ids": reparto,
            "refund_repartition_line_ids": reparto,
        })

    def _crear_bhe(self, bhe):
        Partner = self.env["res.partner"]
        rut_receptor = "%s-%s" % (bhe.findtext("rutReceptor"), bhe.findtext("dvReceptor"))
        company = self.env["res.company"].search([("vat", "in", Partner._vats_busqueda(rut_receptor))], limit=1)
        if not company:
            raise UserError(_("La boleta de honorarios es para %s, que no es una compañía de este sistema") % rut_receptor)
        rut_emisor = "%s-%s" % (bhe.findtext("rutEmisor"), bhe.findtext("dvEmisor"))
        dc = self.env["sii.document_class"].search([("sii_code", "=", 71)], limit=1)
        folio = bhe.findtext("numeroBoleta")
        partner = Partner._buscar_por_vat(rut_emisor, [("active", "=", True), ("parent_id", "=", False)])
        if partner:
            existe = self.env["account.move"].search([
                ("company_id", "=", company.id),
                ("partner_id", "=", partner.id),
                ("document_class_id", "=", dc.id),
                ("sii_document_number", "=", folio),
                ("move_type", "=", "in_invoice"),
            ], limit=1)
            if existe:
                return existe
        else:
            partner = Partner.create({
                "name": Partner._rut_formateado(rut_emisor) or rut_emisor,
                "vat": self.format_rut(rut_emisor),
                "document_type_id": self.env.ref("l10n_cl_fe.dt_RUT").id,
                "document_number": Partner._rut_formateado(rut_emisor) or rut_emisor,
                "street": bhe.findtext("domicilioEmisor") or False,
                "company_type": "person",
                "country_id": self.env.ref("base.cl").id,
                "supplier_rank": 1,
            })
        journal = self._get_journal(71, company)
        impuesto = self.env["account.tax"]
        if bhe.findtext("retieneEmisor") != "S":
            impuesto = self._impuesto_retencion_honorarios(float(bhe.findtext("porcentajeImpuesto") or 0), company)
        items = bhe.findall("prestacionServicios/item") or [bhe]
        lineas = [(0, 0, {
            "name": (item.findtext("descripcionLinea") or "").strip() or _("Honorarios"),
            "quantity": 1,
            "price_unit": float(item.findtext("valorServicio") or item.findtext("totalHonorarios") or 0),
            "account_id": journal.default_account_id.id,
            "tax_ids": [(6, 0, impuesto.ids)],
        }) for item in items]
        f = bhe.findtext("fechaBoleta")
        fecha = fields.Date.to_date("%s-%s-%s" % (f[:4], f[4:6], f[6:8]))
        move = self.env["account.move"].with_company(company).create({
            "move_type": "in_invoice",
            "partner_id": partner.id,
            "company_id": company.id,
            "journal_id": journal.id,
            "invoice_date": fecha,
            "date": fecha,
            "document_class_id": dc.id,
            "sii_document_number": folio,
            "use_documents": False,
            "invoice_origin": "XML BHE: %s" % (self.filename or ""),
            "invoice_line_ids": lineas,
        })
        linea = move.line_ids.filtered(lambda l: impuesto and l.tax_line_id == impuesto)
        retencion = -float(bhe.findtext("impuestoHonorarios") or 0)
        if linea and move.currency_id.compare_amounts(linea.amount_currency, retencion):
            move.write({"line_ids": [(1, linea.id, {"amount_currency": retencion, "balance": retencion})]})
        self._create_attachment(base64.b64decode(self.xml_file).decode("UTF-8"), self.filename or "BHE_%s" % folio, move.id, "account.move", False)
        return move

    def _get_dtes(self):
        bhe = self._bhe()
        if bhe is not None:
            return [bhe]
        xml = self._read_xml("etree")
        if xml.tag == "SetDTE":
            return xml.findall("DTE")
        envio = xml.find("SetDTE")
        if envio is None:
            if xml.tag == "DTE":
                return [xml]
            return []
        return envio.findall("DTE")

    def do_create_pre(self):
        created = []
        self.do_receipt_deliver()
        dtes = self._get_dtes()
        for dte in dtes:
            try:
                documento = dte.find("Documento")
                company_id = self.env["res.company"].search(
                    [("vat", "=", self.format_rut(documento.find("Encabezado/Receptor/RUTRecep").text)),], limit=1,
                )
                if not company_id:
                    _logger.warning("No existe compañia para %s" % documento.find("Encabezado/Receptor/RUTRecep").text)
                    continue
                pre = self._create_pre(documento, company_id,)
                if pre:
                    inv = self._inv_exist(documento)[:1]
                    pre.write(
                        {"id_dte": documento.get("ID"), "move_id": inv.id,}
                    )
                    if inv and not inv.sii_xml_dte:
                        inv.sii_xml_dte = "<DTE>%s</DTE>" % etree.tostring(documento).decode("ISO-8859-1")
                    if inv:
                        pre._aprender_productos()
                    created.append(pre.id)
            except Exception as e:
                msg = "Error en 1 pre con error:  %s" % str(e)
                _logger.warning(msg, exc_info=True)
                if self.dte_id:
                    self.dte_id.message_post(body=msg)
        return created

    def do_reject(self):
        docs = self.document_id or self.dte_id.document_ids
        for doc in docs:
            datos = {
                "claim": "RCD",
                "date": fields.Datetime.now(),
                "user_id": self.env.uid,
                "sequence": 1,
                "estado_dte": "2",
                "document_id": doc.id,
            }
            claim = self.env["sii.dte.claim"].sudo().create(datos)
            claim.do_reject()

    def do_create_inv(self):
        self = self._con_companias_del_usuario()
        created = []
        dtes = self._get_dtes()
        for dte in dtes:
            try:
                to_post = self.type == "ventas" or self.option == "accept"
                company_id = self.document_id.company_id
                documento = dte.find("Documento")
                path_rut = "Encabezado/Receptor/RUTRecep"
                if self.type == "ventas":
                    path_rut = "Encabezado/Emisor/RUTEmisor"
                rut = documento.find(path_rut).text
                company_id = self.env["res.company"].search(
                    [("vat", "=", self.format_rut(rut)),], limit=1,
                )
                if not company_id:
                    raise UserError(_(f"No existe compañia para el rut {rut}"))
                self_co = self.with_company(company_id)
                existente = self_co._inv_exist(documento)[:1]
                inv = existente or self_co._create_inv(documento, company_id,)
                if self.document_id:
                    self.document_id.move_id = inv.id
                if inv:
                    created.append(inv.id)
                if not inv:
                    raise UserError(
                        "El archivo XML no contiene documentos para alguna empresa registrada en Odoo, o ya ha sido procesado anteriormente "
                    )
                if to_post and inv.state=="draft":
                    inv = inv.with_company(company_id)
                    inv._onchange_partner_id()
                    inv._onchange_invoice_line_ids()
                    inv.with_context(purchase_to_done=self.purchase_to_done.id, restore_mode=True)._post()
                Totales = documento.find("Encabezado/Totales")
                monto_xml = float(Totales.find("MntTotal").text) + float(Totales.findtext("MontoNF") or 0)
                self.env.cr.commit()
                if inv.amount_total == monto_xml:
                    continue
                if to_post:
                    inv.button_draft()
                total_line = inv.line_ids.filtered(lambda a: a.name=='')
                diff_amount_currency = diff_balance = 0
                for line in inv.line_ids.filtered('tax_line_id'):
                    if Totales.find("TasaIVA") is not None and line.tax_line_id.amount == float(Totales.find("TasaIVA").text):
                        diff_amount_currency = diff_balance = float(Totales.find("IVA").text) - (line.balance if line.balance >0 else -line.balance)
                        rounding_line_vals = {
                            'price_unit': diff_amount_currency,
                            'quantity': 1.0,
                            'amount_currency': diff_amount_currency,
                            'partner_id': inv.partner_id.id,
                            'move_id': inv.id,
                            'currency_id': inv.currency_id.id,
                            'company_id': inv.company_id.id,
                            'company_currency_id': inv.company_id.currency_id.id,
                            'sequence': 9999,
                            'name': _('%s (rounding)', line.name),
                            'account_id': line.account_id.id,
                        }
                        rounding_line = self.env['account.move.line'].with_context(check_move_validity=False).create(rounding_line_vals)
                if to_post:
                    inv.with_context(restore_mode=True)._post()
                if inv.amount_total == monto_xml:
                    continue
                mensaje = "No se pudo cuadrar %s-%s, %s != %s" % (
                    inv.document_class_id.name,
                    inv.sii_document_number,
                    inv.amount_total,
                    monto_xml)
                if not existente:
                    if inv.state == "posted":
                        inv.button_draft()
                    if self.document_id:
                        self.document_id.move_id = False
                    inv.with_context(force_delete=True).unlink()
                    self.env.cr.commit()
                    created.remove(inv.id)
                raise UserError(mensaje)
            except Exception as e:
                msg = "Error en crear 1 factura con error:  %s" % str(e)
                _logger.warning(msg, exc_info=True)
                _logger.warning(etree.tostring(dte))
                if self.document_id:
                    self.document_id.message_post(body=msg)
        if created and self.option not in [False, "upload"] and self.type == "compras"  and not self._context.get('create_only', False):
            datos = {
                "move_ids": [(6, 0, created)],
                "action": "ambas",
                "claim": "ACD",
                "estado_dte": "0",
                "tipo": "account.move",
            }
            wiz_accept = self.env["sii.dte.validar.wizard"].create(datos)
            wiz_accept.confirm()
        return created

    def _purchase_exist(self, documento, dc_id, company_id):
        purchase_model = self.env["purchase.order"]
        path_rut = "Encabezado/Emisor/RUTEmisor"
        partner = self.env["res.partner"].search(
            [("vat", "=", self.format_rut(documento.find(path_rut).text)),], limit=1
        )
        Folio = documento.find("Encabezado/IdDoc/Folio").text
        return purchase_model.search(
            [
                ("partner_id", "=", partner.id),
                ("partner_ref", "=", self._purchase_partner_ref(dc_id, Folio)),
                ("company_id", "=", company_id.id),
            ],limit=1
        )

    def _prepare_purchase(self, documento, company):
        self.crear_po = True
        purchase_vals = self._get_data(documento, company, ignore_journal=True)
        if purchase_vals.get('order_line', [(5,)]) == [(5,)]:
            raise UserError("No se puede crear una orden de compra vacía")
        return purchase_vals

    def _purchase_abierta(self, productos, company):
        proveedor = self.document_id.partner_id.commercial_partner_id
        if not proveedor or not productos:
            return self.env["purchase.order"]
        abiertas = self.env["purchase.order"].search([
            ("partner_id", "child_of", proveedor.id),
            ("state", "in", ["purchase", "done"]),
            ("invoice_status", "=", "to invoice"),
            ("company_id", "=", company.id),
            ("order_line.product_id", "in", productos.ids),
        ], order="date_order desc")
        if not abiertas:
            return abiertas
        return max(abiertas, key=lambda po: len(po.order_line.product_id & productos))

    def _create_po(self, documento, dc_id, company):
        purchase_vals = self._prepare_purchase(documento, company)
        po = self._purchase_exist(documento, dc_id, company)
        if po:
            return po
        lineas = [l[2] for l in purchase_vals.get("order_line", []) if isinstance(l, (list, tuple)) and len(l) == 3]
        productos = self.env["product.product"].browse([l["product_id"] for l in lineas if l.get("product_id")])
        if not productos.filtered(lambda p: p.type != "service"):
            return po
        po = self._purchase_abierta(productos, company)
        if po:
            return po
        if any(not l.get("product_id") for l in lineas):
            return self.env["purchase.order"]
        emision = fields.Date.to_date(documento.findtext("Encabezado/IdDoc/FchEmis"))
        hoy = fields.Date.context_today(self)
        if not emision or (emision.year, emision.month) != (hoy.year, hoy.month):
            return self.env["purchase.order"]
        purchase_model = self.env["purchase.order"]
        po = purchase_model.create(purchase_vals)
        return po

    def do_create_po(self):
        created = []
        dtes = self._get_dtes()
        for dte in dtes:
            documento = dte.find("Documento")
            path_rut = "Encabezado/Receptor/RUTRecep"
            company = self.env["res.company"].search(
                [("vat", "=", self.format_rut(documento.find(path_rut).text)),], limit=1
            )
            path_tpo_doc = "Encabezado/IdDoc/TipoDTE"
            dc_id = self.env["sii.document_class"].search([("sii_code", "=", documento.find(path_tpo_doc).text)])
            if dc_id.es_factura() or dc_id.es_nd() or dc_id.es_guia() or dc_id.es_boleta_afecta():
                try:
                    with self.env.cr.savepoint():
                        po = self._create_po(documento, dc_id, company)
                        if not po:
                            self.crear_po = False
                            continue
                        created.append(po.id)
                        if self.document_id:
                            self.document_id.purchase_to_done = po
                            self.document_id.auto_map_po_lines()
                except Exception as e:
                    msg = "Error en procesar PO: %s" % str(e)
                    _logger.warning(msg, exc_info=True)
                    if self.document_id:
                        self.document_id.message_post(body=msg)
                    continue
                if self.action == "both" and not dc_id.es_guia():
                    self.purchase_to_done = po
                    self.crear_po = False
        return created
