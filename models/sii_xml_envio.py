import json
import logging
from datetime import datetime, timedelta

import psycopg2
from lxml import etree

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.translate import _

_logger = logging.getLogger(__name__)

try:
    from facturacion_electronica import facturacion_electronica as fe
except Exception as e:
    _logger.warning("no se ha cargado FE %s" % str(e))


_SII_SEND_RESULT_CACHE = {}


status_dte = [
    ("no_revisado", "No Revisado"),
    ("0", "Conforme"),
    ("1", "Error de Schema"),
    ("2", "Error de Firma"),
    ("3", "RUT Receptor No Corresponde"),
    ("90", "Archivo Repetido"),
    ("91", "Archivo Ilegible"),
    ("99", "Envio Rechazado - Otros"),
]


class SIIXMLEnvio(models.Model):
    _name = "sii.xml.envio"
    _inherit = ['mail.thread']
    _description = "XML de envío DTE"

    name = fields.Char(string="Nombre de envío", required=True, readonly=True,)
    xml_envio = fields.Text(string="XML Envío", readonly=True, )
    state = fields.Selection(
        [
            ("draft", "Borrador"),
            ("NoEnviado", "No Enviado"),
            ("Enviado", "Enviado"),
            ("EnProceso", "En Proceso"),
            ("Aceptado", "Aceptado"),
            ("Rechazado", "Rechazado"),
        ],
        default="draft",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Compañia",
        required=True,
        default=lambda self: self.env.company.id,
        readonly=True,
    )
    sii_xml_response = fields.Text(string="SII XML Response", copy=False, readonly=True,)
    sii_send_ident = fields.Text(string="SII Send Identification", copy=False, readonly=True,)
    sii_receipt = fields.Text(string="SII Mensaje de recepción", copy=False, readonly=True,)
    move_ids = fields.One2many(
        "account.move", "sii_xml_request", string="Facturas", readonly=True,
    )
    attachment_id = fields.Many2one("ir.attachment", string="XML Recepción", readonly=True,)
    email_respuesta = fields.Text(string="Email SII", readonly=True,)
    email_estado = fields.Selection(status_dte, string="Respuesta Envío", readonly=True,)
    email_glosa = fields.Text(string="Glosa Recepción", readonly=True,)
    boleta_voucher_ids = fields.One2many(
        "account.move.boleta_voucher", "sii_xml_request", string="NC Vouchers Boletas", readonly=True,
    )
    book_ids = fields.One2many(
        "account.move.book", "sii_xml_request", string="Libros", readonly=True,
    )
    consumo_folios_ids = fields.One2many(
        "account.move.consumo_folios", "sii_xml_request", string="Consumo Folios", readonly=True,
    )
    @api.depends('name', 'sii_send_ident')
    def _compute_display_name(self):
        for r in self:
            r.display_name = "{} Código Envío: {}".format(r.name, r.sii_send_ident) if r.sii_send_ident else r.name


    def unlink(self):
        for r in self:
            if r.state in ["Aceptado", "Enviado"]:
                raise UserError(_("You can not delete a valid document on SII"))
        return super(SIIXMLEnvio, self).unlink()

    def _emisor(self, company=None):
        if not company:
            company = self.company_id
        if not company.dte_resolution_date:
            raise UserError(
                _("La empresa %s no tiene configurada la fecha de resolución SII") % company.name
            )
        Emisor = {}
        Emisor["RUTEmisor"] = company.partner_id.rut()
        Emisor["RznSoc"] = company.name
        Emisor["Modo"] = "produccion" if company.dte_service_provider == "SII" else "certificacion"
        Emisor["NroResol"] = company.dte_resolution_number
        Emisor["FchResol"] = company.dte_resolution_date.strftime("%Y-%m-%d")
        Emisor["ValorIva"] = 19
        return Emisor

    def _resolve_firma(self, company):
        firma_id = self.env.context.get('firma_id')
        if firma_id:
            return self.env['sii.firma'].sudo().browse(firma_id)
        firma = self.env.user.get_digital_signature(company)
        if not firma:
            raise UserError(_(
                "El usuario '%s' no está autorizado a usar firma electrónica para %s."
            ) % (self.env.user.name, company.name))
        return firma

    def _get_datos_empresa(self, company):
        return {
            "Emisor": self._emisor(company),
            "firma_electronica": self._resolve_firma(company).parametros_firma(),
        }

    def get_doc(self):
        for r in self.move_ids:
            return r
        return self.move_ids

    def es_api(self):
        doc = self.get_doc()
        api = doc.document_class_id.es_boleta()
        if self.env.context.get("set_pruebas", False):
            api = False
        return api

    def _postpone_colas(self, delay_seconds):
        delay_seconds = max(int(delay_seconds), 60)
        next_time = datetime.now() + timedelta(seconds=delay_seconds)
        colas = self.env['sii.cola_envio'].search([('envio_id', '=', self.id)])
        if not colas:
            return
        colas.write({'date_time': next_time})
        _logger.warning(
            "SII pidió esperar %ss; posponiendo %d cola(s) hasta %s",
            delay_seconds, len(colas), next_time,
        )

    def send_xml(self):
        if self.sii_send_ident:
            _logger.warning("XML %s ya enviado" % self.name)
            return
        try:
            self.env.cr.execute(
                "SELECT sii_send_ident FROM sii_xml_envio WHERE id = %s FOR UPDATE NOWAIT",
                (self.id,),
            )
            row = self.env.cr.fetchone()
        except psycopg2.errors.LockNotAvailable:
            _logger.warning("XML %s ya está siendo enviado por otra tx; se omite", self.name)
            return
        if row and row[0]:
            return
        datos = self._get_datos_empresa(self.company_id)
        api = self.es_api()
        datos.update(
            {"sii_xml_request": self.xml_envio, "filename": self.name, "api": api,}
        )
        res = fe.enviar_xml(datos)
        self.write(
            {
                "state": res.get("status", "NoEnviado"),
                "sii_send_ident": res.get("sii_send_ident", ""),
                "sii_xml_response": res.get("sii_xml_response", ""),
            }
        )
        retry_after = res.get("retry_after")
        if retry_after:
            try:
                delay = int(retry_after)
            except (TypeError, ValueError):
                delay = 60
            self._postpone_colas(delay)
        if not self.env.registry.in_test_mode():
            self.env.cr.commit()
        try:
            with self.env.cr.savepoint():
                self.set_states()
        except Exception as e:
            _logger.warning(
                f"No se pudo actualizar estados de hijos tras envío {self.name} ({self.sii_send_ident}): {e}",
                exc_info=True,
            )

    def do_send_xml(self):
        self.send_xml()

    def object_receipt(self):
        if '<?xml' in self.sii_receipt:
            return etree.XML(
                self.sii_receipt.replace('<?xml version="1.0" encoding="UTF-8"?>', "")
                .replace("SII:", "")
                .replace(' xmlns="http://www.sii.cl/XMLSchema"', "")
            )
        return json.loads(self.sii_receipt)

    def get_send_status(self):
        datos = self._get_datos_empresa(self.company_id)
        api = self.es_api()
        datos.update(
            {"codigo_envio": self.sii_send_ident, "api": api,}
        )
        res = fe.consulta_estado_envio(datos)
        self.write(
            {"state": res["status"], "sii_receipt": res.get("xml_resp", False),}
        )
        retry_after = res.get("retry_after")
        if retry_after:
            try:
                delay = int(retry_after)
            except (TypeError, ValueError):
                delay = 60
            self._postpone_colas(delay)
        self.set_states()

    def ask_for(self):
        self.get_send_status()

    def solicitar_reenvio_email(self):
        datos = self._get_datos_empresa(self.company_id)
        api = self.es_api()
        datos.update(
            {"codigo_envio": self.sii_send_ident, "api": api,}
        )
        res = fe.reenvio_correo_envio(datos)
        if res.get('errores'):
            raise UserError(res.get('errores'))

    def check_estado_boleta(self, doc, detalles, state):
        for d in detalles:
            if d['tipo'] == doc.document_class_id.sii_code and d['folio'] == doc.sii_document_number:
                if d.get('estado') in ('RLV', 'RPR'):
                    return 'Reparo'
                return 'Rechazado'
        return state

    def set_childs(self, state, detalle_rep_rech=False):
        for r in self.move_ids:
            estado = state
            if r.es_boleta() and detalle_rep_rech:
                estado = self.check_estado_boleta(r, detalle_rep_rech, state)
            r.sii_result = estado

    @api.onchange('state')
    def set_states(self):
        state = self.state
        if state in ['draft', 'NoEnviado']:
            return
        detalle_rep_rech = {}
        if self.sii_receipt:
            receipt = self.object_receipt()
            if type(receipt) is dict:
                if not receipt.get('estadistica'):
                    state = 'Aceptado'
                detalle_rep_rech = []
                if receipt.get('detalle_rep_rech', []) and '"detalle_rep_rech":null' not in receipt.get('detalle_rep_rech', '{}'):
                    if type(receipt['detalle_rep_rech']) is list:
                        detalle_rep_rech = receipt['detalle_rep_rech']
                    else:
                        detalle_rep_rech = json.loads(receipt.get('detalle_rep_rech', '{}'))

            elif receipt.find("RESP_HDR") is not None:
                state = "Aceptado"
        self.set_childs(state, detalle_rep_rech)
        if self.sii_send_ident and state in ("EnProceso", "Aceptado") and 'Libro' not in self.xml_envio:
            self.xml_envio = ""

    def get_all_documents(self):
        """Retorna todos los documentos del envío independiente del modelo"""
        return self.move_ids or self.boleta_voucher_ids or self.book_ids or self.consumo_folios_ids

    def _sort_docs_for_envio(self, docs_list):
        def is_post_doc(doc):
            return (
                (hasattr(doc, 'es_nc') and doc.es_nc())
                or (hasattr(doc, 'es_nd') and doc.es_nd())
            )
        normales = [d for d in docs_list if not is_post_doc(d)]
        post = [d for d in docs_list if is_post_doc(d)]
        return normales + post

    def _build_envio_dte_data(self, docs_list, RUTRecep="60803000-K"):
        self.ensure_one()
        if not docs_list:
            return {}
        docs_list = self._sort_docs_for_envio(list(docs_list))
        company = docs_list[0].company_id
        for doc in docs_list:
            if doc.company_id != company:
                raise UserError(_("Todos los documentos deben ser de la misma empresa"))
        pendientes = self.env[docs_list[0]._name]
        for doc in docs_list:
            if (
                self._context.get("set_pruebas", False)
                or doc.sii_result == "Rechazado"
                or not doc.sii_xml_dte
            ):
                pendientes |= doc
        firma_ctx = {'firma_id': self.env.context['firma_id']} if self.env.context.get('firma_id') else {}
        if pendientes:
            pendientes.with_context(**firma_ctx)._timbrar_batch()
        grupos = {}
        batch = 0
        for doc in docs_list:
            batch += 1
            if hasattr(doc, 'sii_batch_number'):
                if not doc.sii_batch_number or doc.sii_batch_number == 0:
                    doc.sii_batch_number = batch
            dte_data = doc.with_context(**firma_ctx)._prepare_dte_data()
            if not dte_data:
                continue
            if hasattr(doc, 'sii_batch_number') and doc.sii_batch_number:
                dte_data['NroDTE'] = doc.sii_batch_number
            else:
                dte_data['NroDTE'] = batch
            grupos.setdefault(dte_data['TipoDTE'], []).append(dte_data)
        datos = self._get_datos_empresa(company)
        datos.update({
            "RutReceptor": RUTRecep,
            "Documento": [
                {"TipoDTE": k, "documentos": v}
                for k, v in grupos.items()
            ],
        })
        return datos

    @api.model
    def crear_intercambio(self, documents, RUTRecep):
        docs_list = list(documents)
        if not docs_list:
            return b""
        company = docs_list[0].company_id
        firma = docs_list[0]._resolve_firma()
        envio_tmp = self.with_context(firma_id=firma.id).create({
            'name': 'intercambio',
            'xml_envio': 'temporal',
            'company_id': company.id,
        })
        try:
            datos = envio_tmp._build_envio_dte_data(docs_list, RUTRecep=RUTRecep)
            if not datos.get("Documento"):
                return b""
            set_pruebas = self._context.get("set_pruebas", False)
            codigos_boleta = [39, 41]
            es_boleta = any(d["TipoDTE"] in codigos_boleta for d in datos["Documento"])
            datos["api"] = es_boleta and not set_pruebas
            result = fe.xml_envio(datos)
            return result["sii_xml_request"].encode("ISO-8859-1")
        finally:
            envio_tmp.unlink()

    @api.model
    def crear_envio(self, documents, RUTRecep="60803000-K"):
        """Crea envío unificado para cualquier modelo de documento."""
        if not documents:
            return self.browse()

        docs_list = list(documents)
        tipo = getattr(docs_list[0], '_dte_envio_tipo', 'dte')

        if tipo == 'libro':
            return self._crear_envio_libro(docs_list)
        elif tipo == 'consumo_folios':
            return self._crear_envio_consumo_folios(docs_list)
        else:
            return self._crear_envio_dte(docs_list, RUTRecep)

    @api.model
    def _crear_envio_dte(self, docs_list, RUTRecep="60803000-K"):
        """Crea envío para documentos DTE."""
        if not docs_list:
            return self.browse()

        envio_id = docs_list[0].sii_xml_request
        if not envio_id:
            for doc in docs_list:
                if doc.sii_xml_request:
                    envio_id = doc.sii_xml_request
                    break
        if envio_id and envio_id.sii_send_ident:
            return envio_id
        if (envio_id and envio_id.xml_envio
                and envio_id.xml_envio not in ("temporal", "")
                and not envio_id.sii_send_ident):
            envio_id.send_xml()
            return envio_id

        company = docs_list[0].company_id
        set_pruebas = self._context.get("set_pruebas", False)
        firma = docs_list[0]._resolve_firma()

        if not envio_id:
            doc_field = docs_list[0]._dte_xml_envio_field
            vals = {
                'name': 'temporal',
                'xml_envio': 'temporal',
                'company_id': company.id,
            }
            if doc_field:
                vals[doc_field] = [(6, 0, [d.id for d in docs_list])]
            envio_id = self.create(vals)
        envio_id = envio_id.with_context(firma_id=firma.id)

        datos = envio_id._build_envio_dte_data(
            docs_list,
            RUTRecep=RUTRecep,
        )
        if not datos.get("Documento"):
            return self.browse()

        codigos_boleta = [39, 41]
        es_boleta = any(d["TipoDTE"] in codigos_boleta for d in datos["Documento"])
        datos["api"] = es_boleta and not set_pruebas

        if not envio_id.exists():
            envio_id = docs_list[0].sii_xml_request
            if not envio_id:
                for doc in docs_list:
                    if doc.sii_xml_request:
                        envio_id = doc.sii_xml_request
                        break
            if not envio_id:
                doc_field = docs_list[0]._dte_xml_envio_field
                vals = {
                    'name': 'temporal',
                    'xml_envio': 'temporal',
                    'company_id': company.id,
                }
                if doc_field:
                    vals[doc_field] = [(6, 0, [d.id for d in docs_list])]
                envio_id = self.create(vals)
            envio_id = envio_id.with_context(firma_id=firma.id)

        if envio_id.sii_send_ident:
            _SII_SEND_RESULT_CACHE.pop(envio_id.id, None)
            return envio_id

        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext('sii_xml_envio'), %s)",
            (envio_id.id,),
        )
        envio_id.invalidate_recordset(['sii_send_ident'])
        if envio_id.sii_send_ident:
            _SII_SEND_RESULT_CACHE.pop(envio_id.id, None)
            return envio_id

        datos["ID"] = "Env%s" % envio_id.id
        cached = _SII_SEND_RESULT_CACHE.get(envio_id.id)
        if cached and cached.get("sii_send_ident"):
            _logger.info(
                "Reusando resultado SII en memoria para envío %s (retry tras SerializationFailure)",
                envio_id.id,
            )
            result = cached
        else:
            result = fe.timbrar_y_enviar(datos)
            _SII_SEND_RESULT_CACHE[envio_id.id] = result

        send_ident = result.get("sii_send_ident")
        if result.get("status") == "Rechazado" and send_ident == 1:
            send_ident = False
        envio_id.write({
            "xml_envio": result.get("sii_xml_request", "temporal"),
            "name": result.get("sii_send_filename", "temporal"),
            "sii_send_ident": send_ident,
            "sii_xml_response": result.get("sii_xml_response"),
            "state": result.get("status"),
        })

        if not self.env.registry.in_test_mode():
            try:
                self.env.cr.commit()
                if send_ident:
                    _SII_SEND_RESULT_CACHE.pop(envio_id.id, None)
            except psycopg2.errors.SerializationFailure:
                _logger.warning(
                    "SerializationFailure al persistir envío %s; resultado SII queda en caché para reintento.",
                    envio_id.id,
                )
                raise

        sii_result = result.get("sii_result")
        if not sii_result and send_ident:
            sii_result = "Enviado"
        for doc in docs_list:
            try:
                with self.env.cr.savepoint():
                    doc.sii_xml_request = envio_id
                    if sii_result:
                        doc.sii_result = sii_result
            except Exception as e:
                _logger.warning(
                    f"No se pudo actualizar {doc._name} id={doc.id} tras envío {envio_id.name} ({envio_id.sii_send_ident}): {e}",
                    exc_info=True,
                )

        return envio_id

    @api.model
    def _crear_envio_libro(self, documents):
        """Crea envío para libros de compra/venta."""
        doc = documents[0]

        if doc.sii_xml_request and doc.sii_xml_request.state == "Rechazado":
            if len(doc.sii_xml_request[doc._dte_xml_envio_field]) <= 1:
                doc.sii_xml_request.unlink()
            else:
                doc.sii_xml_request = False
            doc._validar()

        envio = doc.sii_xml_request
        if not envio:
            raise UserError(_("El libro debe ser validado antes de enviar"))
        if envio.sii_send_ident:
            return envio

        envio = envio.with_context(firma_id=doc._resolve_firma().id)
        datos = envio._get_datos_empresa(doc.company_id)
        datos.update({
            "Libro": {
                "PeriodoTributario": doc.periodo_tributario,
                "TipoOperacion": doc._dte_tipo_operacion(),
                "TipoLibro": doc.tipo_libro,
                "TipoEnvio": doc.tipo_envio,
            },
            "sii_xml_request": envio.xml_envio.replace(
                '<?xml version="1.0" encoding="ISO-8859-1"?>\n', ""
            ),
            "ID": envio.name,
        })

        result = fe.libro(datos)

        envio.write({
            "xml_envio": result["sii_xml_request"],
            "name": result["sii_send_filename"],
            "company_id": doc.company_id.id,
            "sii_send_ident": result.get("sii_send_ident"),
            "sii_xml_response": result.get("sii_xml_response"),
            "state": result.get("sii_result"),
        })

        if not self.env.registry.in_test_mode():
            self.env.cr.commit()

        try:
            with self.env.cr.savepoint():
                doc.sii_result = "Enviado"
        except Exception as e:
            _logger.warning(
                f"No se pudo actualizar estado del libro {doc._name} id={doc.id} tras envío {envio.name}: {e}",
                exc_info=True,
            )
        return envio

    @api.model
    def _crear_envio_consumo_folios(self, documents):
        """Crea envío para consumo de folios."""
        doc = documents[0]

        if not doc.sii_xml_request:
            doc._validar()

        if doc.sii_xml_request and doc.sii_xml_request.state == "Rechazado":
            if len(doc.sii_xml_request.consumo_folios_ids) <= 1:
                doc.sii_xml_request.unlink()
            else:
                doc.sii_xml_request = False
            doc._validar()
            doc.sii_xml_request.state = "NoEnviado"

        envio = doc.sii_xml_request
        if envio:
            if doc.sii_result in ["NoEnviado", "EnCola"]:
                envio.with_context(firma_id=doc._resolve_firma().id).send_xml()

        return envio
