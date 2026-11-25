import base64
import logging
from datetime import timedelta
from io import BytesIO

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.translate import _

from .binario import BinaryBytes

_logger = logging.getLogger(__name__)

try:
    from facturacion_electronica import facturacion_electronica as fe
except Exception as e:
    _logger.warning(f"no se ha cargado FE {e}")
    fe = None

try:
    import pdf417gen
except Exception:
    _logger.warning("no se ha cargado pdf417gen")


class DteEnvioMixin(models.AbstractModel):
    _name = 'l10n_cl_fe.dte.envio.mixin'
    _description = 'Chilean DTE common send/queue helpers'

    _dte_cola_field = None
    _dte_xml_envio_field = None
    _dte_envio_tipo = 'dte'

    @api.model
    def _get_dte_docs_por_pack(self, es_boleta=False):
        return 50 if es_boleta else 20

    sii_xml_request = fields.Many2one(
        "sii.xml.envio", string="SII XML Request", copy=False,
    )
    sii_message = fields.Text(string="SII Message", copy=False,)
    sii_xml_dte = fields.Text(
        string="SII XML DTE", copy=False, readonly=True,
    )
    sii_batch_number = fields.Integer(
        string="Batch Number", copy=False, readonly=True,
        aggregator=False,
        help="Batch number for processing multiple invoices together",
    )
    sii_barcode = fields.Char(
        string="SII Barcode", copy=False, readonly=True,
        help="SII Barcode Name",
    )
    sii_barcode_img = fields.Binary(
        string="SII Barcode Image",
        compute="_get_barcode_img",
        help="SII Barcode Image in PDF417 format",
    )
    sii_result = fields.Selection(
        [
            ("", "n/a"),
            ("draft", "Borrador"),
            ("NoEnviado", "No Enviado"),
            ("EnCola", "En cola de envío"),
            ("Enviado", "Enviado"),
            ("EnProceso", "En Proceso"),
            ("Aceptado", "Aceptado"),
            ("Rechazado", "Rechazado"),
            ("Reparo", "Reparo"),
            ("Proceso", "Procesado"),
            ("Reenviar", "Reenviar"),
            ("Anulado", "Anulado"),
        ],
        string="Estado SII",
        copy=False,
        default="draft",
        help="Resultado del envío y proceso del documento en el SII",
    )

    def pdf417bc(self, ted, columns=13, ratio=3):
        bc = pdf417gen.encode(ted, security_level=5, columns=columns, encoding="ISO-8859-1")
        return pdf417gen.render_image(bc, padding=15, scale=1, ratio=ratio)

    def get_barcode_img(self, columns=13, ratio=3, xml=False):
        ted = xml or getattr(self, 'sii_barcode', '')
        if not ted:
            return False
        barcodefile = BytesIO()
        image = self.pdf417bc(ted, columns, ratio)
        image.save(barcodefile, "PNG")
        return BinaryBytes(barcodefile.getvalue())

    def _get_barcode_img(self):
        for r in self:
            r.sii_barcode_img = r.get_barcode_img() if r.sii_barcode else False

    def _resolve_firma(self):
        self.ensure_one()
        firma_id = self.env.context.get('firma_id')
        if firma_id:
            firma = self.env['sii.firma'].browse(firma_id)._firma_autorizada(self.company_id)
            if not firma:
                raise UserError(_(
                    "El usuario '%s' no está autorizado a usar la firma electrónica indicada para %s."
                ) % (self.env.user.name, self.company_id.name))
            return firma
        firma = self.env.user.get_digital_signature(self.company_id)
        if not firma:
            raise UserError(_(
                "El usuario '%s' no está autorizado a usar firma electrónica para %s."
            ) % (self.env.user.name, self.company_id.name))
        return firma

    def _get_datos_empresa(self):
        return {
            "Emisor": self._emisor(),
            "firma_electronica": self._resolve_firma().parametros_firma(),
        }

    def _emisor(self):
        raise NotImplementedError(
            _("El modelo %s debe implementar _emisor()") % self._name
        )

    def _es_doc_cola(self):
        """Doc listo para evaluar el cierre de la cola: por defecto se
        considera resuelto cuando el SII devolvió mensaje por documento."""
        return bool(self.sii_message)

    def do_dte_send(self):
        return self.env['sii.xml.envio'].crear_envio(self)

    def _dte_send_filter(self):
        return self

    def _dte_send_should_queue(self):
        if (self.sii_xml_request and self.sii_xml_request.sii_send_ident
                and self.sii_result not in (False, '', 'NoEnviado', 'Rechazado')):
            return False
        return self.sii_result in ('', 'NoEnviado', 'Rechazado', False)

    def _dte_send_pre_queue(self):
        self._dte_clean_envio_if_needed()
        self._dte_repair_for_send()

    def _dte_clean_envio_if_needed(self):
        return

    def _dte_repair_for_send(self):
        return

    def _dte_send_xml_orphan(self):
        if not self.sii_xml_request or not self._dte_xml_envio_field:
            return False
        return len(self.sii_xml_request[self._dte_xml_envio_field]) == 1

    @api.model
    def _resolve_cola_firma(self, company, respaldo_admin=False):
        """Firma para una cola de envío: la del usuario y, solo con ``respaldo_admin`` (el cron,
        ver sii.cola_envio._validar_firma), la del admin si el usuario no tiene. No depende de
        env.su: el botón Procesar también corre con sudo y no puede disponer de la firma del admin
        (ver sii.cola_envio._check_firma_autorizada)."""
        if not company:
            return self.env['sii.firma']
        user = self.env.user
        firma = user.sudo().get_digital_signature(company)
        if not firma and respaldo_admin:
            admin = self.env.ref('base.user_admin', raise_if_not_found=False)
            if admin and admin.id != user.id:
                firma = admin.sudo().get_digital_signature(company)
        return firma or self.env['sii.firma']

    def _set_cola_firma(self, vals):
        """Completa vals['firma_id'] de una cola nueva con la firma del usuario.

        Un usuario sin firma propia deja la cola sin firma: el cron le asigna la del admin
        (sii.cola_envio._validar_firma). Si tampoco hay esa, falla al encolar."""
        if 'firma_id' in vals or not vals.get('company_id'):
            return
        company = self.env['res.company'].browse(vals['company_id'])
        firma = self._resolve_cola_firma(company)
        if firma:
            vals['firma_id'] = firma.id
        elif not self._resolve_cola_firma(company, respaldo_admin=True):
            raise UserError(_(
                "No hay firma electrónica disponible para la compañía '%s'. "
                "Asigne una firma al usuario o al admin."
            ) % company.name)

    def _dte_ampara_traslado(self):
        return False

    def _fill_envio_defaults(self, vals):
        ISCP = self.env['ir.config_parameter'].sudo()
        metodo = ISCP.get_str('account.send_dte_method', default='diferido')
        if metodo == 'manual' and 'tipo_trabajo' not in vals:
            return False
        if 'company_id' not in vals:
            vals['company_id'] = self[0].company_id.id
        if 'tipo_trabajo' not in vals:
            vals['tipo_trabajo'] = 'pasivo' if metodo == 'diferido' and not self._dte_ampara_traslado() else 'envio'
        if 'date_time' not in vals:
            tiempo = fields.Datetime.now()
            if vals['tipo_trabajo'] == 'pasivo':
                tiempo += timedelta(
                    hours=ISCP.get_int('account.auto_send_dte', default=1)
                )
            vals['date_time'] = tiempo
        if 'send_email' not in vals:
            company = self.env['res.company'].browse(vals['company_id'])
            auto_send = ISCP.get_bool('account.auto_send_email', default=True)
            vals['send_email'] = bool(
                company.dte_service_provider != 'SIICERT' and auto_send
            )
        return True

    def _dte_send_collect(self):
        docs = self.browse()
        for rec in self.with_context(lang='es_CL')._dte_send_filter():
            if not rec._dte_send_should_queue():
                continue
            rec._dte_send_pre_queue()
            rec.sii_result = 'EnCola'
            rec.sii_message = ''
            docs |= rec
        return docs

    def queue(self, vals=None):
        if not self:
            return self.env['sii.cola_envio']
        vals = dict(vals or {})
        if not self._fill_envio_defaults(vals):
            return self.env['sii.cola_envio']
        vals.pop('user_id', None)
        self._set_cola_firma(vals)
        vals[self._dte_cola_field] = [(6, 0, self.ids)]
        cola = self.env['sii.cola_envio'].create(vals)
        cola._log_user_docs(self)
        return cola

    def do_dte_send_now(self):
        docs = self._dte_send_collect()
        if not docs:
            return
        return docs.queue({
            'tipo_trabajo': 'envio',
            'n_atencion': self.env.context.get('n_atencion') or '',
            'set_pruebas': self.env.context.get('set_pruebas', False),
            'manual': True,
        })

    def _dte_status_api_flag(self, record):
        return False

    def _dte_status_folio_key(self, record):
        return record.sii_document_number

    def _dte_status_apply(self, record, result):
        record.sii_result = result['status']
        if result.get('xml_resp'):
            record.sii_message = result['xml_resp']

    def _post_dte_status_notify(self):
        return

    def _xml_download_path(self):
        raise NotImplementedError(
            _("El modelo %s debe implementar _xml_download_path()") % self._name
        )

    def get_xml_file(self):
        return {
            "type": "ir.actions.act_url",
            "url": f"/download/xml/{self._xml_download_path()}/{self.id}",
            "target": "self",
        }

    def _attachment_filename(self):
        self.ensure_one()
        return f"{self.name or self.id}.xml".replace(" ", "_").replace("/", "_")

    def _create_attachment_respuesta_cliente(self, att):
        return

    def _create_attachment(self):
        self.ensure_one()
        filename = self._attachment_filename()
        res_model = self._name
        att = self.env["ir.attachment"].search([
            ("name", "=", filename),
            ("res_id", "=", self.id),
            ("res_model", "=", res_model),
        ], limit=1)
        self._create_attachment_respuesta_cliente(att)
        if att:
            return att
        rut = self.partner_id.commercial_partner_id.rut() if self.partner_id else ""
        xml_intercambio = self.env["sii.xml.envio"].crear_intercambio(
            self.with_context(lang="es_CL"), RUTRecep=rut,
        )
        att = self.env["ir.attachment"].sudo().create({
            "name": filename,
            "url": f"/download/xml/{self._xml_download_path()}/{self.id}",
            "res_model": res_model,
            "res_id": self.id,
            "type": "binary",
            "raw": xml_intercambio,
        })
        return att

    def _dte_requires_response_persistence(self):
        self.ensure_one()
        return False

    def ask_for_dte_status(self):
        for r in self:
            if not r.sii_xml_request and not r.sii_xml_request.sii_send_ident:
                raise UserError(_(
                    "No se ha enviado aún el documento, aún está en cola de envío interna en odoo"
                ))
            if r.sii_xml_request.state not in ["Aceptado", "Rechazado"]:
                r.sii_xml_request.with_context(
                    set_pruebas=self.env.context.get("set_pruebas", False)
                ).get_send_status()
        try:
            self._get_dte_status()
        except Exception as e:
            _logger.warning(f"Error al obtener DTE Status: {e}", exc_info=True)
        self._post_dte_status_notify()

    def _get_dte_status(self):
        datos = self[0]._get_datos_empresa()
        datos["Documento"] = []
        docs = {}
        api = False
        for r in self:
            api = api or self._dte_status_api_flag(r)
            if r.sii_xml_request.state not in ["Aceptado", "Rechazado"]:
                continue
            docs.setdefault(r.document_class_id.sii_code, [])
            docs[r.document_class_id.sii_code].append(r._dte_para_estado())
        if not docs:
            _logger.warning("En get_dte_status, no docs")
            return
        if self.env.context.get("set_pruebas", False):
            api = False
        datos['api'] = api
        for k, v in docs.items():
            datos["Documento"].append({"TipoDTE": k, "documentos": v})
        resultado = fe.consulta_estado_dte(datos)
        if not resultado:
            _logger.warning("En get_dte_status, no resultado")
            return
        for r in self:
            key = f"T{r.document_class_id.sii_code}F{self._dte_status_folio_key(r)}"
            if key in resultado:
                self._dte_status_apply(r, resultado[key])
