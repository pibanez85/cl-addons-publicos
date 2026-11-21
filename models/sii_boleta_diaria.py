from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

ESTADOS_PAGO_EXCLUIDOS = ("draft", "cancel")
TOLERANCIA_DIAS_48 = 1
ESTADOS_48 = [
    ("sin_pagos", "Sin pagos en Odoo"),
    ("rebaja_pendiente", "Rebaja pendiente"),
    ("diferencia", "Con diferencia"),
    ("conciliado", "Conciliado"),
]
ESTADOS_REBAJA = [
    ("por_aplicar", "Por aplicar en el SII"),
    ("aplicada", "Aplicada, sin verificar"),
    ("verificada", "Verificada en el SII"),
    ("sin_reflejo", "Aplicada sin reflejo en el SII"),
    ("descartada", "Descartada"),
]


class SiiBoletaDiaria(models.Model):
    _name = "sii.boleta.diaria"
    _description = "Boletas diarias del SII"
    _inherit = ["mail.thread"]
    _order = "fecha desc, tipo_dte, canal"

    company_id = fields.Many2one("res.company", string="Compañía", required=True, readonly=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    fecha = fields.Date(required=True, readonly=True, index=True)
    periodo = fields.Char(compute="_compute_periodo", store=True, index=True)
    tipo_dte = fields.Integer(string="Tipo", required=True, readonly=True)
    canal = fields.Selection(
        [("presencial", "Presencial"), ("internet", "Internet")], readonly=True,
        help="Presencial: máquinas de pago. Internet: pasarelas de pago. Vacío: el SII no separa por canal",
    )
    canal_sii = fields.Char(string="Canal (SII)", readonly=True)
    cantidad = fields.Integer(readonly=True, tracking=True)
    neto = fields.Monetary(readonly=True, tracking=True)
    exento = fields.Monetary(readonly=True, tracking=True)
    iva = fields.Monetary(string="IVA", readonly=True, tracking=True)
    total = fields.Monetary(readonly=True, tracking=True)
    iva_terceros = fields.Monetary(string="IVA de terceros", readonly=True)
    operaciones_exentas = fields.Monetary(readonly=True)
    modificado = fields.Boolean(string="Modificado en el SII", readonly=True)
    motivo_codigo = fields.Char(string="Código de motivo", readonly=True)
    motivo = fields.Char(readonly=True)
    primera_total = fields.Monetary(string="Total visto por primera vez", readonly=True)
    primera_iva = fields.Monetary(string="IVA visto por primera vez", readonly=True)
    rebaja_inferida = fields.Monetary(compute="_compute_rebaja_inferida", store=True,
                                      help="Total visto por primera vez menos el total actual del SII")
    informado = fields.Boolean(string="Informado por el SII", default=True, readonly=True)
    consultado = fields.Datetime(readonly=True)
    pago_ids = fields.One2many("account.payment", "boleta_48_id", string="Pagos en Odoo")
    rebaja_ids = fields.One2many("sii.boleta.rebaja", "fila_id", string="Rebajas por facturas")
    boletas_voucher = fields.Monetary(compute="_compute_boletas_voucher", string="Boletas 39/41 con voucher",
                                      help="Boletas emitidas y pagadas con un medio que declara ventas en el SII: "
                                           "en modo voucher se anulan con NC")
    pagado_odoo = fields.Monetary(compute="_compute_conciliacion_48", string="Pagado en Odoo")
    con_factura = fields.Monetary(compute="_compute_conciliacion_48", string="Respaldado con factura")
    sin_factura = fields.Monetary(compute="_compute_conciliacion_48", string="Venta sin factura")
    rebajado = fields.Monetary(compute="_compute_conciliacion_48", string="Rebaja aplicada")
    rebaja_pendiente = fields.Monetary(compute="_compute_conciliacion_48")
    iva_en_riesgo = fields.Monetary(compute="_compute_conciliacion_48", string="IVA en riesgo")
    diferencia_sii = fields.Monetary(compute="_compute_conciliacion_48", string="Diferencia con el SII")
    estado_48 = fields.Selection(ESTADOS_48, compute="_compute_estado_48", store=True, string="Conciliación")

    def init(self):
        self.env.cr.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS sii_boleta_diaria_fila_unica "
            "ON sii_boleta_diaria (company_id, fecha, tipo_dte, COALESCE(canal, ''))"
        )

    @api.depends("fecha")
    def _compute_periodo(self):
        for r in self:
            r.periodo = r.fecha.strftime("%Y%m") if r.fecha else False

    @api.depends("primera_total", "total")
    def _compute_rebaja_inferida(self):
        for r in self:
            r.rebaja_inferida = r.primera_total - r.total

    @api.depends("tipo_dte", "fecha", "canal")
    def _compute_display_name(self):
        canales = dict(self._fields["canal"].selection)
        for r in self:
            r.display_name = " ".join(filter(None, [str(r.tipo_dte), r.fecha and r.fecha.strftime("%d/%m/%Y"),
                                                    canales.get(r.canal)]))

    def _facturas(self):
        res = {}
        for pago in self.pago_ids.filtered(lambda p: p.state not in ESTADOS_PAGO_EXCLUIDOS):
            for factura, monto, iva in pago._facturas_48():
                actual = res.setdefault(factura, [0.0, 0.0])
                actual[0] += monto
                actual[1] += iva
        return res

    @api.depends("pago_ids.amount", "pago_ids.state", "rebaja_ids.estado", "rebaja_ids.monto", "total")
    def _compute_conciliacion_48(self):
        for r in self:
            pagos = r.pago_ids.filtered(lambda p: p.state not in ESTADOS_PAGO_EXCLUIDOS)
            facturas = r._facturas()
            aplicadas = r.rebaja_ids.filtered(lambda b: b.estado in ("aplicada", "verificada", "sin_reflejo"))
            r.pagado_odoo = sum(pagos.mapped("amount"))
            r.con_factura = sum(m for m, _i in facturas.values())
            r.sin_factura = r.pagado_odoo - r.con_factura
            r.rebajado = sum(aplicadas.mapped("monto"))
            r.rebaja_pendiente = max(r.con_factura - r.rebajado, 0)
            iva_facturas = sum(i for _m, i in facturas.values())
            r.iva_en_riesgo = round(iva_facturas * r.rebaja_pendiente / r.con_factura) if r.con_factura else 0
            r.diferencia_sii = r.total - (r.pagado_odoo - r.rebajado)

    @api.depends("pago_ids.amount", "pago_ids.state", "rebaja_ids.estado", "rebaja_ids.monto", "total", "tipo_dte")
    def _compute_estado_48(self):
        for r in self:
            if r.tipo_dte != 48:
                r.estado_48 = False
            elif not r.pago_ids.filtered(lambda p: p.state not in ESTADOS_PAGO_EXCLUIDOS):
                r.estado_48 = "sin_pagos" if abs(r.total) > 1 else "conciliado"
            elif r.rebaja_pendiente > 1:
                r.estado_48 = "rebaja_pendiente"
            elif abs(r.diferencia_sii) > 1:
                r.estado_48 = "diferencia"
            else:
                r.estado_48 = "conciliado"

    @api.depends("fecha", "canal", "company_id")
    def _compute_boletas_voucher(self):
        Voucher = self.env["account.move.boleta_voucher"]
        self.boletas_voucher = 0
        for (company, periodo), filas in self.filtered(lambda f: f.tipo_dte == 48 and f.fecha).grouped(
                lambda f: (f.company_id, f.periodo)).items():
            desde = min(filas.mapped("fecha")).replace(day=1)
            hasta = (desde + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            pagadas = Voucher._boletas_pagadas_con_48(company, desde, hasta, excluir_cubiertas=False)
            for fila in filas:
                fila.boletas_voucher = sum(p["monto"] for p in pagadas if p["fecha"] == fila.fecha
                                           and (not fila.canal or p["canal"] == fila.canal))

    @api.model
    def _guardar(self, company, datos, tipos):
        desde, hasta = datos["desde"], datos["hasta"]
        existentes = {(r.fecha, r.tipo_dte, r.canal or False): r for r in self.search([
            ("company_id", "=", company.id), ("tipo_dte", "in", list(tipos)),
            ("fecha", ">=", desde), ("fecha", "<=", hasta),
        ])}
        ahora = fields.Datetime.now()
        vistas = self.browse()
        nuevas = []
        for fila in datos["filas"]:
            valores = {k: v or 0 if k in ("neto", "exento", "iva", "total", "iva_terceros", "operaciones_exentas")
                       else v for k, v in fila.items()}
            valores.update(informado=True, consultado=ahora)
            registro = existentes.get((fila["fecha"], fila["tipo_dte"], fila["canal"] or False))
            if registro:
                cambios = {k: v for k, v in valores.items() if k != "consultado" and registro[k] != v}
                registro.write(dict(cambios, consultado=ahora) if cambios else {"consultado": ahora})
                vistas |= registro
            else:
                nuevas.append(dict(valores, company_id=company.id, primera_total=valores["total"],
                                   primera_iva=valores["iva"]))
        vistas |= self.create(nuevas)
        (self.browse([r.id for r in existentes.values()]) - vistas).filtered("informado").write({"informado": False})
        vistas.filtered(lambda r: r.tipo_dte == 48).rebaja_ids._verificar()
        return vistas

    @api.model
    def _fecha_pago(self, pago):
        tx = pago.payment_transaction_id if "payment_transaction_id" in pago._fields else False
        momento = tx and (tx.sudo().last_state_change or tx.sudo().create_date)
        if not momento:
            return pago.date
        zona = pytz.timezone(pago.company_id.partner_id.tz or self.env.user.tz or "America/Santiago")
        return pytz.utc.localize(momento).astimezone(zona).date()

    @api.model
    def _conciliar_48(self, company, desde, hasta):
        Pago = self.env["account.payment"]
        pagos = Pago.search([
            ("company_id", "=", company.id),
            ("journal_id", "in", company.medios_de_pago_electronico.ids),
            ("payment_type", "=", "inbound"),
            ("state", "not in", ESTADOS_PAGO_EXCLUIDOS),
            ("boleta_48_manual", "=", False),
            ("date", ">=", desde - timedelta(days=TOLERANCIA_DIAS_48 + 7)),
            ("date", "<=", hasta + timedelta(days=TOLERANCIA_DIAS_48)),
        ])
        filas = self.search([
            ("company_id", "=", company.id), ("tipo_dte", "=", 48),
            ("fecha", ">=", desde - timedelta(days=TOLERANCIA_DIAS_48)),
            ("fecha", "<=", hasta + timedelta(days=TOLERANCIA_DIAS_48)),
        ])
        por_clave = {(f.fecha, f.canal or False): f for f in filas}
        tocadas = filas.browse()
        for pago in pagos:
            fecha = self._fecha_pago(pago)
            if not desde <= fecha <= hasta:
                continue
            canal = pago.journal_id.sii_canal_48 or False
            fila = self.browse()
            for dias in (0, -1, 1)[:1 + 2 * TOLERANCIA_DIAS_48]:
                dia = fecha + timedelta(days=dias)
                fila = por_clave.get((dia, canal)) or por_clave.get((dia, False)) or self.browse()
                if fila:
                    break
            if fila != pago.boleta_48_id:
                tocadas |= fila | pago.boleta_48_id
                pago.boleta_48_id = fila
        (filas | tocadas).rebaja_ids._verificar()
        return pagos

    def action_proponer_rebajas(self):
        Rebaja = self.env["sii.boleta.rebaja"]
        for r in self.filtered(lambda f: f.tipo_dte == 48):
            facturas = r._facturas()
            ya = r.rebaja_ids.filtered(lambda b: b.estado != "descartada")
            cubiertas = ya.filtered(lambda b: b.estado != "por_aplicar").factura_ids
            pendientes = {f: v for f, v in facturas.items() if f not in cubiertas}
            propuesta = ya.filtered(lambda b: b.estado == "por_aplicar")
            if not pendientes:
                propuesta.unlink()
                continue
            valores = {
                "factura_ids": [(6, 0, [f.id for f in pendientes])],
                "monto": sum(m for m, _i in pendientes.values()),
                "iva": sum(i for _m, i in pendientes.values()),
            }
            if propuesta:
                propuesta[:1].write(valores)
                propuesta[1:].unlink()
            else:
                Rebaja.create(dict(valores, fila_id=r.id))

    def action_vincular_pagos(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Vincular pagos a %s") % self.display_name,
            "res_model": "sii.boleta.vincular",
            "view_mode": "form",
            "target": "new",
            "context": {"default_fila_id": self.id},
        }


class SiiBoletaRebaja(models.Model):
    _name = "sii.boleta.rebaja"
    _description = "Rebaja del comprobante 48 por facturas pagadas con medios electrónicos"
    _order = "id desc"

    fila_id = fields.Many2one("sii.boleta.diaria", required=True, ondelete="cascade", index=True,
                              string="Comprobante 48 del SII")
    company_id = fields.Many2one(related="fila_id.company_id", store=True)
    currency_id = fields.Many2one(related="fila_id.currency_id")
    fecha = fields.Date(related="fila_id.fecha", store=True)
    canal = fields.Selection(related="fila_id.canal", store=True)
    factura_ids = fields.Many2many("account.move", string="Facturas", readonly=True)
    monto = fields.Monetary(readonly=True)
    iva = fields.Monetary(string="IVA", readonly=True)
    estado = fields.Selection(ESTADOS_REBAJA, default="por_aplicar", required=True, readonly=True)
    total_sii_al_aplicar = fields.Monetary(readonly=True)
    aplicada_fecha = fields.Datetime(readonly=True)
    aplicada_uid = fields.Many2one("res.users", string="Aplicada por", readonly=True)

    def action_marcar_aplicada(self):
        for r in self.filtered(lambda b: b.estado == "por_aplicar"):
            r.write({
                "estado": "aplicada",
                "total_sii_al_aplicar": r.fila_id.total,
                "aplicada_fecha": fields.Datetime.now(),
                "aplicada_uid": self.env.uid,
            })
        self._verificar()

    def action_descartar(self):
        self.filtered(lambda b: b.estado in ("por_aplicar", "aplicada", "sin_reflejo")).estado = "descartada"

    def _verificar(self):
        for fila in self.filtered(lambda b: b.estado in ("aplicada", "verificada", "sin_reflejo")).fila_id:
            aplicadas = fila.rebaja_ids.filtered(lambda b: b.estado in ("aplicada", "verificada", "sin_reflejo"))
            esperado = sum(aplicadas.mapped("monto"))
            for b in aplicadas:
                reflejada = abs(fila.rebaja_inferida - esperado) <= 1 or (
                    b.total_sii_al_aplicar and abs(b.total_sii_al_aplicar - fila.total - b.monto) <= 1)
                if reflejada:
                    b.estado = "verificada"
                elif fila.consultado and b.aplicada_fecha and fila.consultado > b.aplicada_fecha:
                    b.estado = "sin_reflejo"
                else:
                    b.estado = "aplicada"


class AccountPayment48(models.Model):
    _inherit = "account.payment"

    sii_declara_ventas = fields.Boolean(related="journal_id.sii_declara_ventas")
    sii_canal_48 = fields.Selection(related="journal_id.sii_canal_48", string="Canal en el SII")
    boleta_48_id = fields.Many2one(
        "sii.boleta.diaria", string="Comprobante 48 del SII", index="btree_not_null", copy=False,
        domain="[('company_id', '=', company_id), ('tipo_dte', '=', 48)]",
    )
    boleta_48_manual = fields.Boolean(string="Vínculo 48 manual", copy=False,
                                      help="El vínculo lo fijó un usuario: la conciliación automática no lo cambia")
    fecha_48 = fields.Date(compute="_compute_fecha_48", string="Fecha para el 48",
                           help="Fecha de la transacción de la pasarela; si no hay, la del pago")

    @api.depends("date")
    def _compute_fecha_48(self):
        Fila = self.env["sii.boleta.diaria"]
        for r in self:
            r.fecha_48 = Fila._fecha_pago(r) if r.date else False

    @api.onchange("boleta_48_id")
    def _onchange_boleta_48_id(self):
        self.boleta_48_manual = True

    def _facturas_48(self):
        return self._documentos_pagados_48((33, 34))

    def _documentos_pagados_48(self, codigos):
        self.ensure_one()
        res = []
        partials = self.move_id.line_ids.matched_debit_ids
        if partials:
            for partial in partials:
                factura = partial.debit_move_id.move_id
                if factura.move_type == "out_invoice" and factura.document_class_id.sii_code in codigos:
                    res.append((factura, partial.amount, factura._iva_proporcional_48(partial.amount)))
            return res
        facturas = self.reconciled_invoice_ids.filtered(
            lambda f: f.move_type == "out_invoice" and f.document_class_id.sii_code in codigos)
        restante = self.amount
        for factura in facturas:
            monto = min(restante, factura.amount_total)
            restante -= monto
            if monto:
                res.append((factura, monto, factura._iva_proporcional_48(monto)))
        return res

    def action_desvincular_48(self):
        self.write({"boleta_48_id": False, "boleta_48_manual": True})

    def action_conciliar_48_auto(self):
        self.write({"boleta_48_manual": False})
        for company in self.company_id:
            pagos = self.filtered(lambda p: p.company_id == company)
            self.env["sii.boleta.diaria"]._conciliar_48(company, min(pagos.mapped("date")) - timedelta(days=1),
                                                         max(pagos.mapped("date")) + timedelta(days=1))


class AccountMove48(models.Model):
    _inherit = "account.move"

    def _iva_proporcional_48(self, monto):
        self.ensure_one()
        return round(monto * abs(self.amount_tax) / abs(self.amount_total)) if self.amount_total else 0


class SiiBoletaVincular(models.TransientModel):
    _name = "sii.boleta.vincular"
    _description = "Vincular pagos a un comprobante 48 del SII"

    fila_id = fields.Many2one("sii.boleta.diaria", required=True)
    company_id = fields.Many2one(related="fila_id.company_id")
    candidato_ids = fields.Many2many("account.payment", compute="_compute_candidato_ids")
    pago_ids = fields.Many2many("account.payment", string="Pagos", domain="[('id', 'in', candidato_ids)]")

    @api.depends("fila_id")
    def _compute_candidato_ids(self):
        for r in self:
            fila = r.fila_id
            r.candidato_ids = self.env["account.payment"].search([
                ("company_id", "=", fila.company_id.id),
                ("journal_id", "in", fila.company_id.medios_de_pago_electronico.ids),
                ("payment_type", "=", "inbound"),
                ("state", "not in", ESTADOS_PAGO_EXCLUIDOS),
                ("date", ">=", fila.fecha - timedelta(days=3)),
                ("date", "<=", fila.fecha + timedelta(days=3)),
            ]).filtered(lambda p: not fila.canal or p.journal_id.sii_canal_48 == fila.canal) if fila.fecha else False

    def action_confirmar(self):
        if not self.pago_ids:
            raise UserError(_("Seleccione al menos un pago"))
        self.pago_ids.write({"boleta_48_id": self.fila_id.id, "boleta_48_manual": True})


class BoletaVoucher48(models.Model):
    _inherit = "account.move.boleta_voucher"

    @api.model
    def _documentos_anulados_nc(self, company):
        anulados = set()
        for modelo in ("account.move.referencias", "pos.order.referencias"):
            if modelo not in self.env:
                continue
            padre = "move_id" if modelo == "account.move.referencias" else "order_id"
            for ref in self.env[modelo].sudo().search([
                ("ref_id.sii_code", "in", [39, 41]), ("cod_ref", "in", ["1", "3"]),
                ("%s.company_id" % padre, "=", company.id),
            ]):
                if str(ref.folio or "").isdigit():
                    anulados.add((ref.ref_id.id, int(ref.folio)))
        return anulados

    @api.model
    def _documentos_cubiertos(self, company, excepto=None):
        vouchers = self.search([("company_id", "=", company.id), ("sii_result", "not in", ["Rechazado", "Anulado"])])
        if excepto:
            vouchers -= excepto
        cubiertos = set(vouchers.move_ids)
        if "order_ids" in self._fields:
            cubiertos |= set(vouchers.order_ids)
        return cubiertos

    @api.model
    def _boletas_pagadas_con_48(self, company, desde, hasta, excluir_cubiertas=True, excepto=None):
        Fila = self.env["sii.boleta.diaria"]
        medios = company.medios_de_pago_electronico
        if not medios:
            return []
        res = []
        pagos = self.env["account.payment"].search([
            ("company_id", "=", company.id),
            ("journal_id", "in", medios.ids),
            ("payment_type", "=", "inbound"),
            ("state", "not in", ESTADOS_PAGO_EXCLUIDOS),
            ("date", ">=", desde - timedelta(days=TOLERANCIA_DIAS_48 + 7)),
            ("date", "<=", hasta + timedelta(days=TOLERANCIA_DIAS_48)),
        ])
        for pago in pagos:
            fecha = Fila._fecha_pago(pago)
            if not desde <= fecha <= hasta:
                continue
            for boleta, monto, iva in pago._documentos_pagados_48((39, 41)):
                res.append({"documento": boleta, "pago": pago, "fecha": fecha, "canal": pago.journal_id.sii_canal_48,
                            "monto": monto, "iva": iva})
        res += self._boletas_pos_pagadas_con_48(company, desde, hasta, medios)
        anulados = self._documentos_anulados_nc(company)
        cubiertos = self._documentos_cubiertos(company, excepto) if excluir_cubiertas else set()
        return [p for p in res if (p["documento"].document_class_id.id, int(p["documento"].sii_document_number or 0))
                not in anulados and p["documento"] not in cubiertos]

    @api.model
    def _boletas_pos_pagadas_con_48(self, company, desde, hasta, medios):
        if "pos.payment" not in self.env or "document_class_id" not in self.env["pos.order"]._fields:
            return []
        zona = pytz.timezone(company.partner_id.tz or self.env.user.tz or "America/Santiago")
        inicio = zona.localize(datetime.combine(desde, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        fin = zona.localize(datetime.combine(hasta + timedelta(days=1), time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        res = []
        for pago in self.env["pos.payment"].sudo().search([
            ("company_id", "=", company.id),
            ("payment_method_id.journal_id", "in", medios.ids),
            ("payment_date", ">=", inicio), ("payment_date", "<", fin),
            ("pos_order_id.document_class_id.sii_code", "in", [39, 41]),
            ("pos_order_id.sii_document_number", "not in", [False, 0, "0"]),
        ]):
            orden = pago.pos_order_id
            res.append({
                "documento": orden, "pago": pago,
                "fecha": pytz.utc.localize(pago.payment_date).astimezone(zona).date(),
                "canal": pago.payment_method_id.journal_id.sii_canal_48,
                "monto": pago.amount,
                "iva": round(pago.amount * abs(orden.amount_tax) / abs(orden.amount_total)) if orden.amount_total else 0,
            })
        return res
