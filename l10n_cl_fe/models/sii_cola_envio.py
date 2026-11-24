import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta


from odoo import _, SUPERUSER_ID, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ColaEnvio(models.Model):
    _name = "sii.cola_envio"
    _inherit = ['mail.thread']
    _description = "Cola de envío DTE Persistente al SII"
    _order = "sequence, id"

    name = fields.Char(string="Nombre", compute="_compute_name", store=True)
    tipo_trabajo = fields.Selection(
        [
            ("pasivo", "pasivo"),
            ("envio", "Envío"),
            ("consulta", "Consulta"),
            ("persistencia", "Persistencia Respuesta"),
        ],
        string="Tipo de trabajo",
    )
    active = fields.Boolean(string="Active", default=True,)
    n_atencion = fields.Char(string="Número de Atención",)
    set_pruebas = fields.Boolean(string="Set de pruebas",
                                 default=False)
    date_time = fields.Datetime(string="Auto Envío al SII",)
    send_email = fields.Boolean(string="Auto Enviar Email", default=False,)
    company_id = fields.Many2one("res.company", string="Company")
    firma_id = fields.Many2one(
        "sii.firma",
        string="Firma",
        help="Firma electrónica usada para agrupar los packs",
    )
    intento = fields.Integer(string="Intento", default=0, readonly=True)
    manual = fields.Boolean(string="Manual", default=False, help="No incluir en autopack")
    es_boleta = fields.Boolean(string="Es Boleta", default=False, help="Pack de boletas electrónicas")
    sequence = fields.Integer(string="Secuencia", default=10, help="Orden de procesamiento")
    processing = fields.Boolean(
        string="Procesando", default=False, copy=False,
        help="True mientras el cron está armando dict/XML/envío. Bloquea la incorporación de nuevos docs.",
    )

    document_ids = fields.Many2many(
        comodel_name='account.move',
        relation='sii_cola_envio_account_move_rel',
        string="Documentos",
    )
    book_ids = fields.Many2many(
        comodel_name='account.move.book',
        relation='sii_cola_envio_book_rel',
        string="Libros",
    )
    consumo_folios_ids = fields.Many2many(
        comodel_name='account.move.consumo_folios',
        relation='sii_cola_envio_consumo_folios_rel',
        string="Consumo Folios",
    )
    boleta_voucher_ids = fields.Many2many(
        comodel_name='account.move.boleta_voucher',
        relation='sii_cola_envio_boleta_voucher_rel',
        string="Boletas Voucher",
    )

    document_count = fields.Integer(
        string="Cantidad Documentos",
        compute="_compute_document_count",
        store=True,
    )
    envio_id = fields.Many2one(
        "sii.xml.envio",
        string="XML Envío",
        ondelete="set null",
        copy=False,
        help="Sobre SII generado al procesar esta cola. Una cola = un xml.envio.",
    )
    queue_log = fields.Json(
        string="Log de encoladores",
        default=dict,
        help="Mapa de 'modelo:doc_id' a datos de quien encoló el documento",
    )

    @api.depends('document_ids', 'book_ids', 'consumo_folios_ids', 'boleta_voucher_ids')
    def _compute_document_count(self):
        for rec in self:
            rec.document_count = (
                len(rec.document_ids) +
                len(rec.book_ids) +
                len(rec.consumo_folios_ids) +
                len(rec.boleta_voucher_ids)
            )


    @api.depends('tipo_trabajo', 'company_id', 'document_count')
    def _compute_name(self):
        for rec in self:
            rec.name = f"{rec.tipo_trabajo or ''} - {rec.company_id.name or ''} ({rec.document_count})"

    def action_reset_intentos(self):
        """Resetea los intentos del registro actual."""
        self.intento = 0
        return True

    def action_procesar(self):
        """Procesa manualmente el registro actual."""
        self._procesar_tipo_trabajo()
        return True

    def action_asignar_firma_actual(self):
        """Asigna la firma del usuario actual a las colas seleccionadas."""
        sin_firma = self.filtered(lambda c: not c.firma_id)
        for company, colas in sin_firma.grouped("company_id").items():
            firma = self.env.user.sudo().get_digital_signature(company)
            if not firma:
                raise UserError(_(
                    "El usuario '%s' no tiene firma electrónica para la "
                    "compañía '%s'. Asígnele una firma antes de continuar."
                ) % (self.env.user.name, company.name))
            colas.write({"firma_id": firma.id})
        return True

    def get_all_documents_by_model(self):
        """Retorna dict con documentos agrupados por modelo."""
        self.ensure_one()
        result = {}
        if self.document_ids:
            result['account.move'] = self.document_ids
        if self.book_ids:
            result['account.move.book'] = self.book_ids
        if self.consumo_folios_ids:
            result['account.move.consumo_folios'] = self.consumo_folios_ids
        if self.boleta_voucher_ids:
            result['account.move.boleta_voucher'] = self.boleta_voucher_ids
        return result

    def enviar_email(self, doc):
        doc.send_exchange()

    def _es_doc(self, doc):
        return doc._es_doc_cola()

    def _get_ctx(self):
        return {
            'company_id': self.company_id.id,
            'set_pruebas': self.set_pruebas,
            'n_atencion': self.n_atencion or '',
            'firma_id': self.firma_id.id,
        }

    def _get_all_docs_with_context(self):
        """Retorna todos los documentos con contexto aplicado, agrupados por modelo."""
        ctx = self._get_ctx()
        docs_by_model = {}
        for model, docs in self.get_all_documents_by_model().items():
            if docs:
                docs_by_model[model] = docs.with_context(**ctx)
        return docs_by_model

    def _get_first_doc(self, docs_by_model):
        """Retorna el primer documento de cualquier modelo."""
        for docs in docs_by_model.values():
            if docs:
                return docs[0]
        return False

    TERMINAL_SII_RESULTS = ("Proceso", "Reparo", "Rechazado", "Anulado")
    ENVIO_TERMINAL_STATES = ("Aceptado", "Rechazado")

    def _should_finish(self, docs_by_model):
        has_doc = False
        for docs in docs_by_model.values():
            for doc in docs:
                if not self._es_doc(doc):
                    return False
                has_doc = True
                if doc.sii_result not in self.TERMINAL_SII_RESULTS:
                    return False
        return has_doc

    def _should_skip_envio(self, first_doc):
        """Verifica si ya hay un envío en proceso al SII.

        Sólo se salta el envío si el SII ya recibió el documento
        (sii_send_ident presente y estado avanzado). Si el envío es temporal
        (sin sii_send_ident) hay que reintentar, no pasar a consulta."""
        if not first_doc or not first_doc.sii_xml_request:
            return False
        if not first_doc.sii_xml_request.sii_send_ident:
            return False
        return first_doc.sii_xml_request.state in ("Aceptado", "Enviado", "EnProceso")

    @staticmethod
    def _docs_sin_envio(docs_by_model):
        return any(
            not (d.sii_xml_request and d.sii_xml_request.sii_send_ident)
            for docs in docs_by_model.values() for d in docs
        )

    def _needs_envio(self, first_doc):
        """Verifica si necesita envío."""
        if not first_doc:
            return False
        return (
            not first_doc.sii_xml_request
            or not first_doc.sii_xml_request.sii_send_ident
            or first_doc.sii_xml_request.state not in ["Aceptado", "Enviado", "EnProceso"]
        )

    def _try_lock(self):
        """Advisory lock no bloqueante por fila. True si lo obtuvimos.
        Se libera al fin de la transacción. Reemplaza FOR UPDATE NOWAIT para
        evitar logs ruidosos de LockNotAvailable y SerializationFailure."""
        self.ensure_one()
        self.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext('sii_cola_envio'), %s)",
            (self.id,),
        )
        return self.env.cr.fetchone()[0]

    def _has_pending_reference_in_other_cola(self, docs_by_model):
        for model, docs in docs_by_model.items():
            model_info = self._get_model_info(model)
            if not model_info:
                continue
            ref_field = model_info['referencias_field']
            for doc in docs:
                for ref in doc[ref_field]:
                    ref_doc = self._get_referenced_doc(ref, model_info)
                    if not ref_doc:
                        continue
                    if ref_doc.sii_xml_request and ref_doc.sii_xml_request.sii_send_ident:
                        continue
                    if ref_doc in docs:
                        continue
                    cola_field = ref_doc._dte_cola_field
                    if not cola_field:
                        continue
                    other = self.search([
                        ('id', '!=', self.id),
                        ('envio_id.sii_send_ident', '=', False),
                        (cola_field, 'in', ref_doc.ids),
                    ], limit=1)
                    if other:
                        return other
        return False

    def _procesar_envio(self, docs_by_model):
        """Procesa envío para todos los documentos."""
        otra = self._has_pending_reference_in_other_cola(docs_by_model)
        if otra:
            _logger.info(
                "Cola %s posterga envío: doc referenciado está en cola %s aún sin enviar.",
                self.id, otra.id,
            )
            return
        todo_enviado = True
        for model, docs in docs_by_model.items():
            try:
                restantes = docs
                while restantes:
                    envio = self._do_envio(restantes)
                    if not (envio and envio.sii_send_ident):
                        break
                    incluidos = restantes.filtered(lambda d: d.sii_xml_request == envio)
                    if not incluidos:
                        break
                    if not self.envio_id:
                        self.envio_id = envio
                    self._post_queue_log_to_xml_envio(envio, model, incluidos)
                    pendientes = incluidos.filtered(
                        lambda d: d.sii_result in ('', 'NoEnviado', 'EnCola', False)
                    )
                    if pendientes:
                        pendientes.write({'sii_result': 'Enviado'})
                    restantes -= incluidos
                    self.env.cr.commit()
                if restantes:
                    todo_enviado = False
            except Exception as e:
                self.env.cr.rollback()
                self._registrar_error(e, f'envío cola {model}')
                todo_enviado = False
        if todo_enviado:
            self.tipo_trabajo = "consulta"
            self.env.cr.commit()

    def _post_queue_log_to_xml_envio(self, xml_envio, model, docs):
        """Publica en el chatter del xml.envio quién encoló cada documento."""
        if not xml_envio or not hasattr(xml_envio, 'message_post'):
            return
        log = self.queue_log or {}
        lineas = []
        for doc in docs:
            entry = log.get(f"{model}:{doc.id}")
            doc_name = doc.display_name or doc.name
            if entry:
                user_name = entry.get('user_name') or ''
                ts = entry.get('ts') or ''
                lineas.append(
                    f"<li>{entry.get('doc_name') or doc_name} — encolado por <b>{user_name}</b> ({ts})</li>"
                )
            else:
                lineas.append(f"<li>{doc_name} — sin registro de encolador</li>")
        if not lineas:
            return
        xml_envio.message_post(
            body=f"<p>Documentos encolados en este envío:</p><ul>{''.join(lineas)}</ul>",
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )

    def _procesar_consulta(self, docs_by_model):
        if not self._envio_listo_para_consulta_doc():
            return
        for model, docs in docs_by_model.items():
            ready = docs.filtered(
                lambda r: r.sii_xml_request and r.sii_xml_request.sii_send_ident
            )
            if not ready:
                continue
            self._consulta_docs_paralelo(model, ready.ids)

    def _envio_listo_para_consulta_doc(self):
        envio = self.envio_id
        if not envio or not envio.sii_send_ident:
            return False
        if envio.state in self.ENVIO_TERMINAL_STATES:
            return True
        try:
            envio.with_context(**self._get_ctx()).get_send_status()
        except Exception as e:
            self._registrar_error(e, 'consulta envio')
        return False

    def _consulta_doc_en_thread(self, doc_id, model, ctx):
        pool = self.pool
        try:
            with pool.cursor() as new_cr:
                new_env = api.Environment(new_cr, SUPERUSER_ID, ctx)
                doc = new_env[model].browse(doc_id).exists()
                if not doc:
                    return
                try:
                    doc.ask_for_dte_status()
                except Exception as e:
                    _logger.warning(
                        "consulta doc %s:%s falló: %s",
                        model, doc_id, e, exc_info=True,
                    )
        except Exception as e:
            _logger.warning(
                "Worker consulta doc %s:%s abortó: %s",
                model, doc_id, e, exc_info=True,
            )

    def _consulta_docs_paralelo(self, model, doc_ids):
        if not doc_ids:
            return
        ctx = self._get_ctx()
        if len(doc_ids) == 1:
            self._consulta_doc_en_thread(doc_ids[0], model, ctx)
            return
        max_workers = int(self.env['ir.config_parameter'].sudo().get_param(
            'l10n_cl_fe.consulta_doc_paralelo_workers', '5'
        ))
        max_workers = min(max(1, max_workers), len(doc_ids))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(ex.map(
                lambda doc_id: self._consulta_doc_en_thread(doc_id, model, ctx),
                doc_ids,
            ))

    def _procesar_pasivo(self, docs_by_model, first_doc):
        """Procesa tipo trabajo pasivo."""
        if self._should_finish(docs_by_model):
            self._procesar_finalizado(docs_by_model, first_doc)
            return
        if self._should_skip_envio(first_doc) and not self._docs_sin_envio(docs_by_model):
            if not self.envio_id:
                self.envio_id = first_doc.sii_xml_request
            self.tipo_trabajo = "consulta"
            return
        self._procesar_envio(docs_by_model)

    def _procesar_persistencia(self, docs_by_model):
        """Procesa persistencia de respuesta."""
        if not self.date_time or datetime.now() < self.date_time:
            return

        docs_to_remove = []
        all_docs = []
        for docs in docs_by_model.values():
            all_docs.extend(list(docs))

        for doc in all_docs:
            if (
                doc._dte_requires_response_persistence()
                and doc.sii_xml_request.create_date <= (datetime.now() + timedelta(days=8))
                and self.env["sii.respuesta.cliente"].search([
                    ("id", "in", doc.respuesta_ids.ids),
                    ("company_id", "=", self.company_id.id),
                    ("recep_envio", "=", "no_revisado"),
                    ("type", "=", "RecepcionEnvio"),
                ])
            ):
                self.enviar_email(doc)
            else:
                docs_to_remove.append(doc)

        remaining_docs = [d for d in all_docs if d not in docs_to_remove]
        if not remaining_docs:
            self.unlink()
        else:
            persistente = self.env["ir.config_parameter"].sudo().get_param(
                "account.auto_send_persistencia", default=24
            )
            self.date_time = datetime.now() + timedelta(hours=int(persistente))

    def _procesar_finalizado(self, docs_by_model, first_doc):
        """Procesa cuando el documento ya tiene resultado final."""
        if self.send_email and first_doc.sii_result in ["Proceso", "Reparo"]:
            docs_to_remove = []
            all_docs = []
            for docs in docs_by_model.values():
                all_docs.extend(list(docs))

            for doc in all_docs:
                if not doc.partner_id:
                    docs_to_remove.append(doc)
                    continue
                self.enviar_email(doc)

            remaining_docs = [d for d in all_docs if d not in docs_to_remove]
            persistencia_docs = [
                d for d in remaining_docs if d._dte_requires_response_persistence()
            ]
            if not persistencia_docs:
                self.unlink()
                return
            self.tipo_trabajo = "persistencia"
            persistente = self.env["ir.config_parameter"].sudo().get_param(
                "account.auto_send_persistencia", default=24
            )
            self.date_time = datetime.now() + timedelta(hours=int(persistente))
            return
        self.unlink()

    def _validar_firma(self):
        """Valida que la cola tenga firma electrónica asignada."""
        if not self.firma_id:
            _logger.warning(f"Cola {self.id} sin firma electrónica")
            return False
        return True

    def _procesar_tipo_trabajo(self):
        if not self._try_lock():
            return
        self.invalidate_recordset(['processing'])
        if self.processing:
            return
        self.processing = True
        self.intento += 1
        self.env.cr.commit()
        try:
            if not self._validar_firma():
                return
            self.invalidate_recordset()
            docs_by_model = self._get_all_docs_with_context()
            if not docs_by_model:
                return
            first_doc = self._get_first_doc(docs_by_model)
            if self.tipo_trabajo == "persistencia":
                self._procesar_persistencia(docs_by_model)
            elif self.tipo_trabajo == "pasivo":
                self._procesar_pasivo(docs_by_model, first_doc)
            elif self._should_finish(docs_by_model):
                self._procesar_finalizado(docs_by_model, first_doc)
            elif self.tipo_trabajo == "consulta":
                self._procesar_consulta(docs_by_model)
            elif self.tipo_trabajo == "envio":
                if self._should_skip_envio(first_doc) and not self._docs_sin_envio(docs_by_model):
                    self.tipo_trabajo = "consulta"
                elif self._needs_envio(first_doc) or self._docs_sin_envio(docs_by_model):
                    self._procesar_envio(docs_by_model)
        finally:
            try:
                if self.exists():
                    self.processing = False
            except Exception:
                pass

    def _procesar_cola_segura(self, tipo_trabajo):
        try:
            self._procesar_tipo_trabajo()
        except Exception as e:
            self.env.cr.rollback()
            if not self.exists():
                return
            self._registrar_error(e, tipo_trabajo)
            self.processing = False

    def reset_intentos(self):
        query = """
            UPDATE sii_cola_envio
            SET intento = 0
            WHERE intento >= 10
            AND create_date <= (NOW() - INTERVAL '1 day')
        """
        self.env.cr.execute(query)
        query = """
            UPDATE sii_cola_envio
            SET processing = false
            WHERE processing = true
            AND write_date <= (NOW() - INTERVAL '1 hour')
        """
        self.env.cr.execute(query)

    def _get_autopack_domain(self, tipo_trabajo):
        """Retorna domain base para buscar items de autopack."""
        domain = [
            ("active", "=", True),
            ('intento', '<=', 10),
            ('tipo_trabajo', '=', tipo_trabajo),
            ('manual', '=', False),
            ('processing', '=', False),
            ('firma_id', '!=', False),
            ('book_ids', '=', False),
            ('consumo_folios_ids', '=', False),
        ]
        if tipo_trabajo == 'pasivo':
            domain.append(('date_time', '>', datetime.now()))
        return domain

    def _get_model_info(self, model):
        """Retorna información del modelo para autopack."""
        if model == 'account.move':
            return {
                'referencias_model': 'account.move.referencias',
                'referencias_field': 'referencias',
                'parent_field': 'move_id',
            }
        return False

    def _do_envio(self, docs):
        if not docs:
            return False
        return docs.do_dte_send()

    def _get_referenced_doc(self, referencia, model_info):
        """Obtiene el documento referenciado."""
        if not referencia.ref_id or not referencia.ref_id.dte:
            return False
        folio = referencia.folio
        if not folio:
            return False
        try:
            doc_number = int(folio)
        except (ValueError, TypeError):
            return False
        parent_field = model_info['parent_field']
        parent_doc = referencia[parent_field]
        return parent_doc.search([
            ('sii_document_number', '=', doc_number),
            ('document_class_id', '=', referencia.ref_id.id),
            ('company_id', '=', parent_doc.company_id.id),
        ], limit=1)

    def _get_queue_dependencies(self, queue_items):
        """Construye mapa de dependencias basado en referencias DTE."""
        doc_to_queue = {}
        for item in queue_items:
            for model, docs in item.get_all_documents_by_model().items():
                model_info = self._get_model_info(model)
                if model_info:
                    for doc in docs:
                        doc_to_queue[(model, doc.id)] = item

        dependencies = {}
        for item in queue_items:
            dependencies[item.id] = set()
            for model, docs in item.get_all_documents_by_model().items():
                model_info = self._get_model_info(model)
                if not model_info:
                    continue
                ref_field = model_info['referencias_field']
                for doc in docs:
                    for ref in doc[ref_field]:
                        ref_doc = self._get_referenced_doc(ref, model_info)
                        if ref_doc and (model, ref_doc.id) in doc_to_queue:
                            ref_queue_item = doc_to_queue[(model, ref_doc.id)]
                            if ref_queue_item.id != item.id:
                                dependencies[item.id].add(ref_queue_item.id)
        return dependencies

    def _topological_sort(self, queue_items, dependencies):
        """Ordena items respetando dependencias."""
        result = []
        in_degree = {item.id: len(dependencies.get(item.id, set())) for item in queue_items}
        item_map = {item.id: item for item in queue_items}

        queue = [item for item in queue_items if in_degree[item.id] == 0]

        while queue:
            item = queue.pop(0)
            result.append(item)
            for other_id, deps in dependencies.items():
                if item.id in deps:
                    in_degree[other_id] -= 1
                    if in_degree[other_id] == 0 and item_map[other_id] not in result:
                        queue.append(item_map[other_id])

        for item in queue_items:
            if item not in result:
                result.append(item)

        return result

    def _docs_por_pack_for(self, es_boleta):
        return 50 if es_boleta else 20

    def _autopack(self):
        """Agrupa items de cola en packs; 50 docs para boletas, 20 para resto."""
        for tipo_trabajo in ('envio', 'pasivo'):
            domain = self._get_autopack_domain(tipo_trabajo)
            items = self.search(domain).filtered(lambda i: all(
                getattr(docs, '_dte_envio_tipo', 'dte') == 'dte'
                for docs in i.get_all_documents_by_model().values()))
            if not items:
                continue

            items_auto = items.filtered(
                lambda i: i.document_count < self._docs_por_pack_for(i.es_boleta)
            )
            if not items_auto:
                continue

            dependencies = self._get_queue_dependencies(items_auto)
            sorted_items = self._topological_sort(items_auto, dependencies)

            for seq, item in enumerate(sorted_items):
                item.sequence = seq

            packs = {}
            for item in sorted_items:
                pack_key = self._get_pack_key(item)
                item_count = item.document_count
                limit = self._docs_por_pack_for(item.es_boleta)
                pack = packs.get(pack_key)
                if pack and pack['count'] + item_count > limit:
                    if len(pack['items']) > 1:
                        self._unificar_items(pack['items'])
                    pack = None
                if pack is None:
                    pack = {'items': self.browse(), 'count': 0}
                    packs[pack_key] = pack
                pack['items'] |= item
                pack['count'] += item_count

            for pack in packs.values():
                if len(pack['items']) > 1:
                    self._unificar_items(pack['items'])

    def _get_pack_key(self, item):
        """Retorna la clave para agrupar items en un pack."""
        return (
            item.company_id.id,
            item.firma_id.id,
            item.set_pruebas,
            item.n_atencion,
            item.es_boleta,
        )

    def _unificar_items(self, items):
        """Unifica múltiples items de cola en uno solo."""
        if len(items) <= 1:
            return
        lockables = self.browse()
        for item in items:
            if not item._try_lock():
                continue
            item.invalidate_recordset(['processing'])
            if item.processing:
                continue
            lockables |= item
        if len(lockables) <= 1:
            return
        items = lockables
        items.invalidate_recordset()
        first_item = items[0]
        max_sequence = 0
        merged_log = dict(first_item.queue_log or {})

        docs_by_model = {}
        for item in items:
            for model, docs in item.get_all_documents_by_model().items():
                docs_by_model.setdefault(model, self.env[model])
                docs_by_model[model] |= docs
            max_sequence = max(max_sequence, item.sequence)
            if item != first_item and item.queue_log:
                merged_log.update(item.queue_log)

        for model, docs in docs_by_model.items():
            doc_field = docs._dte_cola_field
            if doc_field:
                first_item[doc_field] = [(6, 0, docs.ids)]

        first_item.queue_log = merged_log
        first_item.sequence = max_sequence
        (items - first_item).unlink()

    def _get_queue_domain(self, vals):
        """Retorna domain para buscar cola existente compatible."""
        return [
            ('active', '=', True),
            ('manual', '=', False),
            ('processing', '=', False),
            ('intento', '=', 0),
            ('company_id', '=', vals.get('company_id')),
            ('firma_id', '=', vals.get('firma_id')),
            ('tipo_trabajo', '=', vals.get('tipo_trabajo')),
            ('es_boleta', '=', vals.get('es_boleta', False)),
            ('book_ids', '=', False),
            ('consumo_folios_ids', '=', False),
        ]

    def _log_user_docs(self, documents):
        self.ensure_one()
        user = self.env.user
        model = documents._name
        log = dict(self.queue_log or {})
        ts = fields.Datetime.now().isoformat()
        for doc in documents:
            log["%s:%s" % (model, doc.id)] = {
                "user_id": user.id,
                "user_name": user.name,
                "ts": ts,
                "doc_name": doc.name or str(doc.id),
            }
        self.queue_log = log

    def _registrar_error(self, error, tipo_trabajo):
        """Registra error en el log y como mensaje."""
        import traceback
        error_msg = str(error)
        trace = traceback.format_exc()
        _logger.warning(f"error al procesar tipo trabajo {tipo_trabajo}: {error_msg}", exc_info=True)
        self.message_post(
            body=f"<p><strong>Error en {tipo_trabajo}:</strong></p><pre>{error_msg}</pre><details><summary>Detalle</summary><pre>{trace}</pre></details>",
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )

    @api.model
    def _cron_procesar_cola(self):
        self.reset_intentos()
        self._autopack()
        query = [
            ("active", "=", True),
            ('intento', '<=', 10),
            ('processing', '=', False),
        ]

        not_throttled = ['|', ('date_time', '=', False), ('date_time', '<=', datetime.now())]

        ids = self.search(query + not_throttled + [('tipo_trabajo', '=', 'envio')], limit=20)
        for c in ids:
            c._procesar_cola_segura('envio')

        ids = self.search(query + [
            ('tipo_trabajo', '=', 'pasivo'),
            ('date_time', '<=', datetime.now())], limit=20)
        for c in ids:
            c._procesar_cola_segura('pasivo')

        ids = self.search(query + not_throttled + [('tipo_trabajo', '=', 'consulta')], limit=20)
        for c in ids:
            c._procesar_cola_segura('consulta')

        ids = self.search(query + [('tipo_trabajo', '=', 'persistencia')], limit=20)
        for c in ids:
            c._procesar_cola_segura('persistencia')
