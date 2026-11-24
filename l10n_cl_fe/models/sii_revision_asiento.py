import calendar
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .sii_revision import (
    CREDITOS_F29, CREDITOS_RESTA_F29, DEBITOS_F29, DEBITOS_RESTA_F29, _totales_f29,
)

RUT_TESORERIA = "60.805.000-0"


class SiiRevisionPeriodoAsiento(models.Model):
    _inherit = "sii.revision.periodo"

    move_f29_id = fields.Many2one("account.move", string="Asiento F29", readonly=True, copy=False)
    move_f29_estado = fields.Selection(related="move_f29_id.state", string="Estado del asiento")
    f29_pagado = fields.Boolean(compute="_compute_f29_pagado", string="F29 pagado")
    f29_saldos_aviso = fields.Html(compute="_compute_f29_saldos_aviso", sanitize=False)

    def _fin_periodo(self):
        anio, mes = int(self.periodo[:4]), int(self.periodo[4:])
        return date(anio, mes, calendar.monthrange(anio, mes)[1])

    def _valores_declarados(self):
        ajustadas = self.f29_linea_ids._ajustadas()
        sii = {l.codigo: l.valor_sii for l in self.f29_linea_ids}
        valores = {l.codigo: (l.valor_propuesto if l in ajustadas else l.valor_sii) for l in self.f29_linea_ids}
        ajustados = set(ajustadas.mapped("codigo"))
        calculados = _totales_f29(valores)
        for total, componentes in (
            (538, set(DEBITOS_F29) | set(DEBITOS_RESTA_F29)),
            (537, set(CREDITOS_F29) | set(CREDITOS_RESTA_F29)),
            (62, {563, 115, 68}),
        ):
            if total not in ajustados and ajustados & componentes and total in calculados:
                valores[total] = calculados[total]
        debito, credito = valores.get(538, 0), valores.get(537, 0)
        if (debito, credito) != (sii.get(538, 0), sii.get(537, 0)):
            for codigo, valor in ((89, max(debito - credito, 0)), (77, max(credito - debito, 0))):
                if codigo not in ajustados:
                    valores[codigo] = valor
        return valores

    def _partidas_f29(self, v):
        remanente = v.get(504, 0)
        reajuste = self._reajuste_partida(remanente)
        partidas = [
            ("f29_cuenta_remanente_id", _("Reajuste UTM del remanente (504 − 77 anterior)"), reajuste, False),
            ("f29_cuenta_reajuste_id", _("Reajuste UTM del remanente (504 − 77 anterior)"), -reajuste, False),
            ("f29_cuenta_debito_id", _("IVA Débito Fiscal (538)"), v.get(538, 0), False),
            ("f29_cuenta_credito_id", _("IVA Crédito Fiscal (537 − 504)"), -(v.get(537, 0) - remanente), False),
            ("f29_cuenta_remanente_id", _("Remanente del mes anterior (504)"), -remanente, False),
            ("f29_cuenta_por_pagar_id", _("IVA por pagar (89)"), -v.get(89, 0), True),
            ("f29_cuenta_remanente_id", _("Remanente para el mes siguiente (77)"), v.get(77, 0), False),
        ]
        for codigo, campo, nombre in (
            (62, "f29_cuenta_ppm_id", _("PPM (62)")),
            (151, "f29_cuenta_retencion_id", _("Retención de segunda categoría (151)")),
            (48, "f29_cuenta_impuesto_unico_id", _("Impuesto único (48)")),
        ):
            if v.get(codigo):
                partidas += [(campo, nombre, v[codigo], False), ("f29_cuenta_por_pagar_id", nombre, -v[codigo], True)]
        return [p for p in partidas if round(p[2])]

    def _remanente_77_anterior(self):
        anterior = self._periodo_anterior()
        return anterior._valores_declarados().get(77) if anterior.f29_linea_ids else None

    def _reajuste_partida(self, remanente):
        anterior = self._remanente_77_anterior()
        return remanente - anterior if remanente and anterior is not None else 0

    def _tesoreria(self):
        company = self.company_id.sudo()
        if company.f29_tesoreria_id:
            return company.f29_tesoreria_id
        Partner = self.env["res.partner"].sudo()
        tesoreria = Partner._buscar_por_vat(RUT_TESORERIA, [("parent_id", "=", False)]) or Partner.create({
            "name": "Tesorería General de la República",
            "vat": Partner._vats_busqueda(RUT_TESORERIA)[0],
            "document_number": RUT_TESORERIA,
            "document_type_id": self.env.ref("l10n_cl_fe.dt_RUT").id,
            "company_type": "company",
        })
        company.f29_tesoreria_id = tesoreria
        return tesoreria

    def _valores_asiento_f29(self):
        company = self.company_id.sudo()
        if not self.f29_formulario_ids:
            raise UserError(_("Primero consulte la propuesta de F29 del período"))
        partidas = self._partidas_f29(self._valores_declarados())
        faltan = sorted({company._fields[campo].string for campo, _n, _m, _p in partidas + [("f29_diario_id", 0, 0, 0)]
                         if not company[campo]})
        if faltan:
            raise UserError(_("Falta configurar en Ajustes, bloque F29: %s") % ", ".join(faltan))
        por_pagar = company.f29_cuenta_por_pagar_id
        if any(p[3] for p in partidas) and por_pagar.account_type != "liability_payable":
            raise UserError(_(
                "La cuenta F29 por pagar %s debe ser de tipo «Por pagar» para registrar el pago del F29"
            ) % por_pagar.display_name)
        debe = sum(m for _c, _n, m, _p in partidas if m > 0)
        haber = -sum(m for _c, _n, m, _p in partidas if m < 0)
        if company.currency_id.compare_amounts(debe, haber):
            detalle = "\n".join("%s: %s" % (n, m) for _c, n, m, _p in partidas)
            raise UserError(_("El asiento F29 no cuadra: debe %s, haber %s\n%s") % (debe, haber, detalle))
        tesoreria = self._tesoreria() if any(p[3] for p in partidas) else self.env["res.partner"]
        return {
            "move_type": "entry",
            "company_id": company.id,
            "journal_id": company.f29_diario_id.id,
            "date": self._fin_periodo(),
            "ref": _("F29 %s") % self.periodo,
            "line_ids": [(0, 0, {
                "name": nombre,
                "account_id": company[campo].id,
                "partner_id": tesoreria.id if pagar else False,
                "debit": monto if monto > 0 else 0,
                "credit": -monto if monto < 0 else 0,
            }) for campo, nombre, monto, pagar in partidas],
        }

    def action_generar_asiento_f29(self):
        self.ensure_one()
        if self.move_f29_id.state == "posted":
            raise UserError(_("El asiento F29 %s ya está publicado: no se modifica") % self.move_f29_id.name)
        valores = self._valores_asiento_f29()
        if self.move_f29_id.state == "draft":
            self.move_f29_id.write(dict(valores, line_ids=[(5, 0, 0)] + valores["line_ids"]))
        else:
            self.move_f29_id = self.env["account.move"].with_company(self.company_id).create(valores)
        return self.action_ver_asiento_f29()

    def action_ver_asiento_f29(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.move_f29_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def _lineas_por_pagar_f29(self):
        cuenta = self.company_id.sudo().f29_cuenta_por_pagar_id
        return self.move_f29_id.line_ids.filtered(lambda l: l.account_id == cuenta and not l.reconciled)

    @api.depends("move_f29_id.line_ids.reconciled", "move_f29_id.state")
    def _compute_f29_pagado(self):
        for r in self:
            cuenta = r.company_id.sudo().f29_cuenta_por_pagar_id
            lineas = r.move_f29_id.line_ids.filtered(lambda l: l.account_id == cuenta)
            r.f29_pagado = bool(lineas) and r.move_f29_id.state == "posted" and all(lineas.mapped("reconciled"))

    def _memo_pago_f29(self):
        declaracion = self._declaracion_vigente()
        if declaracion:
            return _("F29 %s folio %s") % (self.periodo, declaracion.folio)
        return _("F29 %s") % self.periodo

    def _declaracion_vigente(self):
        declaraciones = self.f29_declaracion_ids.filtered("folio")
        return declaraciones.filtered(lambda d: "vigente" in (d.estado or "").lower())[:1] or declaraciones[:1]

    def action_registrar_pago_f29(self):
        self.ensure_one()
        if self.move_f29_id.state != "posted":
            raise UserError(_("Publique primero el asiento F29 del período"))
        lineas = self._lineas_por_pagar_f29()
        if not lineas:
            raise UserError(_("El F29 de %s no tiene saldo por pagar") % self.periodo)
        declaracion = self._declaracion_vigente()
        contexto = {
            "active_model": "account.move.line",
            "active_ids": lineas.ids,
            "f29_revision_id": self.id,
        }
        if declaracion.fecha:
            contexto["default_payment_date"] = declaracion.fecha
        return {
            "name": _("Pagar F29"),
            "type": "ir.actions.act_window",
            "res_model": "account.payment.register",
            "view_mode": "form",
            "target": "new",
            "context": contexto,
        }

    def _saldo_cuenta(self, cuenta, hasta):
        if not cuenta:
            return 0
        dominio = [
            ("account_id", "=", cuenta.id),
            ("company_id", "=", self.company_id.id),
            ("parent_state", "=", "posted"),
            ("date", "<=", hasta),
        ]
        if self.move_f29_id:
            dominio.append(("move_id", "!=", self.move_f29_id.id))
        grupos = self.env["account.move.line"]._read_group(dominio, aggregates=["balance:sum"])
        return grupos[0][0] if grupos else 0

    @api.depends("f29_linea_ids.valor_sii", "f29_linea_ids.valor_propuesto", "f29_linea_ids.motivo", "move_f29_id")
    def _compute_f29_saldos_aviso(self):
        for r in self:
            r.f29_saldos_aviso = False
            company = r.company_id.sudo()
            if not r.f29_formulario_ids or not company.f29_cuenta_debito_id:
                continue
            v = r._valores_declarados()
            fin = r._fin_periodo()
            esperado = (
                (company.f29_cuenta_debito_id, _("IVA Débito Fiscal"), -v.get(538, 0)),
                (company.f29_cuenta_credito_id, _("IVA Crédito Fiscal"), v.get(537, 0) - v.get(504, 0)),
                (company.f29_cuenta_remanente_id, _("Remanente Crédito Fiscal"),
                 v.get(504, 0) - r._reajuste_partida(v.get(504, 0))),
            )
            filas = []
            for cuenta, nombre, declarado in esperado:
                if not cuenta:
                    continue
                saldo = r._saldo_cuenta(cuenta, fin)
                if company.currency_id.compare_amounts(saldo, declarado):
                    filas.append("<li>%s (%s): saldo %s, declarado %s, diferencia %s</li>" % (
                        nombre, cuenta.with_company(r.company_id).code, _formato_monto(saldo), _formato_monto(declarado),
                        _formato_monto(saldo - declarado)))
            if filas:
                r.f29_saldos_aviso = "<p>%s</p><ul>%s</ul><p>%s</p>" % (
                    _("Los saldos contables al %s, antes del asiento F29, no calzan con lo declarado:") % fin.strftime("%d-%m-%Y"),
                    "".join(filas),
                    _("Puede deberse a meses sin liquidar o a facturas mal clasificadas. Es solo informativo: "
                      "el ajuste lo valida el contador."),
                )


def _formato_monto(valor):
    return "{:,.0f}".format(valor).replace(",", ".")


class AccountPaymentRegisterF29(models.TransientModel):
    _inherit = "account.payment.register"

    f29_revision_id = fields.Many2one(
        "sii.revision.periodo", default=lambda self: self.env.context.get("f29_revision_id"),
    )
    f29_recargo = fields.Monetary(
        currency_field="currency_id", string="Reajuste, intereses y multa",
        help="Códigos 92, 93 y 94 del F29 pagado fuera de plazo: se registran en la cuenta de gasto configurada",
    )

    def _compute_communication(self):
        super()._compute_communication()
        for wizard in self.filtered("f29_revision_id"):
            wizard.communication = wizard.f29_revision_id._memo_pago_f29()

    @api.depends("early_payment_discount_mode", "f29_recargo")
    def _compute_payment_difference_handling(self):
        super()._compute_payment_difference_handling()
        for wizard in self.filtered(lambda w: w.f29_revision_id and w.f29_recargo and w.can_edit_wizard):
            wizard.payment_difference_handling = "reconcile"

    @api.depends("f29_recargo")
    def _compute_amount(self):
        super()._compute_amount()
        for wizard in self.filtered(lambda w: w.f29_revision_id and w.can_edit_wizard and w.payment_date):
            total = wizard._get_total_amount_in_wizard_currency_to_full_reconcile(
                wizard._get_batches()[0], early_payment_discount=False)[0]
            wizard.amount = abs(total) + (wizard.f29_recargo or 0)

    @api.onchange("f29_recargo")
    def _onchange_f29_recargo(self):
        if not self.f29_revision_id:
            return
        if self.f29_recargo:
            cuenta = self.f29_revision_id.company_id.sudo().f29_cuenta_recargo_id
            if not cuenta:
                raise UserError(_("Falta configurar en Ajustes, bloque F29, la cuenta de reajuste, intereses y multas"))
            self.payment_difference_handling = "reconcile"
            self.writeoff_account_id = cuenta
            self.writeoff_label = _("Reajuste, intereses y multa F29 %s") % self.f29_revision_id.periodo
