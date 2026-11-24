import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .sii_revision import _fecha, _formato

CODIGOS_PAGO_F29 = (91, 92, 93, 94, 795, 547, 931, 932, 60, 922, 915)


def _numero(valor):
    try:
        return float(valor) if valor is not None and valor != "" else None
    except (TypeError, ValueError):
        return None


class SiiRevisionF29DeclaracionDetalle(models.Model):
    _inherit = "sii.revision.f29.declaracion"

    currency_id = fields.Many2one(related="revision_id.currency_id")
    detalle_fecha = fields.Datetime(string="Detalle traído", readonly=True)
    sin_detalle = fields.Boolean(readonly=True, help="El SII o trafquin no entregó los códigos de esta declaración")
    pago_sin_detalle = fields.Boolean(string="Sin detalle de pago", readonly=True)
    codigo_ids = fields.One2many("sii.revision.f29.declaracion.codigo", "declaracion_id", string="Códigos declarados")
    total_plazo_legal = fields.Monetary(string="Total a pagar en plazo legal (91)", readonly=True)
    ipc = fields.Monetary(string="Reajuste IPC (92)", readonly=True)
    intereses_multas = fields.Monetary(string="Intereses y multas (93)", readonly=True)
    condonacion = fields.Monetary(string="Condonación (795)", readonly=True)
    total_con_recargo = fields.Monetary(string="Total con recargo (94)", readonly=True)
    total_pagado = fields.Monetary(string="Total declarado a pagar", readonly=True,
                                   help="Código 94 si viene, si no el 91: lo declarado a pagar, no prueba de pago")
    recargo = fields.Monetary(compute="_compute_recargo", string="Recargo (92 + 93 − 795)")
    tipo_declaracion = fields.Char(readonly=True)
    corrige_folios = fields.Char(string="Corrige a folio", readonly=True)
    fecha_presentacion = fields.Date(readonly=True)
    banco = fields.Char(readonly=True)
    medio_pago = fields.Char(string="Medio de pago", readonly=True)
    pdf_id = fields.Many2one("ir.attachment", string="PDF", readonly=True)

    @api.depends("ipc", "intereses_multas", "condonacion")
    def _compute_recargo(self):
        for r in self:
            r.recargo = r.ipc + r.intereses_multas - r.condonacion

    def _valores(self):
        return {c.codigo: c.valor for c in self.codigo_ids}

    def _guardar_detalle(self, data):
        self.ensure_one()
        codigos = data.get("codigos")
        pago = data.get("pago")
        valores = {
            "estado": data.get("estado") or self.estado,
            "fecha": _fecha(data.get("fecha")) or self.fecha,
            "total": float(data.get("total") or self.total or 0),
            "detalle_fecha": fields.Datetime.now(),
            "sin_detalle": codigos is None,
            "pago_sin_detalle": not pago,
            "total_pagado": float(data.get("total_pagado") or 0),
            "codigo_ids": [(5,)] + [(0, 0, {
                "codigo": int(c.get("codigo") or 0),
                "glosa": c.get("glosa") or "",
                "valor": _numero(c.get("valor")) or 0,
                "valor_texto": str(c.get("valor")) if c.get("valor") is not None else False,
            }) for c in codigos or [] if c.get("codigo")],
        }
        pago = pago or {}
        valores.update({
            "total_plazo_legal": float(pago.get("total_plazo_legal") or 0),
            "ipc": float(pago.get("ipc") or 0),
            "intereses_multas": float(pago.get("intereses_multas") or 0),
            "condonacion": float(pago.get("condonacion") or 0),
            "total_con_recargo": float(pago.get("total_con_recargo") or 0),
            "tipo_declaracion": pago.get("tipo_declaracion") or False,
            "corrige_folios": ", ".join(str(f) for f in pago.get("corrige_folios") or []) or False,
            "fecha_presentacion": _fecha(pago.get("fecha_presentacion")),
            "banco": pago.get("banco") or False,
            "medio_pago": pago.get("medio_pago") or False,
        })
        if data.get("pdf_b64"):
            self.pdf_id.unlink()
            valores["pdf_id"] = self.env["ir.attachment"].create({
                "name": "F29 %s folio %s.pdf" % (self.revision_id.periodo, self.folio),
                "type": "binary",
                "raw": base64.b64decode(data["pdf_b64"]),
                "mimetype": "application/pdf",
                "res_model": "sii.revision.periodo",
                "res_id": self.revision_id.id,
            }).id
        self.write(valores)

    def action_ver_codigos(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("F29 declarado folio %s") % self.folio,
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class SiiRevisionF29DeclaracionCodigo(models.Model):
    _name = "sii.revision.f29.declaracion.codigo"
    _description = "Código del F29 declarado"
    _order = "codigo"

    declaracion_id = fields.Many2one("sii.revision.f29.declaracion", required=True, ondelete="cascade", index=True)
    codigo = fields.Integer(required=True)
    glosa = fields.Char()
    valor = fields.Float()
    valor_texto = fields.Char(string="Valor como lo trae el SII")


class SiiRevisionF29LineaDeclarado(models.Model):
    _inherit = "sii.revision.f29.linea"

    valor_declarado = fields.Float(compute="_compute_valor_declarado", string="Declarado")
    declarado_difiere = fields.Boolean(compute="_compute_valor_declarado")

    @api.depends("revision_id.f29_declaracion_ids.codigo_ids.valor", "valor_sii")
    def _compute_valor_declarado(self):
        for r in self:
            vigente = r.revision_id._vigente()
            declarados = vigente._valores() if vigente.codigo_ids else None
            r.valor_declarado = declarados.get(r.codigo, 0) if declarados is not None else 0
            r.declarado_difiere = declarados is not None and abs(r.valor_declarado - r.valor_sii) > 1


class SiiRevisionPeriodoDeclarado(models.Model):
    _inherit = "sii.revision.periodo"

    declarado_detalle = fields.Boolean(compute="_compute_declarado_aviso")
    declarado_sin_pago = fields.Boolean(compute="_compute_declarado_aviso")
    declarado_aviso = fields.Html(compute="_compute_declarado_aviso", sanitize=False)

    @api.depends("f29_declaracion_ids.codigo_ids.valor", "f29_formulario_ids.valor_sii")
    def _compute_declarado_aviso(self):
        for r in self:
            vigente = r._vigente()
            r.declarado_detalle = bool(vigente.codigo_ids)
            r.declarado_sin_pago = bool(vigente.detalle_fecha and vigente.pago_sin_detalle)
            difieren = r.f29_formulario_ids.filtered("declarado_difiere")
            r.declarado_aviso = _(
                "<strong>Lo declarado difiere de la propuesta del SII</strong> (folio %s): %s"
            ) % (vigente.folio, "; ".join("%s: propuesta %s, declarado %s (%s)" % (
                l.codigo, _formato(l.valor_sii), _formato(l.valor_declarado), _formato(l.valor_declarado - l.valor_sii))
                for l in difieren)) if difieren else False

    def _valores_declarados(self):
        vigente = self._vigente()
        if vigente.codigo_ids:
            return vigente._valores()
        return super()._valores_declarados()

    def action_traer_declarado(self):
        for r in self:
            vigente = r._vigente()
            data = r._trafquin("/api/f29/declaracion", {"folio": int(vigente.folio)} if vigente and (vigente.folio or "").isdigit() else {},
                               clave=True).get("data") or {}
            if "vigente" not in (data.get("estado") or "").lower():
                raise UserError(_("El SII no informa una declaración Vigente del F29 de %s (estado: %s)")
                                % (r.periodo, data.get("estado") or _("sin declaración")))
            folio = str(data.get("folio") or "")
            declaracion = r.f29_declaracion_ids.filtered(lambda d: d.folio == folio)[:1] or \
                self.env["sii.revision.f29.declaracion"].create({"revision_id": r.id, "folio": folio})
            declaracion._guardar_detalle(data)

    def _recargo_f29(self):
        vigente = self._vigente()
        return vigente.recargo if vigente.detalle_fecha and not vigente.pago_sin_detalle else 0

    def _partidas_f29(self, v):
        partidas = super()._partidas_f29(v)
        postergado = v.get(779, 0)
        if not postergado:
            return partidas
        res = []
        for campo, nombre, monto, pagar in partidas:
            if campo == "f29_cuenta_por_pagar_id" and monto < 0 and postergado:
                reduce = min(-monto, postergado)
                monto += reduce
                postergado -= reduce
                res.append(("f29_cuenta_postergado_id", _("IVA postergado (779)"), -reduce, False))
            res.append((campo, nombre, monto, pagar))
        return [p for p in res if round(p[2])]

    def action_registrar_pago_f29(self):
        accion = super().action_registrar_pago_f29()
        recargo = self._recargo_f29()
        if recargo:
            accion["context"] = dict(accion["context"], default_f29_recargo=recargo)
        return accion
