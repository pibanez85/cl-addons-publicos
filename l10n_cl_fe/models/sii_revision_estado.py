from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

ESTADOS_PERIODO = [
    ("fuera_actividades", "Antes del inicio de actividades"),
    ("sin_consultar", "Sin consultar"),
    ("error", "Error"),
    ("descuadrado", "Descuadrado"),
    ("por_declarar", "Sin declarar, vencido"),
    ("en_plazo", "Por declarar"),
    ("fuera_plazo", "Declarado fuera de plazo"),
    ("declarado", "Declarado, sin pago en Odoo"),
    ("pagado", "Pagado"),
]


def _vencimiento(periodo):
    anio, mes = int(periodo[:4]), int(periodo[4:])
    anio, mes = (anio + 1, 1) if mes == 12 else (anio, mes + 1)
    vence = date(anio, mes, 20)
    while vence.weekday() >= 5:
        vence += timedelta(days=1)
    return vence


class SiiRevisionPeriodoEstado(models.Model):
    _inherit = "sii.revision.periodo"

    f29_debito = fields.Monetary(compute="_compute_totales_f29", store=True, string="Débito (538)")
    f29_credito = fields.Monetary(compute="_compute_totales_f29", store=True, string="Crédito (537)")
    impuesto_ventas = fields.Monetary(compute="_compute_totales_f29", store=True, string="Impuesto ventas",
                                      help="Débito fiscal del mes (código 538), declarado o de la propuesta del SII")
    impuesto_compras = fields.Monetary(
        compute="_compute_totales_f29", store=True, string="Impuesto compras",
        help="Crédito fiscal del mes: código 537 menos el remanente del mes anterior (504), declarado o de la "
             "propuesta del SII. Es el IVA de las compras del período, sin arrastrar el remanente")
    f29_iva_pagar = fields.Monetary(compute="_compute_totales_f29", store=True, string="IVA a pagar (89)")
    f29_remanente = fields.Monetary(compute="_compute_totales_f29", store=True, string="Remanente (77)")
    f29_ppm = fields.Monetary(compute="_compute_totales_f29", store=True, string="PPM (62)")
    total_a_pagar = fields.Monetary(
        compute="_compute_totales_f29", store=True, string="Total a pagar",
        help="Declaración Vigente con detalle: total pagado (94 si hay recargo, si no 91). "
             "Sin ella: total determinado de la propuesta del SII (547, o 89 + 62 + retenciones)")
    recargo_f29 = fields.Monetary(compute="_compute_totales_f29", store=True, string="Recargo (92 + 93 − 795)")
    f29_debito_odoo = fields.Monetary(compute="_compute_totales_f29", store=True, string="Débito Odoo (538)")
    f29_credito_odoo = fields.Monetary(compute="_compute_totales_f29", store=True, string="Crédito Odoo (537)")
    f29_iva_pagar_odoo = fields.Monetary(compute="_compute_totales_f29", store=True, string="IVA a pagar Odoo (89)")
    declaracion_estado = fields.Char(compute="_compute_declaracion", store=True, string="Declaración")
    declaracion_fecha = fields.Date(compute="_compute_declaracion", store=True, string="Fecha de declaración")
    declaracion_total = fields.Monetary(compute="_compute_declaracion", store=True, string="Total declarado")
    vence = fields.Date(compute="_compute_vence", store=True, string="Vencimiento")
    anio = fields.Char(compute="_compute_vence", store=True, string="Año")
    rcv_estado = fields.Selection([
        ("sin_consultar", "Sin consultar"),
        ("error", "Error"),
        ("incompleto", "Incompleto"),
        ("diferencias", "Con diferencias"),
        ("conciliado", "Conciliado"),
    ], compute="_compute_rcv_estado", store=True, string="Conciliación RCV")
    contable_estado = fields.Selection([
        ("sin_asiento", "Sin asiento"),
        ("borrador", "Borrador"),
        ("publicado", "Publicado"),
        ("pagado", "Pagado"),
    ], compute="_compute_contable_estado", store=True, string="Contabilidad")
    estado = fields.Selection(ESTADOS_PERIODO, compute="_compute_estado", store=True, index=True)

    @api.depends("f29_linea_ids.codigo", "f29_linea_ids.valor_sii", "f29_linea_ids.valor_odoo",
                 "f29_linea_ids.calculado_odoo", "f29_declaracion_ids.codigo_ids.valor",
                 "f29_declaracion_ids.estado", "f29_declaracion_ids.folio", "f29_declaracion_ids.total_pagado",
                 "f29_declaracion_ids.pago_sin_detalle", "f29_declaracion_ids.detalle_fecha",
                 "f29_declaracion_ids.ipc", "f29_declaracion_ids.intereses_multas",
                 "f29_declaracion_ids.condonacion")
    def _compute_totales_f29(self):
        for r in self:
            vigente = r._vigente()
            declarado = bool(vigente.codigo_ids)
            v = vigente._valores() if declarado else {l.codigo: l.valor_sii for l in r.f29_linea_ids}
            odoo = {l.codigo: l.valor_odoo for l in r.f29_linea_ids if l.calculado_odoo}
            r.f29_debito = r.impuesto_ventas = v.get(538, 0)
            r.f29_credito = v.get(537, 0)
            r.impuesto_compras = v.get(537, 0) - v.get(504, 0)
            r.f29_iva_pagar = v.get(89, 0)
            r.f29_remanente = v.get(77, 0)
            r.f29_ppm = v.get(62, 0)
            r.f29_debito_odoo = odoo.get(538, 0)
            r.f29_credito_odoo = odoo.get(537, 0)
            r.f29_iva_pagar_odoo = odoo.get(89, 0)
            r.recargo_f29 = r._recargo_f29()
            if declarado:
                r.total_a_pagar = vigente.total_pagado or v.get(94) or v.get(91, 0)
            else:
                r.total_a_pagar = v.get(547) or sum(v.get(c, 0) for c in (89, 62, 151, 48))

    def _vigente(self):
        return self.f29_declaracion_ids.filtered(lambda d: d.folio and "vigente" in (d.estado or "").lower())[:1]

    @api.depends("f29_declaracion_ids.estado", "f29_declaracion_ids.folio", "f29_declaracion_ids.fecha",
                 "f29_declaracion_ids.total")
    def _compute_declaracion(self):
        for r in self:
            declaracion = r._vigente() or r.f29_declaracion_ids[:1]
            r.declaracion_estado = declaracion.estado
            r.declaracion_fecha = declaracion.fecha
            r.declaracion_total = declaracion.total

    @api.depends("periodo")
    def _compute_vence(self):
        for r in self:
            valido = r.periodo and r.periodo.isdigit() and len(r.periodo) == 6
            r.vence = _vencimiento(r.periodo) if valido else False
            r.anio = r.periodo[:4] if valido else False

    @api.depends("compras_fecha", "ventas_fecha", "compras_error", "ventas_error", "compras_completo",
                 "ventas_completo", "compras_linea_ids.estado", "ventas_linea_ids.estado")
    def _compute_rcv_estado(self):
        for r in self:
            if not (r.compras_fecha or r.ventas_fecha):
                r.rcv_estado = "sin_consultar"
            elif r.compras_error or r.ventas_error:
                r.rcv_estado = "error"
            elif not (r.compras_completo and r.ventas_completo):
                r.rcv_estado = "incompleto"
            elif (r.compras_linea_ids | r.ventas_linea_ids).filtered(
                    lambda l: l.estado in ("falta_odoo", "falta_sii", "diferencia")):
                r.rcv_estado = "diferencias"
            else:
                r.rcv_estado = "conciliado"

    @api.depends("move_f29_id.state", "move_f29_id.line_ids.reconciled")
    def _compute_contable_estado(self):
        for r in self:
            if not r.move_f29_id or r.move_f29_id.state == "cancel":
                r.contable_estado = "sin_asiento"
            elif r.move_f29_id.state == "draft":
                r.contable_estado = "borrador"
            else:
                r.contable_estado = "pagado" if r.f29_pagado else "publicado"

    @api.model
    def _inicio_actividades(self, company):
        if company.start_date:
            return company.start_date
        primero = self.sudo().search([("company_id", "=", company.id), ("f29_declaracion_ids.folio", "!=", False)],
                                     order="periodo", limit=1)
        return date(int(primero.periodo[:4]), int(primero.periodo[4:]), 1) if primero else False

    def _antes_de_actividades(self):
        inicio = self._inicio_actividades(self.company_id)
        return bool(inicio and self.periodo and self.periodo < inicio.strftime("%Y%m"))

    @api.constrains("periodo", "company_id")
    def _check_inicio_actividades(self):
        for r in self.filtered(lambda r: r._antes_de_actividades()):
            raise ValidationError(_(
                "El período %s es anterior al inicio de actividades de %s (%s): no lleva Declaración Mensual F29"
            ) % (r.periodo, r.company_id.name, r._inicio_actividades(r.company_id).strftime("%m/%Y")))

    @api.depends("f29_fecha", "f29_error", "rcv_estado", "f29_descuadres", "vence", "contable_estado",
                 "f29_declaracion_ids.estado", "f29_declaracion_ids.folio", "f29_declaracion_ids.fecha",
                 "company_id.partner_id.start_date")
    def _compute_estado(self):
        hoy = fields.Date.context_today(self)
        for r in self:
            vigente = r._vigente()
            if r._antes_de_actividades():
                r.estado = "fuera_actividades"
            elif not r.f29_fecha:
                r.estado = "sin_consultar"
            elif r.f29_error or r.rcv_estado == "error":
                r.estado = "error"
            elif vigente and r.contable_estado == "pagado":
                r.estado = "pagado"
            elif vigente and r.vence and vigente.fecha and vigente.fecha > r.vence:
                r.estado = "fuera_plazo"
            elif vigente:
                r.estado = "declarado"
            elif r.f29_descuadres:
                r.estado = "descuadrado"
            else:
                r.estado = "por_declarar" if r.vence and hoy > r.vence else "en_plazo"

    @api.model
    def _actualizar_vencidos(self):
        vencidos = self.search([("estado", "in", ("en_plazo", "descuadrado")),
                                ("vence", "<", fields.Date.context_today(self))])
        self.env.add_to_compute(self._fields["estado"], vencidos)
        vencidos.flush_recordset(["estado"])
