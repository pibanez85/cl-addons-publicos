import base64
import logging
import re

from lxml import etree

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)
try:
    from facturacion_electronica import clase_util as fe_util
    from facturacion_electronica import facturacion_electronica as fe
except ImportError:
    _logger.warning("No se ha podido cargar fe")


class DTEClaim(models.Model):
    _name = "sii.dte.claim"
    _description = "DTE CLAIM"
    _inherit = ["mail.thread"]

    document_id = fields.Many2one("mail.message.dte.document", string="Documento", ondelete="cascade",)
    move_id = fields.Many2one("account.move", string="Documento", ondelete="cascade",)
    sequence = fields.Integer(string="Número de línea", default=1)
    claim = fields.Selection(
        [
            ("N/D", "No definido"),
            ("ACD", "Acepta Contenido del Documento"),
            ("RCD", "Reclamo al  Contenido del Documento "),
            ("ERM", "Otorga  Recibo  de  Mercaderías  o Servicios"),
            ("RFP", "Reclamo por Falta Parcial de Mercaderías"),
            ("RFT", "Reclamo por Falta Total de Mercaderías"),
            ("PAG", "DTE Pagado al Contado"),
        ],
        string="Reclamo",
        copy=False,
        default="N/D",
    )
    estado_dte = fields.Selection(
        [("0", "DTE Recibido Ok"), ("1", "DTE Aceptado con Discrepancia."), ("2", "DTE Rechazado"),],
        string="Estado de Recepción Documento",
    )
    date = fields.Datetime(string="Fecha Reclamo",)
    user_id = fields.Many2one("res.users")
    claim_description = fields.Char(string="Detalle Reclamo",)

    def send_claim(self):
        doc = self.move_id
        if doc:
            doc.set_dte_claim(self.claim)
            return
        doc = self.document_id
        folio = doc.number
        tipo_dte = doc.document_class_id.sii_code
        datos = doc._get_datos_empresa()
        rut_emisor = doc.get_doc_rut()
        datos["DTEClaim"] = [
            {
                "RUTEmisor": rut_emisor,
                "TipoDTE": tipo_dte,
                "Folio": folio,
                "Claim": self.claim
            }
        ]
        try:
            respuesta = fe.ingreso_reclamo_documento(datos)
        except Exception as e:
                msg = "Error al ingresar Reclamo DTE"
                _logger.warning("%s: %s" % (msg, str(e)), exc_info=True)
                if e.args[0][0] == 503:
                    raise UserError('%s: Conexión al SII caída/rechazada o el SII está temporalmente fuera de línea, reintente la acción' % (msg))
                raise UserError(("%s: %s" % (msg, str(e))))

    def _create_attachment(self, xml, name, id=False, model="account.move"):
        data = xml.encode("ISO-8859-1")
        filename = (name).replace(" ", "")
        url_path = (
            "/web/binary/download_document?model="
            + model
            + "\
    &field=sii_xml_request&id=%s&filename=%s"
            % (id, filename)
        )
        att = self.env["ir.attachment"].search(
            [("name", "=", filename), ("res_id", "=", id), ("res_model", "=", model)], limit=1,
        )
        if att:
            return att
        values = dict(
            name=filename, url=url_path, res_model=model, res_id=id, type="binary", raw=data,
        )
        att = self.env["ir.attachment"].create(values)
        return att

    def do_reject(self):
        """Rechazo comercial (RespuestaDTE/ResultadoDTE con EstadoDTE 2) del DTE recibido, armado
        desde su XML, enviado al proveedor; luego el reclamo al SII."""
        id_seq = self.env.ref("l10n_cl_fe.response_sequence")
        IdRespuesta = id_seq.next_by_id()
        NroDetalles = 1
        doc = self.move_id or self.document_id
        tipo = "account.move" if self.move_id else "mail.message.dte.document"
        if self.move_id:
            xml = doc.sii_xml_dte
        else:
            xml = doc.xml or doc._get_xml()
        if not xml:
            raise UserError(_("El documento %s no tiene el XML del DTE: no se puede rechazar.") % doc.display_name)
        # El DTE tal como lo emitió el proveedor. El estado va en cada DTE: la biblioteca arma el
        # ResultadoDTE con el EstadoDTE del documento, no con el de la respuesta (con "xml_dte"
        # quedaba EstadoDTE 0, es decir "Recibido Ok").
        xml = re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", xml)
        xml = re.sub(r' xmlns(:\w+)?="[^"]*"', "", xml).replace("<ds:", "<").replace("</ds:", "</")
        documento = etree.fromstring(xml.encode("ISO-8859-1"), etree.XMLParser(encoding="ISO-8859-1"))
        documento = documento if documento.tag == "Documento" else documento.find("Documento")
        dte = fe_util.recursive_xml(documento)
        glosa = self.claim_description or "DTE Rechazado"
        dte.update({"CodEnvio": IdRespuesta, "EstadoDTE": 2, "EstadoDTEGlosa": glosa, "CodRchDsc": -1})
        datos = doc._get_datos_empresa()
        datos["filename"] = "rechazo_comercial_%s.xml" % str(IdRespuesta)
        datos["ValidacionCom"] = {
            "IdRespuesta": IdRespuesta,
            "NroDetalles": NroDetalles,
            "RutResponde": doc.company_id.partner_id.rut(),
            "RutRecibe": dte["Encabezado"]["Emisor"]["RUTEmisor"],
            "Receptor": {"RUTRecep": dte["Encabezado"]["Emisor"]["RUTEmisor"]},
            "NmbContacto": self.env.user.partner_id.name,
            "FonoContacto": self.env.user.partner_id.phone,
            "MailContacto": self.env.user.partner_id.email,
            "EstadoDTE": 2,
            "EstadoDTEGlosa": glosa,
            "DTEs": [dte],
        }
        resp = fe.validacion_comercial(datos)
        if resp.get("error"):
            raise UserError(resp["error"])
        att = self._create_attachment(resp["respuesta_xml"], resp["nombre_xml"], doc.id, tipo)
        dte_email_id = doc.company_id.dte_email_id or self.env.company.dte_email_id
        email_to = doc.partner_id.commercial_partner_id.dte_email
        if not email_to and not self.move_id:
            email_to = doc.dte_id.sudo().mail_id.email_from
        values = {
            "res_id": doc.id,
            "email_from": dte_email_id.display_name,
            "email_to": email_to,
            "auto_delete": False,
            "model": tipo,
            "body": "XML de Respuesta DTE, Estado: {} , Glosa: {} ".format(resp["EstadoDTE"], resp["EstadoDTEGlosa"]),
            "subject": "XML de Respuesta DTE",
            "attachment_ids": [[6, 0, att.ids]],
        }
        send_mail = self.env["mail.mail"].create(values)
        send_mail.send()
        if self.claim != "N/D":
            doc.set_dte_claim(claim=self.claim)

    def do_validar_comercial(self):
        if self.estado_dte == "0":
            self.claim_description = "DTE Recibido Ok"
        id_seq = self.env.ref("l10n_cl_fe.response_sequence")
        IdRespuesta = id_seq.next_by_id()
        NroDetalles = 1
        doc = self.move_id
        tipo = "account.move"
        if not doc:
            tipo = "mail.message.dte.document"
            doc = self.document_id
        if doc.claim in ["ACD"] or (self.move_id and doc.move_type in ["out_invoice", "out_refund"]):
            return
        datos = doc._get_datos_empresa()
        dte = doc._dte()
        if self.move_id:
            recibido = self.env["mail.message.dte.document"].sudo().search(
                [("move_id", "=", self.move_id.id)], limit=1)
            if recibido.amount and dte.get("Encabezado", {}).get("Totales"):
                dte["Encabezado"]["Totales"]["MntTotal"] = recibido.amount
        """ @TODO separar estos dos"""
        dte["CodEnvio"] = IdRespuesta
        datos["filename"] = "validacion_comercial_%s.xml" % str(IdRespuesta)
        receptor = doc._receptor()
        datos["ValidacionCom"] = {
            "IdRespuesta": IdRespuesta,
            "NroDetalles": NroDetalles,
            "RutResponde": doc.company_id.partner_id.rut(),
            "RutRecibe": receptor["RUTRecep"],
            "NmbContacto": self.env.user.partner_id.name,
            "FonoContacto": self.env.user.partner_id.phone,
            "MailContacto": self.env.user.partner_id.email,
            "EstadoDTE": self.estado_dte,
            "EstadoDTEGlosa": self.claim_description,
            "Receptor": {"RUTRecep": receptor["RUTRecep"],},
            "DTEs": [dte],
        }

        if self.estado_dte in ["2"]:
            datos["filename"] = "validacion_comercial_%s.xml" % str(IdRespuesta)
            datos["ValidacionCom"]["CodRchDsc"] = -1
        elif self.estado_dte != "0":
            datos["ValidacionCom"]["CodRchDsc"] = -2
        resp = fe.validacion_comercial(datos)
        doc.sii_message = resp["respuesta_xml"]
        att = self._create_attachment(resp["respuesta_xml"], resp["nombre_xml"], doc.id, tipo)
        dte_email_id = doc.company_id.dte_email_id or self.env.company.dte_email_id
        values = {
            "res_id": doc.id,
            "email_from": dte_email_id.display_name,
            "email_to": doc.partner_id.commercial_partner_id.dte_email,
            "auto_delete": False,
            "model": tipo,
            "body": "XML de Validación Comercial, Estado: {}, Glosa: {}".format(
                resp["EstadoDTE"], resp["EstadoDTEGlosa"]
            ),
            "subject": "XML de Validación Comercial",
            "attachment_ids": [[6, 0, att.ids]],
        }
        send_mail = self.env["mail.mail"].create(values)
        send_mail.send()
        if self.claim == "N/D":
            return
        try:
            doc.set_dte_claim(claim=self.claim)
        except Exception as e:
            _logger.warning("Error al setear Reclamo %s" % str(e), exc_info=True)
        try:
            doc.get_dte_claim()
        except Exception:
            _logger.warning("@TODO crear código que encole la respuesta", exc_info=True)


    def do_recep_mercaderia(self):
        message = ""
        doc = self.move_id
        tipo = "account.move"
        if not doc:
            tipo = "mail.message.dte.document"
            doc = self.document_id
        if doc.claim in ["ACD"]:
            return
        if self.claim == "ERM":
            datos = doc._get_datos_empresa()
            receptor = doc._receptor()
            datos["RecepcionMer"] = {
                "EstadoRecepDTE": self.estado_dte,
                "RecepDTEGlosa": self.claim_description,
                "RutResponde": doc.company_id.partner_id.rut(),
                "RutRecibe": receptor["RUTRecep"],
                "Recinto": doc.company_id.street,
                "NmbContacto": self.env.user.partner_id.name,
                "FonoContacto": self.env.user.partner_id.phone,
                "MailContacto": self.env.user.partner_id.email,
                "Receptor": {"RUTRecep": receptor["RUTRecep"],},
                "DTEs": [doc._dte()],
            }
            resp = fe.recepcion_mercaderias(datos)
            doc.sii_message = resp["respuesta_xml"]
            att = self._create_attachment(resp["respuesta_xml"], resp["nombre_xml"], doc.id, tipo)
            dte_email_id = doc.company_id.dte_email_id or self.env.company.dte_email_id
            values = {
                "res_id": doc.id,
                "email_from": dte_email_id.display_name,
                "email_to": doc.partner_id.commercial_partner_id.dte_email,
                "auto_delete": False,
                "model": tipo,
                "body": "XML de Recepción de Mercaderías\n %s" % (message),
                "subject": "XML de Recepción de Documento",
                "attachment_ids": [[6, 0, att.ids]],
            }
            send_mail = self.env["mail.mail"].create(values)
            send_mail.send()
        if self.claim == "N/D":
            return
        try:
            doc.set_dte_claim(claim=self.claim)
        except Exception as e:
            _logger.warning("Error al setear Reclamo  Recep Mercadería %s" % str(e), exc_info=True)
        try:
            doc.get_dte_claim()
        except Exception:
            _logger.warning("@TODO crear código que encole la respuesta", exc_info=True)
