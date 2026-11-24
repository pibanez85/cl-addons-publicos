from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .sii_boleta_diaria import ESTADOS_PAGO_EXCLUIDOS
from .sii_revision import _fecha, _formato

MONTOS_BOLETA_DIARIA = ("neto", "exento", "iva", "total", "iva_terceros", "operaciones_exentas")
CANALES_SII = ("presencial", "internet")
TIPOS_BOLETA_DIARIA = (35, 38, 39, 41, 47, 48)


def _monto(valor):
    try:
        return float(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


class SiiRevisionPeriodoBoletas(models.Model):
    _inherit = "sii.revision.periodo"

    def _tipos_con_boletas(self, resumen_tipos):
        return sorted({int(f.get("tipo_dte") or 0) for f in resumen_tipos
                       if int(f.get("tipo_dte") or 0) in TIPOS_BOLETA_DIARIA and int(f.get("cantidad") or 0) > 0})

    def _boletas_diarias_sii(self, tipo):
        data = self._trafquin("/api/rcv/boletas_diarias", {"tipo_doc": tipo}).get("data") or {}
        totales = data.get("totales") or {}
        dias = []
        for d in data.get("dias") or []:
            fecha = _fecha(d.get("fecha"))
            if not fecha:
                continue
            fila = {
                "fecha": fecha,
                "tipo_dte": tipo,
                "canal": d.get("canal") if d.get("canal") in CANALES_SII else False,
                "canal_sii": str(d.get("canal_sii")) if d.get("canal_sii") is not None else False,
                "cantidad": int(d.get("cantidad") or 0),
                "modificado": bool(d.get("modificado")),
                "motivo_codigo": str(d.get("motivo_codigo")) if d.get("motivo_codigo") is not None else False,
                "motivo": d.get("motivo") or False,
            }
            fila.update({m: _monto(d.get(m)) for m in MONTOS_BOLETA_DIARIA})
            dias.append(fila)
        return {
            "tipo_dte": tipo,
            "sin_datos": bool(data.get("sin_datos")),
            "tiene_canal": data.get("tiene_canal"),
            "resumen_manual": data.get("resumen_codigo") or False,
            "modificados": int(data.get("modificados") or 0),
            "totales": dict({m: _monto(totales.get(m)) for m in MONTOS_BOLETA_DIARIA},
                            cantidad=int(totales.get("cantidad") or 0)) if totales else False,
            "dias": dias,
            "aviso": _("El SII tiene un resumen mensual ingresado a mano para el tipo %s: el libro usa el resumen "
                       "del RCV") % tipo if data.get("resumen_codigo") and data.get("sin_datos") else False,
        }


class SiiRevisionPeriodoBoletasDiarias(models.Model):
    _inherit = "sii.revision.periodo"

    modo_voucher = fields.Boolean(compute="_compute_modo_voucher")
    boleta_diaria_ids = fields.Many2many("sii.boleta.diaria", compute="_compute_boleta_diaria_ids",
                                         string="Boletas diarias del SII")
    boleta48_ids = fields.Many2many("sii.boleta.diaria", compute="_compute_boleta_diaria_ids",
                                    string="Comprobantes 48 del SII")
    v48_total_sii = fields.Monetary(compute="_compute_v48", string="48 en el SII")
    v48_pagado = fields.Monetary(compute="_compute_v48", string="Pagado en Odoo")
    v48_con_factura = fields.Monetary(compute="_compute_v48", string="Respaldado con factura")
    v48_rebajado = fields.Monetary(compute="_compute_v48", string="Rebaja aplicada")
    v48_pendiente = fields.Monetary(compute="_compute_v48", string="Rebaja pendiente")
    v48_iva_riesgo = fields.Monetary(compute="_compute_v48", string="IVA en riesgo")
    v48_pagos_sin_boleta = fields.Integer(compute="_compute_v48", string="Pagos sin comprobante 48")

    @api.depends("company_id.modelo_emision_boleta")
    def _compute_modo_voucher(self):
        for r in self:
            r.modo_voucher = r.company_id.modelo_emision_boleta == "voucher"

    def _dominio_boletas_diarias(self):
        return [("company_id", "=", self.company_id.id), ("periodo", "=", self.periodo)]

    @api.depends("company_id", "periodo")
    def _compute_boleta_diaria_ids(self):
        Fila = self.env["sii.boleta.diaria"]
        for r in self:
            filas = Fila.search(r._dominio_boletas_diarias()) if r.company_id and r.periodo else Fila
            r.boleta_diaria_ids = filas
            r.boleta48_ids = filas.filtered(lambda f: f.tipo_dte == 48)

    def _pagos_sin_boleta_48(self):
        desde, hasta = self._rango()
        return self.env["account.payment"].search([
            ("company_id", "=", self.company_id.id),
            ("journal_id", "in", self.company_id.medios_de_pago_electronico.ids),
            ("payment_type", "=", "inbound"),
            ("state", "not in", ESTADOS_PAGO_EXCLUIDOS),
            ("boleta_48_id", "=", False),
            ("date", ">=", desde), ("date", "<=", hasta),
        ])

    @api.depends("boleta48_ids")
    def _compute_v48(self):
        for r in self:
            filas = r.boleta48_ids
            r.v48_total_sii = sum(filas.mapped("total"))
            r.v48_pagado = sum(filas.mapped("pagado_odoo"))
            r.v48_con_factura = sum(filas.mapped("con_factura"))
            r.v48_rebajado = sum(filas.mapped("rebajado"))
            r.v48_pendiente = sum(filas.mapped("rebaja_pendiente"))
            r.v48_iva_riesgo = sum(filas.mapped("iva_en_riesgo"))
            r.v48_pagos_sin_boleta = len(r._pagos_sin_boleta_48()) if r.company_id and r.periodo else 0

    def _guardar_boletas_diarias(self, resumen_tipos):
        avisos = []
        filas = []
        consultados = []
        for tipo in self._tipos_con_boletas(resumen_tipos):
            try:
                with self.env.cr.savepoint():
                    datos = self._boletas_diarias_sii(tipo)
            except UserError as e:
                avisos.append(_("Boletas diarias tipo %s: %s") % (tipo, e.args[0] if e.args else e))
                continue
            consultados.append(tipo)
            filas += datos["dias"]
            if datos["aviso"]:
                avisos.append(datos["aviso"])
        desde, hasta = self._rango()
        if consultados:
            self.env["sii.boleta.diaria"]._guardar(self.company_id, {"desde": desde, "hasta": hasta, "filas": filas},
                                                   consultados)
            if 48 in consultados and self.modo_voucher:
                self.env["sii.boleta.diaria"]._conciliar_48(self.company_id, desde, hasta)
        return avisos

    def _boletas_diarias_por_dia(self):
        res = {}
        for fila in self.env["sii.boleta.diaria"].search(
                self._dominio_boletas_diarias() + [("tipo_dte", "!=", 48), ("informado", "=", True)]):
            valores = res.setdefault((fila.fecha, fila.tipo_dte), {"cantidad_sii": 0, "exento": 0.0, "neto": 0.0,
                                                                   "iva": 0.0, "total": 0.0})
            valores["cantidad_sii"] += fila.cantidad
            valores["exento"] += fila.exento
            valores["neto"] += fila.neto
            valores["iva"] += fila.iva
            valores["total"] += fila.total
        return {k: v for k, v in res.items() if v["cantidad_sii"] or v["total"]}

    def action_conciliar_48(self):
        for r in self:
            desde, hasta = r._rango()
            self.env["sii.boleta.diaria"]._conciliar_48(r.company_id, desde, hasta)

    def action_proponer_rebajas(self):
        self.action_conciliar_48()
        self.boleta48_ids.action_proponer_rebajas()

    def _boletas_con_voucher_pendientes(self):
        if not self.modo_voucher:
            return []
        desde, hasta = self._rango()
        return self.env["account.move.boleta_voucher"]._boletas_pagadas_con_48(self.company_id, desde, hasta)

    def _secuencia_nc_voucher(self):
        Clase = self.env["account.journal.sii_document_class"]
        secuencia = Clase.search([("sii_document_class_id.sii_code", "=", 61),
                                  ("journal_id.company_id", "=", self.company_id.id),
                                  ("journal_id.type", "=", "sale")], limit=1).sequence_id
        return secuencia or self.env["ir.sequence"].search([
            ("sii_document_class_id.sii_code", "=", 61), ("company_id", "in", [self.company_id.id, False])], limit=1)

    def action_nc_boletas_voucher(self):
        self.ensure_one()
        pendientes = self._boletas_con_voucher_pendientes()
        Voucher = self.env["account.move.boleta_voucher"].with_company(self.company_id)
        periodo = self._periodo_libro()
        voucher = Voucher.search([("company_id", "=", self.company_id.id), ("periodo", "=", periodo),
                                  ("sii_result", "in", [False, "", "draft", "NoEnviado"])], limit=1)
        if pendientes:
            if not voucher:
                secuencia = self._secuencia_nc_voucher()
                if not secuencia:
                    raise UserError(_("No hay una secuencia de notas de crédito (61) para %s: configure el CAF de NC "
                                      "en un diario de ventas") % self.company_id.name)
                voucher = Voucher.create({
                    "name": _("NC boletas con voucher %s") % periodo,
                    "company_id": self.company_id.id,
                    "periodo": periodo,
                    "sequence_id": secuencia.id,
                    "folio_anular": 0,
                    "fecha_emision": self._fin_periodo(),
                })
            documentos = {p["documento"] for p in pendientes}
            valores = {"move_ids": [(4, d.id) for d in documentos if d._name == "account.move"]}
            if "order_ids" in voucher._fields:
                valores["order_ids"] = [(4, d.id) for d in documentos if d._name == "pos.order"]
            voucher.write(valores)
            voucher.set_totales()
        if not voucher:
            raise UserError(_("No hay boletas pagadas con medios electrónicos pendientes de NC en %s") % self.periodo)
        return {
            "type": "ir.actions.act_window",
            "res_model": voucher._name,
            "res_id": voucher.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_ver_pagos_sin_boleta_48(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Pagos electrónicos sin comprobante 48"),
            "res_model": "account.payment",
            "view_mode": "list,form",
            "domain": [("id", "in", self._pagos_sin_boleta_48().ids)],
        }

    def _observaciones(self):
        res = super()._observaciones()
        diferencias = self.boleta_linea_ids.filtered(
            lambda b: b.tipo_dte != 48 and b.estado in ("diferencia", "falta_odoo", "falta_sii"))
        res.append(("BOL", "sin_info" if not self.boletas_fecha else ("alerta" if diferencias else "ok"),
                    _("Boletas diarias del SII contra los consumos de folios de Odoo: %s días con diferencia")
                    % len(diferencias), sum(diferencias.mapped("total")), sum(diferencias.mapped("odoo_total"))))
        if self.modo_voucher:
            pendientes = self._boletas_con_voucher_pendientes() if self.company_id and self.periodo else []
            monto = sum(p["monto"] for p in pendientes)
            por_dia = {}
            for p in pendientes:
                por_dia[(p["fecha"], p["canal"])] = por_dia.get((p["fecha"], p["canal"]), 0) + p["monto"]
            detalle = ", ".join("%s %s %s" % (f.strftime("%d/%m"), c or "", _formato(m)) for (f, c), m in sorted(
                por_dia.items(), key=lambda x: (x[0][0], x[0][1] or "")))
            res.append(("V39", "alerta" if monto > 1 else "ok",
                        _("Boletas emitidas pagadas con medios electrónicos en modo voucher: emitir NC por %s "
                          "(%s boletas)%s") % (_formato(monto), len({p["documento"] for p in pendientes}),
                                               ": %s" % detalle if detalle else ""),
                        monto, 0))
            sin_reflejo = self.boleta48_ids.rebaja_ids.filtered(lambda b: b.estado == "sin_reflejo")
            riesgo = self.v48_iva_riesgo + sum(sin_reflejo.mapped("iva"))
            res.append(("V48", "sin_info" if not self.boleta48_ids else ("alerta" if riesgo > 1 else "ok"),
                        _("Facturas pagadas con medios electrónicos no rebajadas del 48: IVA en riesgo de pagarse dos "
                          "veces (%s rebajas aplicadas sin reflejo en el SII)") % len(sin_reflejo),
                        riesgo, self.v48_con_factura))
        return res
