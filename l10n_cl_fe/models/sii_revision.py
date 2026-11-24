import hashlib
import hmac
import json
import logging
import re
import secrets
from datetime import date, datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

TIPOS_BOLETA = (39, 41)
ESTADOS_LIBRO_COMPRAS = ("REGISTRO", "PENDIENTE")
ESTADOS_CF_EXCLUIDOS = ["draft", "Anulado", "Rechazado"]
ESTADOS_CRUCE = [
    ("coincide", "Coincide"),
    ("diferencia", "Monto distinto"),
    ("recibido", "Recibido, sin factura"),
    ("sin_xml", "Pendiente sin XML"),
    ("falta_odoo", "Falta en Odoo"),
    ("falta_sii", "Falta en el SII"),
]
PESTANAS = ("compras", "ventas", "boletas")
GLOSAS_F29 = {
    502: "Débitos facturas emitidas",
    503: "Cantidad de facturas emitidas",
    509: "Cantidad de notas de crédito emitidas",
    510: "Débitos notas de crédito emitidas",
    512: "Cantidad de notas de débito emitidas",
    513: "Débitos notas de débito emitidas",
    110: "Cantidad de boletas",
    111: "Débitos boletas",
    758: "Cantidad de comprobantes de pago electrónico",
    759: "Débitos comprobantes de pago electrónico",
    586: "Cantidad de facturas exentas emitidas",
    511: "IVA por documentos electrónicos recibidos con derecho a crédito",
    514: "IVA por documentos electrónicos recibidos sin derecho a crédito",
    564: "Cantidad de facturas sin derecho a crédito (internas afectas)",
    521: "Neto facturas sin derecho a crédito (internas afectas)",
    524: "Cantidad de facturas de activo fijo",
    525: "Crédito facturas de activo fijo",
    577: "ILA débito: pisco, licores, whisky y aguardiente",
    32: "ILA débito: vinos, champaña y chichas",
    150: "ILA débito: cervezas",
    146: "ILA débito: bebidas analcohólicas",
    752: "ILA débito: bebidas analcohólicas con alto contenido de azúcar",
    545: "ILA débito: notas de débito emitidas",
    546: "ILA débito: notas de crédito emitidas",
    575: "ILA crédito recargado: pisco, licores, whisky y aguardiente",
    574: "ILA crédito recargado: vinos, champaña y chichas",
    580: "ILA crédito recargado: cervezas",
    582: "ILA crédito recargado: bebidas analcohólicas",
    753: "ILA crédito recargado: bebidas analcohólicas con alto contenido de azúcar",
    551: "ILA crédito: notas de débito recibidas",
    559: "ILA crédito: notas de crédito recibidas",
    142: "Ventas y servicios exentos o no gravados",
    519: "Cantidad de facturas recibidas del giro",
    520: "Crédito facturas recibidas del giro",
    527: "Cantidad de notas de crédito recibidas",
    528: "Crédito notas de crédito recibidas",
    531: "Cantidad de notas de débito recibidas",
    532: "Crédito notas de débito recibidas",
    538: "Total débitos",
    537: "Total créditos",
    89: "IVA determinado",
    77: "Remanente de crédito fiscal",
    563: "Base imponible PPM",
    62: "PPM neto determinado",
}
VENTAS_F29 = {33: (503, 502), 30: (503, 502), 56: (512, 513), 61: (509, 510)}
DEBITOS_F29 = (502, 764, 517, 501, 154, 518, 713, 741, 791, 717, 111, 759, 513)
DEBITOS_RESTA_F29 = (510, 709, 734)
CREDITOS_F29 = (520, 762, 766, 525, 532, 553, 504)
CREDITOS_RESTA_F29 = (528, 594, 592, 718, 790)
COMPRAS_F29 = {33: (519, 520), 30: (519, 520), 56: (531, 532), 61: (527, 528)}
ILA_VENTAS_F29 = {24: 577, 25: 32, 26: 150, 27: 146, 271: 752}
ILA_COMPRAS_F29 = {24: 575, 25: 574, 26: 580, 27: 582, 271: 753}
TOTALES_F29 = (538, 537, 89, 77, 62)
AJUSTABLES_F29 = (110, 111, 758, 759)
COMPRAS_CODIGOS_F29 = (519, 520, 511, 514, 524, 525, 527, 528, 531, 532, 564, 521, 504, 537, 89, 77,
                       574, 575, 580, 582, 753, 551, 559)
RESPALDO_SII_F29 = {
    519: ("compras", (33, 30, 46), "cantidad"),
    520: ("compras", (33, 30, 46), "iva"),
    511: ("compras", (33, 46, 56, 61), "iva"),
    531: ("compras", (56,), "cantidad"),
    532: ("compras", (56,), "iva"),
    527: ("compras", (61,), "cantidad"),
    528: ("compras", (61,), "iva"),
    503: ("ventas", (33, 30), "cantidad"),
    502: ("ventas", (33, 30), "iva"),
    512: ("ventas", (56,), "cantidad"),
    513: ("ventas", (56,), "iva"),
    509: ("ventas", (61,), "cantidad"),
    510: ("ventas", (61,), "iva"),
    110: ("boletas", (39,), "cantidad"),
    111: ("boletas", (39,), "iva"),
    758: ("boletas", (48,), "cantidad"),
    759: ("boletas", (48,), "iva"),
}
FORMULAS_F29 = {
    503: "Cantidad de facturas 33/30 del libro de ventas",
    502: "IVA (código 14) de las facturas 33/30 del libro de ventas",
    512: "Cantidad de notas de débito 56 del libro de ventas",
    513: "IVA de las notas de débito 56 del libro de ventas",
    509: "Cantidad de notas de crédito 61 del libro de ventas",
    510: "IVA de las notas de crédito 61 del libro de ventas",
    110: "Boletas 39 del libro de ventas (consumo de folios), sin anuladas",
    111: "IVA de la línea de boletas 39 del libro de ventas (consumo de folios)",
    758: "Comprobantes de pago 48 de la línea 48 del libro de ventas",
    759: "IVA de la línea 48 del libro de ventas",
    519: "Cantidad de facturas 33/30/46 del libro de compras con IVA recuperable o de uso común",
    520: "IVA recuperable de facturas 33/30/46 del libro de compras + IVA de uso común × factor de proporcionalidad",
    511: "IVA de los DTE recibidos 33/46/56 − 61 del libro de compras, con derecho a crédito",
    514: "IVA de los DTE recibidos sin derecho a crédito (no recuperable)",
    524: "Cantidad de facturas de activo fijo del libro de compras",
    525: "IVA de facturas de activo fijo del libro de compras",
    564: "Cantidad de facturas con IVA no recuperable",
    521: "Neto de facturas con IVA no recuperable",
    504: "Código 77 de la Declaración anterior en Odoo, o el 504 del SII",
    563: "Neto + exento de facturas, ND y boletas − NC del libro de ventas",
    62: "redondeo(563 × 115 / 100) − 68",
    89: "538 − 537 si es positivo",
    77: "537 − 538 si es positivo",
}


def _periodo_anterior(hoy):
    return (hoy.replace(day=1) - timedelta(days=1)).strftime("%Y%m")


def _totales_f29(valores, compras=True):
    debitos = sum(valores.get(c, 0) for c in DEBITOS_F29) - sum(valores.get(c, 0) for c in DEBITOS_RESTA_F29)
    res = {538: debitos}
    if compras:
        creditos = sum(valores.get(c, 0) for c in CREDITOS_F29) - sum(valores.get(c, 0) for c in CREDITOS_RESTA_F29)
        res.update({537: creditos, 89: max(debitos - creditos, 0), 77: max(creditos - debitos, 0)})
    if valores.get(563) and valores.get(115):
        res[62] = round(valores[563] * valores[115] / 100) - valores.get(68, 0)
    return res


def _impuestos_movimiento(move):
    res = {}
    for imp in move._get_move_imps().values():
        tax = move.env["account.tax"].browse(imp["tax_id"])
        res[tax] = res.get(tax, 0) + abs((imp["credit"] or 0) - (imp["debit"] or 0))
    return res


def _con_credito(tax):
    lineas = tax.invoice_repartition_line_ids.filtered(lambda r: r.repartition_type == "tax")
    return any(l.account_id and l.use_in_tax_closing for l in lineas)


XML_NO_MIPYME = "XML no disponible en el SII: la compañía no usa el sistema MIPYME; el XML solo llega por correo del emisor"
XML_NO_EN_SII = "El SII no tiene el XML de este documento"
URL_PROPUESTA_F29 = "https://www4.sii.cl/propuestaf29ui/index.html#/infopropuesta"
URL_INGRESO_SII = "https://zeusr.sii.cl/AUT2000/InicioAutenticacion/IngresoRutClave.html?%s"
VIGENCIA_INGRESO_SII = 60
TOLERANCIA_REAJUSTE_UTM = 0.02
URL_OPERACION_IVA = "https://www.sii.cl/destacados/operacion_iva/"


def _formato(valor):
    valor = valor or 0
    if float(valor).is_integer():
        return "{:,.0f}".format(valor).replace(",", ".")
    return "{:,.3f}".format(valor).replace(",", "X").replace(".", ",").replace("X", ".")


def _origen_documento(move, totales):
    registro = isinstance(move, models.BaseModel)
    partner = getattr(move, "commercial_partner_id", False)
    numero = getattr(move, "sii_document_number", "") or ""
    return {
        "tipo_dte": move.document_class_id.sii_code,
        "folio": str(numero),
        "rut": (partner.document_number or "") if partner else "",
        "fecha": getattr(move, "invoice_date", False) or False,
        "neto": totales["neto"],
        "iva": totales["iva"],
        "move_id": move.id if registro and move._name == "account.move" else False,
        "descripcion": "%s %s" % (getattr(move.document_class_id, "name", "") or "", numero),
    }


def _formula_total(codigo):
    if codigo == 538:
        return " + ".join(map(str, DEBITOS_F29)) + " − " + " − ".join(map(str, DEBITOS_RESTA_F29))
    if codigo == 537:
        return " + ".join(map(str, CREDITOS_F29)) + " − " + " − ".join(map(str, CREDITOS_RESTA_F29))
    return FORMULAS_F29.get(codigo, "")


def _componentes_total(codigo):
    if codigo == 538:
        return [(c, 1) for c in DEBITOS_F29] + [(c, -1) for c in DEBITOS_RESTA_F29]
    if codigo == 537:
        return [(c, 1) for c in CREDITOS_F29] + [(c, -1) for c in CREDITOS_RESTA_F29]
    if codigo in (89, 77):
        return [(538, 1 if codigo == 89 else -1), (537, -1 if codigo == 89 else 1)]
    if codigo == 62:
        return [(563, 1)]
    return []


def _fecha(valor):
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(valor or "")[:10], formato).date()
        except ValueError:
            continue
    return False


class SiiRevisionPeriodo(models.Model):
    _name = "sii.revision.periodo"
    _description = "Declaración Mensual F29"
    _order = "periodo desc, company_id"

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company", string="Compañía", required=True, default=lambda self: self.env.company,
    )
    periodo = fields.Char(
        required=True, size=6, help="AAAAMM",
        default=lambda self: _periodo_anterior(fields.Date.context_today(self)),
    )
    compra_book_id = fields.Many2one("account.move.book", string="Libro de compras", readonly=True)
    venta_book_id = fields.Many2one("account.move.book", string="Libro de ventas", readonly=True)
    sistema_xml = fields.Selection(
        [("mipyme", "Sistema MIPYME del SII"), ("propio", "Software propio o de mercado")],
        string="Sistema de facturación", compute="_compute_sistema_xml",
    )

    sistema_xml_aviso = fields.Char(related="company_id.sii_sistema_xml_aviso")
    sii_ingreso_huella = fields.Char(groups="base.group_system", copy=False)
    sii_ingreso_vence = fields.Datetime(groups="base.group_system", copy=False)

    @api.depends("company_id.sii_sistema_xml", "company_id.sii_sistema_xml_trafquin", "company_id.partner_id.es_mipyme")
    def _compute_sistema_xml(self):
        for r in self:
            r.sistema_xml = r.company_id._sistema_xml() if r.company_id else False
    f29_tiene_propuesta = fields.Boolean(string="Hay propuesta", readonly=True)
    f29_estado = fields.Char(string="Estado de la propuesta", readonly=True)
    f29_otros = fields.Text(string="Otros datos del SII", readonly=True)
    f29_error = fields.Text(readonly=True)
    f29_fecha = fields.Datetime(string="F29 consultado", readonly=True)
    f29_linea_ids = fields.One2many("sii.revision.f29.linea", "revision_id", string="Códigos")
    f29_formulario_ids = fields.One2many(
        "sii.revision.f29.linea", "revision_id", domain=[("administrativo", "=", False)], string="Códigos del formulario",
    )
    f29_admin_ids = fields.One2many(
        "sii.revision.f29.linea", "revision_id", domain=[("administrativo", "=", True)],
        string="Códigos administrativos del SII",
    )
    compras_pendientes_iva = fields.Monetary(
        compute="_compute_compras_pendientes", string="IVA de compras del RCV sin factura en Odoo",
    )
    compras_observados = fields.Text(readonly=True)
    observaciones_html = fields.Html(compute="_compute_observaciones", string="Observaciones probables del SII")
    f29_declaracion_ids = fields.One2many("sii.revision.f29.declaracion", "revision_id", string="Declaraciones")
    f29_ajustes = fields.Integer(compute="_compute_f29_ajustes", string="Ajustes en borrador")
    f29_descuadres = fields.Integer(compute="_compute_f29_descuadres", store=True, string="Códigos descuadrados")
    compras_linea_ids = fields.One2many(
        "sii.revision.rcv.linea", "revision_id", domain=[("libro", "=", "compras")], string="Compras",
    )
    ventas_linea_ids = fields.One2many(
        "sii.revision.rcv.linea", "revision_id", domain=[("libro", "=", "ventas")], string="Ventas",
    )
    boleta_linea_ids = fields.One2many("sii.revision.boleta.linea", "revision_id", string="Boletas")
    compras_error = fields.Text(readonly=True)
    ventas_error = fields.Text(readonly=True)
    boletas_error = fields.Text(readonly=True)
    compras_avisos = fields.Text(readonly=True)
    ventas_avisos = fields.Text(readonly=True)
    boletas_avisos = fields.Text(readonly=True)
    compras_completo = fields.Boolean(readonly=True)
    ventas_completo = fields.Boolean(readonly=True)
    boletas_completo = fields.Boolean(readonly=True)
    compras_fecha = fields.Datetime(string="Compras consultado", readonly=True)
    ventas_fecha = fields.Datetime(string="Ventas consultado", readonly=True)
    boletas_fecha = fields.Datetime(string="Boletas consultado", readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    compras_iva_sii = fields.Monetary(compute="_compute_totales", string="IVA SII compras")
    compras_iva_odoo = fields.Monetary(compute="_compute_totales", string="IVA Odoo compras")
    compras_iva_dif = fields.Monetary(compute="_compute_totales", string="Diferencia IVA compras")
    compras_total_sii = fields.Monetary(compute="_compute_totales", string="Total SII compras")
    compras_total_odoo = fields.Monetary(compute="_compute_totales", string="Total Odoo compras")
    compras_total_dif = fields.Monetary(compute="_compute_totales", string="Diferencia total compras")
    ventas_iva_sii = fields.Monetary(compute="_compute_totales", string="IVA SII ventas")
    ventas_iva_odoo = fields.Monetary(compute="_compute_totales", string="IVA Odoo ventas")
    ventas_iva_dif = fields.Monetary(compute="_compute_totales", string="Diferencia IVA ventas")
    ventas_total_sii = fields.Monetary(compute="_compute_totales", string="Total SII ventas")
    ventas_total_odoo = fields.Monetary(compute="_compute_totales", string="Total Odoo ventas")
    ventas_total_dif = fields.Monetary(compute="_compute_totales", string="Diferencia total ventas")
    boletas_iva_sii = fields.Monetary(compute="_compute_totales", string="IVA SII boletas")
    boletas_iva_odoo = fields.Monetary(compute="_compute_totales", string="IVA Odoo boletas")
    boletas_iva_dif = fields.Monetary(compute="_compute_totales", string="Diferencia IVA boletas")
    boletas_total_sii = fields.Monetary(compute="_compute_totales", string="Total SII boletas")
    boletas_total_odoo = fields.Monetary(compute="_compute_totales", string="Total Odoo boletas")
    boletas_total_dif = fields.Monetary(compute="_compute_totales", string="Diferencia total boletas")

    _sql_constraints = [
        ("periodo_unico", "unique(company_id, periodo)",
         "Ya existe la Declaración Mensual F29 de ese período para la compañía"),
    ]

    @api.depends("periodo", "company_id")
    def _compute_name(self):
        for r in self:
            r.name = "%s/%s %s" % (r.periodo[4:], r.periodo[:4], r.company_id.name) if r.periodo else False

    @api.depends("f29_linea_ids.motivo", "f29_linea_ids.valor_propuesto", "f29_linea_ids.diferencia")
    def _compute_f29_ajustes(self):
        for r in self:
            r.f29_ajustes = len(r.f29_linea_ids._ajustadas())

    @api.depends("f29_linea_ids.calculado_odoo", "f29_linea_ids.administrativo", "f29_linea_ids.explicacion",
                 "f29_linea_ids.diferencia")
    def _compute_f29_descuadres(self):
        for r in self:
            r.f29_descuadres = len(r.f29_linea_ids.filtered(
                lambda l: l.calculado_odoo and not l.administrativo and not l.explicacion and abs(l.diferencia) > 1))

    @api.depends("compras_linea_ids.estado", "compras_linea_ids.estado_sii", "compras_linea_ids.iva")
    def _compute_compras_pendientes(self):
        for r in self:
            r.compras_pendientes_iva = sum(r.compras_linea_ids.filtered(
                lambda l: l.estado in ("sin_xml", "recibido", "falta_odoo")
                and l.estado_sii in ESTADOS_LIBRO_COMPRAS).mapped("iva"))

    def _declarado(self, *codigos):
        valores = {}
        for l in self.f29_formulario_ids:
            valores[l.codigo] = l.valor_propuesto if l in l._ajustadas() else l.valor_sii
        return sum(valores.get(c, 0) for c in codigos)

    def _iva_registro(self, lineas, tipos, estados=None):
        return sum(lineas.filtered(
            lambda l: l.estado != "falta_sii" and l.tipo_dte in tipos
            and (not estados or l.estado_sii in estados)).mapped("iva"))

    def _observaciones(self):
        self.ensure_one()
        ventas, compras, boletas = self.ventas_linea_ids, self.compras_linea_ids, self.boleta_linea_ids
        hay_f29 = bool(self.f29_formulario_ids)
        res = []

        def menor(nombre, declarado, registro, tiene_datos, detalle):
            if not hay_f29 or not tiene_datos:
                res.append((nombre, "sin_info", detalle, declarado, registro))
            else:
                res.append((nombre, "alerta" if declarado < registro - 1 else "ok", detalle, declarado, registro))

        menor("W01", self._declarado(502, 764, 717, 513, 517), self._iva_registro(ventas, (33, 30, 56)),
              bool(self.ventas_fecha), _("Débito de facturas y notas de débito (502, 764, 717, 513, 517) contra el Registro de Ventas"))
        menor("W01i", self._declarado(111), sum(boletas.filtered(lambda b: b.tipo_dte == 39).mapped("iva")),
              bool(self.boletas_fecha), _("Débito de boletas (111) contra el resumen de boletas del RCV"))
        menor("W01N", self._declarado(111, 759), sum(boletas.filtered(lambda b: b.tipo_dte in (39, 48)).mapped("iva")),
              bool(self.boletas_fecha), _("Débito de boletas y comprobantes de pago (111 y 759) contra lo registrado"))
        menor("W01LF", self._declarado(501, 818), self._iva_registro(ventas, (43,)),
              bool(self.ventas_fecha), _("Liquidaciones y liquidaciones factura (501, 818) contra el Registro de Ventas"))
        credito = self._declarado(520, 762, 766, 525)
        registro = self._iva_registro(compras, (33, 30, 56, 46), ("REGISTRO",))
        if not hay_f29 or not self.compras_completo:
            res.append(("W08", "sin_info", _("Crédito (520, 762, 766, 525) contra el Registro de Compras"), credito, registro))
        else:
            res.append(("W08", "alerta" if credito > registro + 1 else "ok",
                        _("Crédito (520, 762, 766, 525) contra el Registro de Compras"), credito, registro))
        valido = re.fullmatch(r"\d{4}(0[1-9]|1[0-2])", self.periodo or "")
        anterior = valido and self._periodo_anterior()
        linea_77 = anterior and anterior.f29_formulario_ids.filtered(lambda l: l.codigo == 77)[:1]
        remanente = self._declarado(504)
        texto = _("Remanente 504 contra el 77 del mes anterior reajustado por UTM")
        reajustado = linea_77 and self._reajuste_remanente(anterior._declarado(77))[0]
        if not hay_f29 or not linea_77 or reajustado is False:
            res.append(("FM01", "sin_info", texto, remanente, reajustado or 0))
        else:
            res.append(("FM01", "ok" if abs(remanente - reajustado) <= 1 else "alerta", texto, remanente, reajustado))
        if not self.compras_observados:
            res.append(("W39", "sin_info", _("Proveedores observados por el SII"), 0, 0))
        else:
            ruts = {self.env["res.partner"]._rut_clave(p.get("rut") or "") for p in json.loads(self.compras_observados)}
            observados = compras.filtered(lambda l: self.env["res.partner"]._rut_clave(l.rut or "") in ruts)
            detalle = ", ".join("%s %s %s" % (l.tipo_dte, l.folio, l.rut) for l in observados) or _("ninguno")
            res.append(("W39", "alerta" if observados else "ok",
                        _("Documentos de proveedores observados por el SII: %s") % detalle,
                        len(observados), len(ruts)))
        return res

    @api.depends("f29_linea_ids.valor_sii", "f29_linea_ids.valor_propuesto", "f29_linea_ids.motivo",
                 "compras_linea_ids.iva", "ventas_linea_ids.iva", "boleta_linea_ids.iva", "compras_observados",
                 "compras_completo", "ventas_fecha", "boletas_fecha")
    def _compute_observaciones(self):
        colores = {"ok": ("success", _("Sin observación")), "alerta": ("danger", _("Probable observación")),
                   "sin_info": ("secondary", _("Sin información"))}
        for r in self:
            filas = []
            for nombre, estado, detalle, declarado, registro in r._observaciones():
                color, texto = colores[estado]
                filas.append(
                    "<tr><td><a href='%s' target='_blank'>%s</a></td><td><span class='badge text-bg-%s'>%s</span></td>"
                    "<td>%s</td><td class='text-end'>%s</td><td class='text-end'>%s</td></tr>" % (
                        URL_OPERACION_IVA, nombre, color, texto, detalle, _formato(declarado), _formato(registro)))
            r.observaciones_html = (
                "<table class='table table-sm'><thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th><th>%s</th>"
                "</tr></thead><tbody>%s</tbody></table>" % (
                    _("Observación"), _("Estado"), _("Qué revisa"), _("F29"), _("Registro SII"), "".join(filas)))

    @api.depends(
        "compras_linea_ids.iva", "compras_linea_ids.total", "compras_linea_ids.odoo_iva",
        "compras_linea_ids.odoo_total", "ventas_linea_ids.iva", "ventas_linea_ids.total",
        "ventas_linea_ids.odoo_iva", "ventas_linea_ids.odoo_total", "boleta_linea_ids.iva",
        "boleta_linea_ids.total", "boleta_linea_ids.odoo_iva", "boleta_linea_ids.odoo_total",
    )
    def _compute_totales(self):
        for r in self:
            for pestana, lineas in (("compras", r.compras_linea_ids), ("ventas", r.ventas_linea_ids),
                                    ("boletas", r.boleta_linea_ids)):
                valores = {
                    "iva_sii": sum(lineas.mapped("iva")),
                    "iva_odoo": sum(lineas.mapped("odoo_iva")),
                    "total_sii": sum(lineas.mapped("total")),
                    "total_odoo": sum(lineas.mapped("odoo_total")),
                }
                valores["iva_dif"] = valores["iva_sii"] - valores["iva_odoo"]
                valores["total_dif"] = valores["total_sii"] - valores["total_odoo"]
                for clave, valor in valores.items():
                    r["%s_%s" % (pestana, clave)] = valor

    def _rango(self):
        if not re.fullmatch(r"\d{4}(0[1-9]|1[0-2])", self.periodo or ""):
            raise UserError(_("El período debe tener el formato AAAAMM"))
        desde = date(int(self.periodo[:4]), int(self.periodo[4:]), 1)
        hasta = (desde.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        return desde, hasta

    def _periodo_libro(self):
        return "%s-%s" % (self.periodo[:4], self.periodo[4:])

    def _trafquin(self, ruta, datos, clave=False):
        return self.env["sii.dte.trafquin.wizard"]._llamar_trafquin(
            self.company_id, ruta, dict({"periodo": self.periodo}, **datos), clave=clave)

    def _consultar(self, pestana, funcion):
        self.ensure_one()
        self._rango()
        try:
            with self.env.cr.savepoint():
                funcion()
            self["%s_error" % pestana] = False
        except UserError as e:
            mensaje = e.args[0] if e.args else str(e)
            if "CLAVE_RECHAZADA" in mensaje and self.company_id._marcar_clave_sii_rechazada():
                mensaje = _("La clave SII de %s fue rechazada: revísela en Ajustes") % self.company_id.name
            self["%s_error" % pestana] = mensaje
        self["%s_fecha" % pestana] = fields.Datetime.now()

    def action_consultar_f29(self):
        for r in self:
            r._consultar("f29", r._consultar_f29)

    def action_consultar_compras(self):
        for r in self:
            r._consultar("compras", lambda r=r: r._consultar_rcv("compras"))

    def action_consultar_ventas(self):
        for r in self:
            r._consultar("ventas", lambda r=r: r._consultar_rcv("ventas"))

    def action_consultar_boletas(self):
        for r in self:
            r._consultar("boletas", r._consultar_boletas)

    def action_actualizar_boletas(self):
        for r in self:
            r._consultar("boletas", lambda r=r: r._consultar_boletas(actualizar=True))

    def action_consultar_rcv(self):
        self.action_consultar_compras()
        self.action_consultar_ventas()

    def action_consultar_todo(self):
        self.action_consultar_rcv()
        self.action_consultar_boletas()
        self.action_consultar_f29()

    def _libro(self, tipo):
        Libro = self.env["account.move.book"].sudo()
        libro = Libro.search([
            ("company_id", "=", self.company_id.id),
            ("periodo_tributario", "=", self._periodo_libro()),
            ("tipo_operacion", "=", tipo),
            ("tipo_libro", "=", "MENSUAL"),
        ], order="id desc", limit=1)
        if not libro:
            libro = Libro.with_company(self.company_id).create(dict(self._valores_libro(tipo), **{
                "company_id": self.company_id.id,
                "periodo_tributario": self._periodo_libro(),
                "tipo_operacion": tipo,
                "name": "%s %s" % (tipo, self._periodo_libro()),
                "currency_id": self.company_id.currency_id.id,
            }))
        elif libro.sii_result in (False, "draft"):
            libro.write(self._valores_libro(tipo))
        self["compra_book_id" if tipo == "COMPRA" else "venta_book_id"] = libro.id
        return libro

    def _valores_libro(self, tipo):
        moneda = self.company_id.currency_id
        temporal = self.env["account.move.book"].sudo().with_company(self.company_id).new({
            "company_id": self.company_id.id,
            "periodo_tributario": self._periodo_libro(),
            "tipo_operacion": tipo,
            "currency_id": moneda.id,
        })
        temporal.set_movimientos()
        temporal.compute_taxes()
        return {
            "date": self._fin_periodo(),
            "move_ids": [(6, 0, temporal.move_ids.ids)],
            "boletas": [(5,)] + [(0, 0, {
                "currency_id": moneda.id,
                "tipo_boleta": b.tipo_boleta.id,
                "rango_inicial": b.rango_inicial or 0,
                "rango_final": b.rango_final or 0,
                "cantidad_boletas": b.cantidad_boletas,
                "cantidad_anulados": b.cantidad_anulados,
                "neto": b.neto,
                "monto_exento": b.monto_exento,
                "impuesto": b.impuesto.id,
                "monto_impuesto": b.monto_impuesto,
                "monto_total": b.monto_total,
            }) for b in temporal.boletas],
            "impuestos": [(5,)] + [(0, 0, {
                "currency_id": moneda.id,
                "tax_id": t.tax_id.id,
                "credit": t.credit,
                "debit": t.debit,
            }) for t in temporal.impuestos],
        }

    def _consultar_f29(self):
        data = self._trafquin("/api/f29/propuesta", {}, clave=True).get("data") or {}
        anteriores = {l.codigo: l for l in self.f29_linea_ids._ajustadas()}
        grupos = data.get("grupos") or {}
        lineas = []
        for administrativo, clave in ((False, "codigos"), (True, "codigos_administrativos")):
            for c in data.get(clave) or []:
                lineas.append({
                    "codigo": int(c.get("codigo") or 0),
                    "orden": c.get("orden") or False,
                    "columna": c.get("columna") or "",
                    "glosa": c.get("glosa") or "",
                    "signo": c.get("signo") or "",
                    "grupo": grupos.get(c.get("grupo"), c.get("grupo")) or "",
                    "administrativo": administrativo,
                    "valor_sii": float(c.get("valor") or 0),
                })
        sii = {l["codigo"]: l["valor_sii"] for l in lineas}
        calculados = _totales_f29(sii)
        for codigo, valor in (data.get("totales_calculados") or {}).items():
            if str(codigo).isdigit() and valor is not None:
                calculados[int(codigo)] = float(valor)
        for codigo, valor in calculados.items():
            if not sii.get(codigo):
                sii[codigo] = valor
                linea = next((l for l in lineas if l["codigo"] == codigo), None)
                if linea is None:
                    linea = {"codigo": codigo, "glosa": GLOSAS_F29.get(codigo, "")}
                    lineas.append(linea)
                linea.update(valor_sii=valor, sii_calculado=True)
        odoo, aportes, motivos = self._propuesta_odoo(sii)
        for codigo, valor in odoo.items():
            if codigo not in sii and valor:
                lineas.append({"codigo": codigo, "glosa": GLOSAS_F29.get(codigo, ""), "valor_sii": 0})
        for valores in lineas:
            codigo = valores["codigo"]
            valores.update(calculado_odoo=codigo in odoo, valor_odoo=odoo.get(codigo, 0),
                           formula=_formula_total(codigo) if codigo in TOTALES_F29 else FORMULAS_F29.get(codigo, ""),
                           motivo_no_calculado=False if codigo in odoo else (
                               motivos.get(codigo) or _("Odoo no calcula este código")))
            if codigo in odoo and codigo not in TOTALES_F29 and not valores.get("administrativo"):
                valores["aporte_ids"] = [(0, 0, dict(a, origen="odoo")) for a in aportes.get(codigo, [])] + [
                    (0, 0, a) for a in self._aportes_sii_y_diferencia(
                        codigo, aportes.get(codigo, []), valores.get("valor_sii", 0), odoo[codigo])]
            if not valores.get("glosa"):
                valores["glosa"] = GLOSAS_F29.get(codigo, "")
            anterior = anteriores.get(codigo)
            if anterior:
                valores.update({
                    "valor_propuesto": anterior.valor_propuesto,
                    "motivo": anterior.motivo,
                    "ajuste_uid": anterior.ajuste_uid.id,
                    "ajuste_fecha": anterior.ajuste_fecha,
                })
        existentes = {d.folio: d for d in self.f29_declaracion_ids if d.folio}
        declaraciones = []
        for d in data.get("declaraciones") or []:
            valores = {
                "estado": d.get("estado"),
                "folio": str(d.get("folio") or ""),
                "fecha": _fecha(d.get("fecha")),
                "total": float(d.get("total") or 0),
                "en_negocio": bool(d.get("en_negocio")),
            }
            anterior = existentes.pop(valores["folio"], None) if valores["folio"] else None
            declaraciones.append((1, anterior.id, valores) if anterior else (0, 0, valores))
        declaraciones += [(2, d.id) for d in self.f29_declaracion_ids if d.folio not in {
            v[2]["folio"] for v in declaraciones if v[0] in (0, 1)}]
        otros = data.get("otros_campos")
        self.write({
            "f29_tiene_propuesta": bool(data.get("tiene_propuesta")),
            "f29_estado": data.get("descripcion_estado") or (
                _("Sin propuesta del SII") if not data.get("tiene_propuesta") else False),
            "f29_otros": json.dumps(otros, ensure_ascii=False, indent=1) if otros else False,
            "f29_linea_ids": [(5,)] + [(0, 0, v) for v in lineas],
            "f29_declaracion_ids": declaraciones,
        })
        self._aportes_totales()

    def _aportes_totales(self):
        por_codigo = {l.codigo: l for l in self.f29_formulario_ids}
        Aporte = self.env["sii.revision.f29.aporte"]
        for codigo in TOTALES_F29:
            linea = por_codigo.get(codigo)
            if not linea or not linea.calculado_odoo:
                continue
            valores = []
            for componente, signo in _componentes_total(codigo):
                origen = por_codigo.get(componente)
                if origen and origen.calculado_odoo and origen.valor_odoo:
                    valores.append({
                        "linea_id": linea.id,
                        "origen": "odoo",
                        "componente_id": origen.id,
                        "descripcion": "%s %s %s" % ("+" if signo > 0 else "−", componente, origen.glosa or ""),
                        "monto": signo * origen.valor_odoo,
                    })
            residuo = linea.valor_odoo - sum(v["monto"] for v in valores)
            if abs(residuo) >= 0.005:
                texto = _("Resultado negativo: el código queda en 0") if codigo in (89, 77) and not linea.valor_odoo \
                    else _("Tasa 115 y crédito 68 informados por el SII, y redondeo")
                valores.append({"linea_id": linea.id, "origen": "odoo", "monto": residuo, "descripcion": texto})
            Aporte.create(valores)
            Aporte.create([dict(v, linea_id=linea.id) for v in self._aportes_sii_y_diferencia(
                codigo, valores, linea.valor_sii, linea.valor_odoo)])

    def _clave_aporte(self, libro, aporte):
        if libro == "boletas":
            return (aporte.get("tipo_dte"),)
        rut = self.env["res.partner"]._rut_clave(aporte.get("rut") or "") if libro == "compras" else ""
        return (aporte.get("tipo_dte"), str(aporte.get("folio") or ""), rut)

    def _aportes_sii_y_diferencia(self, codigo, aportes_odoo, valor_sii, valor_odoo):
        res = []
        respaldo = RESPALDO_SII_F29.get(codigo)
        resto = valor_sii - valor_odoo
        if respaldo:
            libro, tipos, campo = respaldo
            sii = {}
            if libro == "boletas":
                for b in self.boleta_linea_ids.filtered(lambda b: b.tipo_dte in tipos):
                    aporte = {"origen": "sii", "tipo_dte": b.tipo_dte, "fecha": b.fecha, "iva": b.iva,
                              "monto": b.cantidad_sii if campo == "cantidad" else b.iva, "sii_boleta_id": b.id,
                              "descripcion": _("Resumen de boletas del RCV")}
                    res.append(aporte)
                    sii.setdefault(self._clave_aporte(libro, aporte), []).append(aporte)
            else:
                lineas = self.compras_linea_ids if libro == "compras" else self.ventas_linea_ids
                lineas = lineas.filtered(lambda l: l.estado != "falta_sii" and l.tipo_dte in tipos and (
                    libro != "compras" or l.estado_sii == "REGISTRO"))
                for l in lineas:
                    signo = -1 if codigo == 511 and l.tipo_dte == 61 else 1
                    aporte = {"origen": "sii", "tipo_dte": l.tipo_dte, "folio": l.folio, "rut": l.rut,
                              "fecha": l.fecha, "neto": l.neto, "iva": l.iva,
                              "monto": 1 if campo == "cantidad" else signo * l.iva,
                              "rcv_linea_id": l.id, "move_id": l.move_id.id,
                              "descripcion": "%s %s" % (l.razon_social or "", l.estado_sii or "")}
                    res.append(aporte)
                    sii.setdefault(self._clave_aporte(libro, aporte), []).append(aporte)
            odoo = {}
            for a in aportes_odoo:
                odoo.setdefault(self._clave_aporte(libro, a), []).append(a)
            for clave in list(sii) + [c for c in odoo if c not in sii]:
                diferencia = sum(a["monto"] for a in sii.get(clave, [])) - sum(a["monto"] for a in odoo.get(clave, []))
                if abs(diferencia) < 0.005:
                    continue
                base = (sii.get(clave) or odoo.get(clave))[0]
                if clave not in odoo:
                    texto = _("Está en el SII y no en Odoo")
                elif clave not in sii:
                    texto = _("Está en Odoo y no en el SII")
                else:
                    texto = _("Monto distinto entre el SII y Odoo")
                res.append({
                    "origen": "diferencia",
                    "tipo_dte": base.get("tipo_dte"),
                    "folio": base.get("folio"),
                    "rut": base.get("rut"),
                    "fecha": base.get("fecha"),
                    "iva": base.get("iva"),
                    "monto": diferencia,
                    "move_id": base.get("move_id") or False,
                    "rcv_linea_id": base.get("rcv_linea_id") or False,
                    "sii_boleta_id": base.get("sii_boleta_id") or False,
                    "boleta_id": base.get("boleta_id") or False,
                    "descripcion": "%s: %s" % (texto, base.get("descripcion") or ""),
                })
                resto -= diferencia
        if abs(resto) >= 0.005:
            res.append({
                "origen": "diferencia",
                "monto": resto,
                "descripcion": _("Diferencia entre la propuesta del SII y los documentos del RCV") if respaldo
                else _("Sin detalle por documento: el SII no informa los documentos de este código"),
            })
        return res

    def _propuesta_odoo(self, sii):
        res, aportes, motivos = {}, {}, {}

        def sumar(codigo, valor, origen):
            res[codigo] = res.get(codigo, 0) + valor
            aportes.setdefault(codigo, []).append(dict(origen, monto=valor))

        compra = self.compra_book_id or self._libro("COMPRA")
        venta = self.venta_book_id or self._libro("VENTA")
        for m in venta._get_moves():
            totales = m.totales_por_movimiento()
            origen = _origen_documento(m, totales)
            codigo = m.document_class_id.sii_code
            signo = -1 if codigo == 61 else 1
            sumar(563, signo * (totales["neto"] + totales["exento"]), origen)
            if codigo in VENTAS_F29:
                cantidad, monto = VENTAS_F29[codigo]
                sumar(cantidad, 1, origen)
                sumar(monto, totales["iva"], origen)
            elif codigo == 34:
                sumar(586, 1, origen)
                sumar(142, totales["exento"], origen)
            for tax, monto in _impuestos_movimiento(m).items():
                if tax.sii_code in ILA_VENTAS_F29:
                    sumar({56: 545, 61: 546}.get(codigo, ILA_VENTAS_F29[tax.sii_code]), monto,
                          dict(origen, descripcion="%s %s" % (origen["descripcion"], tax.name)))
        for v in venta._nc_boletas_voucher():
            origen = {
                "tipo_dte": 61,
                "folio": str(v.sii_document_number),
                "fecha": v.fecha_emision,
                "descripcion": _("NC de boletas con voucher %s (%s)") % (v.sii_document_number, v.periodo),
                "neto": v.total_neto + v.total_exento,
                "iva": v.total_iva,
            }
            sumar(509, 1, origen)
            sumar(510, v.total_iva, origen)
            sumar(563, -(v.total_neto + v.total_exento), origen)
        for b in venta.boletas:
            origen = {
                "tipo_dte": b.tipo_boleta.sii_code,
                "descripcion": _("Línea %s del libro de ventas %s (consumo de folios)") % (
                    b.tipo_boleta.name, venta.periodo_tributario),
                "neto": b.neto + b.monto_exento,
                "iva": b.monto_impuesto,
                "boleta_id": b.id,
            }
            if b.tipo_boleta.sii_code == 39:
                sumar(110, b.cantidad_boletas - b.cantidad_anulados, origen)
                sumar(111, b.monto_impuesto, origen)
            elif b.tipo_boleta.sii_code == 48:
                sumar(758, b.cantidad_boletas, origen)
                sumar(759, b.monto_impuesto, origen)
            sumar(563, b.neto + b.monto_exento, origen)
        res.update({codigo: res.get(codigo, 0) for codigo in (503, 502, 110, 111, 758, 759, 563)})
        self._sumar_retenciones_honorarios(sumar)
        res[151] = res.get(151, 0)
        if self.compras_completo:
            res.update({codigo: res.get(codigo, 0) for codigo in (519, 520, 511)})
            if not self._sumar_compras(compra, sumar):
                for codigo in (519, 520):
                    res.pop(codigo, None)
                    aportes.pop(codigo, None)
                    motivos[codigo] = _(
                        "Hay facturas con IVA de uso común y el libro de compras no tiene factor de proporcionalidad")
            remanente, origen = self._remanente_anterior(sii.get(504, 0))
            sumar(504, remanente, {"descripcion": origen})
        else:
            for codigo in COMPRAS_CODIGOS_F29:
                motivos[codigo] = _("El RCV de compras del período no está conciliado completo")
        res[115] = sii.get(115, 0)
        res[68] = sii.get(68, 0)
        res.update(_totales_f29(res, compras=self.compras_completo and 520 in res))
        res.pop(115)
        res.pop(68)
        redondeados = {codigo: round(valor) for codigo, valor in res.items()}
        for codigo, valor in redondeados.items():
            if codigo in TOTALES_F29:
                continue
            residuo = valor - sum(a["monto"] for a in aportes.get(codigo, []))
            if abs(residuo) >= 0.005:
                aportes.setdefault(codigo, []).append({"descripcion": _("Redondeo"), "monto": residuo})
        return redondeados, aportes, motivos

    def _sumar_retenciones_honorarios(self, sumar):
        desde, hasta = self._rango()
        boletas = self.env["account.move"].search([
            ("company_id", "=", self.company_id.id),
            ("move_type", "in", ["in_invoice", "in_refund"]),
            ("state", "=", "posted"),
            ("document_class_id.sii_code", "in", [70, 71]),
            ("invoice_date", ">=", desde),
            ("invoice_date", "<=", hasta),
        ], order="invoice_date, id")
        for m in boletas:
            retencion = -sum(m.line_ids.filtered(
                lambda l: l.tax_line_id and l.tax_line_id.amount < 0).mapped("balance"))
            if not retencion:
                continue
            origen = _origen_documento(m, {"neto": abs(m.amount_untaxed_signed), "iva": 0})
            sumar(151, retencion, origen)

    def _sumar_compras(self, compra, sumar):
        uso_comun = []
        for m in compra._get_moves():
            codigo = m.document_class_id.sii_code
            totales = m.totales_por_movimiento()
            origen = _origen_documento(m, totales)
            impuestos = _impuestos_movimiento(m)
            ivas = {tax: monto for tax, monto in impuestos.items() if tax.sii_code == 14}
            no_recuperable = m.no_rec_code or any(tax.no_rec for tax in ivas)
            activo_fijo = any(tax.activo_fijo for tax in ivas)
            iva = sum(ivas.values())
            if m.document_class_id.dte and codigo in (33, 46, 56, 61):
                sumar(514 if no_recuperable else 511, -iva if codigo == 61 else iva, origen)
            if codigo in (33, 30, 46):
                if no_recuperable:
                    sumar(564, 1, origen)
                    sumar(521, totales["neto"], origen)
                elif activo_fijo:
                    sumar(524, 1, origen)
                    sumar(525, iva, origen)
                elif m.iva_uso_comun:
                    sumar(519, 1, origen)
                    uso_comun.append((origen, iva))
                else:
                    sumar(519, 1, origen)
                    sumar(520, iva, origen)
            elif codigo in COMPRAS_F29 and not no_recuperable:
                cantidad, monto = COMPRAS_F29[codigo]
                sumar(cantidad, 1, origen)
                sumar(monto, iva, origen)
            for tax, monto in impuestos.items():
                if tax.sii_code in ILA_COMPRAS_F29 and _con_credito(tax):
                    sumar({56: 551, 61: 559}.get(codigo, ILA_COMPRAS_F29[tax.sii_code]), monto,
                          dict(origen, descripcion="%s %s" % (origen["descripcion"], tax.name)))
        if uso_comun:
            if not compra.fact_prop:
                return False
            for origen, iva in uso_comun:
                sumar(520, iva * compra.fact_prop, dict(
                    origen, descripcion=_("%s: IVA uso común %s × factor %s") % (
                        origen["descripcion"], iva, compra.fact_prop)))
        return True

    def _periodo_anterior(self):
        desde, _hasta = self._rango()
        return self.search([
            ("company_id", "=", self.company_id.id),
            ("periodo", "=", (desde - timedelta(days=1)).strftime("%Y%m")),
        ], limit=1)

    def _utm(self, fecha):
        utm = self.env["res.currency"].with_context(active_test=False).search([("name", "=", "UTM")], limit=1)
        inicio = fecha.replace(day=1)
        fin = (inicio + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        tasa = self.env["res.currency.rate"].sudo().search([
            ("currency_id", "=", utm.id),
            ("name", ">=", inicio),
            ("name", "<=", fin),
            ("company_id", "in", [self.company_id.id, False]),
        ], order="name desc", limit=1)
        return round(1 / tasa.rate) if tasa.rate else 0

    def _reajuste_remanente(self, remanente):
        desde, _hasta = self._rango()
        actual, anterior = self._utm(desde), self._utm(desde - timedelta(days=1))
        if not actual or not anterior:
            return False, _("sin UTM del mes actual o del anterior para reajustar")
        return round(remanente * actual / anterior), _("reajustado por UTM %s / %s") % (
            _formato(actual), _formato(anterior))

    def _remanente_anterior(self, valor_sii):
        anterior = self._periodo_anterior()
        linea = anterior.f29_linea_ids.filtered(lambda l: l.codigo == 77 and l.calculado_odoo)[:1]
        if not linea:
            return valor_sii, _("Código 504 informado por el SII (no hay Declaración anterior en Odoo)")
        reajustado, detalle = self._reajuste_remanente(linea.valor_odoo)
        origen = _("Código 77 calculado por Odoo en la Declaración %s") % anterior.name
        if reajustado is not False:
            return reajustado, "%s, %s" % (origen, detalle)
        if abs(valor_sii - linea.valor_odoo) <= linea.valor_odoo * TOLERANCIA_REAJUSTE_UTM + 1:
            return valor_sii, _("%s (%s); el 504 del SII está dentro de la tolerancia del reajuste") % (origen, detalle)
        return linea.valor_odoo, "%s (%s)" % (origen, detalle)

    def _consultar_rcv(self, libro):
        data = self._trafquin("/api/rcv/documentos", {"libro": libro}).get("data") or {}
        Linea = self.env["sii.revision.rcv.linea"]
        Linea.search([("revision_id", "=", self.id), ("libro", "=", libro)]).unlink()
        lineas = Linea.create([
            dict(Linea._valores_sii(d), revision_id=self.id, libro=libro) for d in data.get("documentos") or []
        ])
        lineas._cruzar()
        if libro == "compras":
            lineas._crear_predocumento()
            lineas._vincular_factura()
            book = self._libro("COMPRA")
            movimientos = self._movimientos_rcv(libro)
            if data.get("completo") and book.sii_result in (False, "draft"):
                book.write({"move_ids": [(6, 0, lineas.filtered(
                    lambda l: l.estado_sii in ESTADOS_LIBRO_COMPRAS).move_id.ids)]})
                book.compute_taxes()
        else:
            movimientos = self._libro("VENTA")._get_moves()
        Linea.create([dict(Linea._valores_odoo(m), revision_id=self.id, libro=libro)
                      for m in movimientos - lineas.move_id])
        avisos = ["%s: %s" % (k, (v or {}).get("message") if isinstance(v, dict) else v)
                  for k, v in (data.get("errores") or {}).items()]
        avisos += ["%s: %s" % (k, v) for k, v in (data.get("mensajes") or {}).items()]
        avisos += [str(a) for a in data.get("advertencias") or []]
        if not data.get("completo"):
            avisos.insert(0, _("El SII no entregó el libro completo: el período no queda conciliado"))
        self.write({
            "%s_completo" % libro: bool(data.get("completo")),
            "%s_avisos" % libro: "\n".join(avisos) or False,
        })
        if libro == "compras":
            observados = data.get("proveedores_observados")
            self.compras_observados = json.dumps(observados) if observados is not None else False

    def _movimientos_rcv(self, libro):
        desde, hasta = self._rango()
        tipos = ["in_invoice", "in_refund"] if libro == "compras" else ["out_invoice", "out_refund"]
        return self.env["account.move"].search([
            ("company_id", "=", self.company_id.id),
            ("move_type", "in", tipos),
            ("state", "=", "posted"),
            ("date", ">=", desde),
            ("date", "<=", hasta),
            ("document_class_id.dte", "=", True),
            ("document_class_id.sii_code", "not in", list(TIPOS_BOLETA)),
            ("sii_document_number", "!=", False),
        ])

    def _consultar_boletas(self, actualizar=False):
        datos = {"libro": "ventas_boletas"}
        if actualizar:
            datos["actualizar"] = True
        respuesta = self._trafquin("/api/rcv/documentos", datos)
        data = respuesta.get("data") if isinstance(respuesta.get("data"), dict) else respuesta
        if data.get("listo") is False:
            raise UserError(_(
                "El SII está preparando el archivo de boletas: vuelva a consultar en %s segundos"
            ) % (data.get("reintentar_en_s") or 60))
        avisos_diarias = self._guardar_boletas_diarias(data.get("resumen_tipos") or [])
        Linea = self.env["sii.revision.boleta.linea"]
        valores = {}
        for d in data.get("dias") or []:
            clave = (_fecha(d.get("fecha")), int(d.get("tipo_dte") or 0))
            valores[clave] = dict(Linea._valores_sii(d), fecha=clave[0], tipo_dte=clave[1])
        for clave, sii in self._boletas_diarias_por_dia().items():
            valores.setdefault(clave, {"fecha": clave[0], "tipo_dte": clave[1]}).update(sii)
        for fila in data.get("resumen_tipos") or []:
            if int(fila.get("tipo_dte") or 0) == 48:
                valores[(False, 48)] = Linea._valores_resumen(fila)
        for clave, odoo in self._boletas_odoo().items():
            linea = valores.setdefault(clave, {"fecha": clave[0], "tipo_dte": clave[1]})
            linea.update(odoo)
        self.boleta_linea_ids.unlink()
        Linea.create([dict(v, revision_id=self.id) for v in valores.values()])
        avisos = [str(a) for a in data.get("advertencias") or []] + avisos_diarias
        self.write({"boletas_completo": True, "boletas_avisos": "\n".join(avisos) or False})

    def _consumos_folios(self):
        desde, hasta = self._rango()
        return self.env["account.move.consumo_folios"].search([
            ("company_id", "=", self.company_id.id),
            ("sii_result", "not in", ESTADOS_CF_EXCLUIDOS),
            ("fecha_inicio", ">=", desde),
            ("fecha_inicio", "<=", hasta),
        ])

    def _boletas_odoo(self):
        res = {}

        def linea(clave):
            return res.setdefault(clave, {"cantidad_odoo": 0, "odoo_iva": 0.0, "odoo_total": 0.0})

        for cf in self._consumos_folios():
            for det in cf.detalles.filtered(lambda d: d.tipo_operacion != "anulados"):
                if det.tpo_doc.sii_code in TIPOS_BOLETA:
                    linea((cf.fecha_inicio, det.tpo_doc.sii_code))["cantidad_odoo"] += det.cantidad
            for imp in cf.impuestos:
                if imp.tpo_doc.sii_code in TIPOS_BOLETA:
                    valores = linea((cf.fecha_inicio, imp.tpo_doc.sii_code))
                    valores["odoo_iva"] += imp.monto_iva
                    valores["odoo_total"] += imp.monto_total
        venta = self._libro("VENTA")
        for b in venta.boletas.filtered(lambda b: b.tipo_boleta.sii_code == 48):
            valores = linea((False, 48))
            valores["cantidad_odoo"] += b.cantidad_boletas
            valores["odoo_iva"] += b.monto_impuesto
            valores["odoo_total"] += b.monto_total
        return res

    def _url_propuesta_sii(self):
        return URL_INGRESO_SII % URL_PROPUESTA_F29

    def _ingreso_automatico(self):
        company = self.company_id
        return self.env.user.has_group("base.group_system") and bool(company.sudo().sii_clave_tributaria) \
            and not company._clave_sii_rechazada()

    def action_abrir_propuesta_sii(self):
        self.ensure_one()
        url = self._url_propuesta_sii()
        if self._ingreso_automatico():
            token = secrets.token_urlsafe(32)
            self.sudo().write({
                "sii_ingreso_huella": hashlib.sha256(token.encode()).hexdigest(),
                "sii_ingreso_vence": fields.Datetime.now() + timedelta(seconds=VIGENCIA_INGRESO_SII),
            })
            url = "/l10n_cl_fe/sii/propuesta_f29/%s/%s" % (self.id, token)
        return {"type": "ir.actions.act_url", "url": url, "target": "new"}

    def _formulario_ingreso_sii(self, token):
        self.ensure_one()
        registro = self.sudo()
        huella, vence = registro.sii_ingreso_huella, registro.sii_ingreso_vence
        registro.write({"sii_ingreso_huella": False, "sii_ingreso_vence": False})
        valido = huella and vence and vence > fields.Datetime.now() and hmac.compare_digest(
            huella, hashlib.sha256((token or "").encode()).hexdigest())
        if not valido or not self._ingreso_automatico():
            return False
        cuerpo, dv = self.company_id._rut_ingreso_sii().upper().split("-")
        return {
            "rut": cuerpo,
            "dv": dv,
            "referencia": URL_PROPUESTA_F29,
            "411": "",
            "rutcntr": "{:,}".format(int(cuerpo)).replace(",", ".") + "-" + dv,
            "clave": self.company_id.sudo().sii_clave_tributaria,
        }

    def action_traer_xml(self):
        self.ensure_one()
        if self.sistema_xml == "propio":
            raise UserError(_(XML_NO_MIPYME))
        lineas = self.compras_linea_ids._xml_descargable()
        if not lineas:
            raise UserError(_("No hay compras sin XML por descargar en este período"))
        lineas.action_descargar_xml()
        self.compras_avisos = _("XML descargados: %s de %s. El resultado de cada documento queda en su línea") % (
            len(lineas.filtered(lambda l: l.xml_estado == "descargado")), len(lineas))

    def action_llevar_f29(self):
        self.ensure_one()
        if not self.f29_linea_ids._ajustadas():
            raise UserError(_("No hay ajustes con valor propuesto y motivo"))
        return {
            "type": "ir.actions.act_window",
            "name": _("Llevar ajustes de la propuesta al SII"),
            "res_model": "sii.revision.f29.enviar",
            "view_mode": "form",
            "target": "new",
            "context": {"default_revision_id": self.id},
        }

    def _llevar_f29(self):
        codigos = [{"codigo": l.codigo, "valor": l.valor_propuesto, "motivo": l.motivo}
                   for l in self.f29_linea_ids._ajustadas()]
        try:
            self._trafquin("/api/f29/guardar", {"confirmar": True, "codigos": codigos}, clave=True)
        except UserError as e:
            mensaje = e.args[0] if e.args else str(e)
            if "CLAVE_RECHAZADA" in mensaje and self.company_id._marcar_clave_sii_rechazada():
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "type": "danger",
                        "sticky": True,
                        "message": _("La clave SII de %s fue rechazada: revísela en Ajustes") % self.company_id.name,
                        "next": {"type": "ir.actions.act_window_close"},
                    },
                }
            if "F29_GUARDADO_DESHABILITADO" in mensaje:
                raise UserError(_(
                    "Guardar la propuesta en el SII aún no está disponible. Los ajustes quedan como borrador en Odoo."
                ))
            raise

    @api.model
    def _companias_trafquin(self):
        return self.env["res.company"].search([
            ("url_trafquin", "!=", False),
            ("token_trafquin", "!=", False),
            ("dte_service_provider", "=", "SII"),
        ])

    @api.model
    def _revision_de(self, company, periodo):
        revision = self.search([("company_id", "=", company.id), ("periodo", "=", periodo)])
        if revision:
            return revision
        inicio = self._inicio_actividades(company)
        if inicio and periodo < inicio.strftime("%Y%m"):
            return revision
        return self.create({"company_id": company.id, "periodo": periodo})

    @api.model
    def _avisar_errores(self, revision, pestanas):
        for pestana in pestanas:
            if revision["%s_error" % pestana]:
                _logger.warning("Declaración Mensual F29 %s %s %s: %s", revision.company_id.name,
                                revision.periodo, pestana, revision["%s_error" % pestana])

    @api.model
    def _cron_revision_mensual(self):
        periodo = _periodo_anterior(fields.Date.context_today(self))
        for company in self._companias_trafquin():
            revision = self._revision_de(company, periodo)
            if not revision:
                continue
            revision.action_consultar_todo()
            self._avisar_errores(revision, ("f29",) + PESTANAS)

    @api.model
    def _cron_rcv_diario(self, periodo=False):
        periodo = periodo or fields.Date.context_today(self).strftime("%Y%m")
        for company in self._companias_trafquin():
            revision = self._revision_de(company, periodo)
            if not revision:
                continue
            revision.action_consultar_rcv()
            self._avisar_errores(revision, ("compras", "ventas"))
            if company.modelo_emision_boleta == "voucher" and company.medios_de_pago_electronico:
                desde, hasta = revision._rango()
                self.env["sii.boleta.diaria"]._conciliar_48(company, (desde - timedelta(days=1)).replace(day=1), hasta)
        self._actualizar_vencidos()


class SiiRevisionF29Linea(models.Model):
    _name = "sii.revision.f29.linea"
    _description = "Código de la Declaración Mensual F29"
    _order = "administrativo, orden, codigo, id"

    revision_id = fields.Many2one("sii.revision.periodo", required=True, ondelete="cascade")
    orden = fields.Integer(readonly=True)
    columna = fields.Char(readonly=True)
    codigo = fields.Integer(readonly=True)
    valor_sii_texto = fields.Char(compute="_compute_valor_sii_texto", string="Valor SII (texto)")
    explicacion = fields.Char(compute="_compute_explicacion")
    formula = fields.Char(readonly=True)
    motivo_no_calculado = fields.Char(string="Motivo", readonly=True)
    aporte_ids = fields.One2many("sii.revision.f29.aporte", "linea_id", string="Detalle")
    aporte_odoo_ids = fields.One2many("sii.revision.f29.aporte", "linea_id", domain=[("origen", "=", "odoo")],
                                      string="Cálculo de Odoo")
    aporte_sii_ids = fields.One2many("sii.revision.f29.aporte", "linea_id", domain=[("origen", "=", "sii")],
                                     string="Respaldo del SII")
    aporte_diferencia_ids = fields.One2many("sii.revision.f29.aporte", "linea_id",
                                            domain=[("origen", "=", "diferencia")], string="Diferencia explicada")
    glosa = fields.Char(readonly=True)
    signo = fields.Char(readonly=True)
    grupo = fields.Char(readonly=True)
    administrativo = fields.Boolean(readonly=True)
    valor_sii = fields.Float(string="Valor SII", readonly=True)
    sii_calculado = fields.Boolean(
        string="Calculado desde la propuesta", readonly=True,
        help="El SII no informa este total: se calcula con la fórmula del formulario desde los códigos que sí informa",
    )
    calculado_odoo = fields.Boolean(readonly=True)
    valor_odoo = fields.Float(string="Valor Odoo", readonly=True)
    diferencia = fields.Float(compute="_compute_diferencia", store=True)
    valor_propuesto = fields.Float(string="Valor propuesto")
    motivo = fields.Char(help="Motivo del cambio que se informará al SII")
    ajustable = fields.Boolean(
        compute="_compute_ajustable",
        help="Solo los códigos del registro diario de boletas se pueden ajustar en la propuesta del SII",
    )
    ajuste_uid = fields.Many2one("res.users", string="Ajustado por", readonly=True)
    ajuste_fecha = fields.Datetime(string="Fecha del ajuste", readonly=True)

    @api.depends("valor_sii", "valor_odoo", "calculado_odoo")
    def _compute_diferencia(self):
        for r in self:
            r.diferencia = r.valor_sii - r.valor_odoo if r.calculado_odoo else 0

    @api.depends("valor_sii")
    def _compute_valor_sii_texto(self):
        for r in self:
            r.valor_sii_texto = _formato(r.valor_sii)

    @api.depends("diferencia", "codigo", "revision_id.compras_pendientes_iva")
    def _compute_explicacion(self):
        for r in self:
            pendiente = r.revision_id.compras_pendientes_iva
            r.explicacion = _("IVA de compras del RCV sin factura en Odoo") if (
                r.codigo in (511, 520, 537) and r.calculado_odoo and pendiente and abs(r.diferencia - pendiente) <= 1
            ) else False

    @api.depends("codigo")
    def _compute_ajustable(self):
        for r in self:
            r.ajustable = r.codigo in AJUSTABLES_F29

    @api.onchange("valor_propuesto", "motivo")
    def _onchange_ajuste(self):
        self.ajuste_uid = self.env.user
        self.ajuste_fecha = fields.Datetime.now()

    def _ajustadas(self):
        return self.filtered(lambda l: l.ajustable and l.motivo and l.valor_propuesto != l.valor_sii)

    def action_detalle(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Código %s") % self.codigo,
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "views": [(self.env.ref("l10n_cl_fe.view_sii_revision_f29_linea_form").id, "form")],
            "target": "new",
        }

    def action_usar_odoo(self):
        for r in self.filtered(lambda l: l.calculado_odoo and l.ajustable):
            r.write({
                "valor_propuesto": r.valor_odoo,
                "ajuste_uid": self.env.uid,
                "ajuste_fecha": fields.Datetime.now(),
            })


class SiiRevisionF29Aporte(models.Model):
    _name = "sii.revision.f29.aporte"
    _description = "Documento que aporta a un código de la Declaración Mensual F29"
    _order = "origen, tipo_dte, folio, id"

    linea_id = fields.Many2one("sii.revision.f29.linea", required=True, ondelete="cascade")
    origen = fields.Selection(
        [("odoo", "Cálculo de Odoo"), ("sii", "Respaldo del SII"), ("diferencia", "Diferencia")], required=True,
    )
    descripcion = fields.Char(string="Detalle")
    tipo_dte = fields.Integer(string="Tipo")
    folio = fields.Char()
    rut = fields.Char(string="RUT")
    fecha = fields.Date()
    neto = fields.Float()
    iva = fields.Float(string="IVA")
    monto = fields.Float(string="Aporte")
    move_id = fields.Many2one("account.move", string="Factura", ondelete="set null")
    boleta_id = fields.Many2one("account.move.book.boletas", string="Línea del libro", ondelete="set null")
    rcv_linea_id = fields.Many2one("sii.revision.rcv.linea", string="Documento del RCV", ondelete="set null")
    sii_boleta_id = fields.Many2one("sii.revision.boleta.linea", string="Boletas del RCV", ondelete="set null")
    componente_id = fields.Many2one("sii.revision.f29.linea", string="Código que suma", ondelete="set null")
    estado_cruce = fields.Selection(related="rcv_linea_id.estado", string="Cruce")
    xml_resultado = fields.Char(related="rcv_linea_id.xml_resultado", string="Resultado XML")
    puede_descargar = fields.Boolean(related="rcv_linea_id.puede_descargar")
    sin_descarga = fields.Boolean(related="rcv_linea_id.sin_descarga")
    xml_pedido_fecha = fields.Datetime(related="rcv_linea_id.xml_pedido_fecha")

    def action_descargar_xml(self):
        return self.rcv_linea_id.action_descargar_xml()

    def action_registrar_sin_xml(self):
        return self.rcv_linea_id.action_registrar_sin_xml()

    def action_pedir_xml(self):
        return self.rcv_linea_id.action_pedir_xml()


class SiiRevisionF29Declaracion(models.Model):
    _name = "sii.revision.f29.declaracion"
    _description = "Declaración de F29 en el SII"
    _order = "fecha desc, id"

    revision_id = fields.Many2one("sii.revision.periodo", required=True, ondelete="cascade")
    estado = fields.Char(readonly=True)
    folio = fields.Char(readonly=True)
    fecha = fields.Date(readonly=True)
    total = fields.Float(readonly=True)
    en_negocio = fields.Boolean(readonly=True)


class SiiRevisionRcvLinea(models.Model):
    _name = "sii.revision.rcv.linea"
    _description = "Documento del Registro de Compras y Ventas"
    _order = "fecha, tipo_dte, folio"

    revision_id = fields.Many2one("sii.revision.periodo", required=True, ondelete="cascade")
    libro = fields.Selection([("compras", "Compras"), ("ventas", "Ventas")], required=True)
    estado_sii = fields.Char(string="Estado en el SII")
    tipo_dte = fields.Integer(string="Tipo")
    document_class_id = fields.Many2one("sii.document_class", string="Documento")
    folio = fields.Char()
    rut = fields.Char(string="RUT")
    razon_social = fields.Char(string="Razón social")
    fecha = fields.Date()
    exento = fields.Float()
    neto = fields.Float()
    iva = fields.Float(string="IVA SII")
    total = fields.Float(string="Total SII")
    odoo_neto = fields.Float(string="Neto Odoo")
    odoo_iva = fields.Float(string="IVA Odoo")
    odoo_total = fields.Float(string="Total Odoo")
    diferencia = fields.Float(compute="_compute_diferencia", store=True)
    estado = fields.Selection(ESTADOS_CRUCE, string="Cruce")
    move_id = fields.Many2one("account.move", string="Factura")
    document_id = fields.Many2one("mail.message.dte.document", string="Documento recibido")
    xml_estado = fields.Selection(
        [("descargado", "XML descargado"), ("no_disponible", "XML no disponible en el SII"), ("error", "Error")],
        string="Descarga XML", readonly=True,
    )
    xml_resultado = fields.Char(string="Resultado XML", readonly=True)
    xml_pedido_fecha = fields.Datetime(string="XML pedido al proveedor", readonly=True)
    puede_descargar = fields.Boolean(compute="_compute_acciones_xml")
    sin_descarga = fields.Boolean(compute="_compute_acciones_xml")

    @api.depends("estado", "libro", "move_id", "xml_estado", "revision_id.sistema_xml")
    def _compute_acciones_xml(self):
        for r in self:
            falta = r.estado in ("falta_odoo", "sin_xml") and not r.move_id
            propio = r.revision_id.sistema_xml == "propio"
            r.puede_descargar = falta and not propio and r.xml_estado != "no_disponible"
            r.sin_descarga = falta and r.libro == "compras" and (propio or r.xml_estado == "no_disponible")

    @api.depends("total", "odoo_total")
    def _compute_diferencia(self):
        for r in self:
            r.diferencia = r.total - r.odoo_total

    @api.model
    def _valores_sii(self, d):
        tipo = int(d.get("tipo_dte") or 0)
        return {
            "estado_sii": d.get("estado_sii"),
            "tipo_dte": tipo,
            "document_class_id": self.env["sii.document_class"].search([("sii_code", "=", tipo)], limit=1).id,
            "folio": str(d.get("folio") or ""),
            "rut": d.get("rut") or "",
            "razon_social": d.get("razon_social"),
            "fecha": _fecha(d.get("fecha_emision")),
            "exento": float(d.get("monto_exento") or 0),
            "neto": float(d.get("monto_neto") or 0),
            "iva": float(d.get("monto_iva") or 0),
            "total": float(d.get("monto_total") or 0),
        }

    @api.model
    def _valores_odoo(self, move):
        partner = move.commercial_partner_id
        return {
            "tipo_dte": move.document_class_id.sii_code,
            "document_class_id": move.document_class_id.id,
            "folio": str(move.sii_document_number),
            "rut": partner.document_number or "",
            "razon_social": partner.name,
            "fecha": move.invoice_date,
            "odoo_neto": move.totales_por_movimiento()["neto"],
            "odoo_iva": move.totales_por_movimiento()["iva"],
            "odoo_total": abs(move.amount_total_signed),
            "estado": "falta_sii",
            "move_id": move.id,
        }

    def _cruzar(self):
        Partner = self.env["res.partner"]
        for r in self.filtered(lambda l: l.estado != "falta_sii"):
            compras = r.libro == "compras"
            company = r.revision_id.company_id
            dominio = [
                ("company_id", "=", company.id),
                ("move_type", "in", ["in_invoice", "in_refund"] if compras else ["out_invoice", "out_refund"]),
                ("document_class_id.sii_code", "=", r.tipo_dte),
                ("sii_document_number", "=", int(r.folio or 0)),
                ("state", "!=", "cancel"),
            ]
            vats = Partner._vats_busqueda(r.rut) if r.rut else []
            if compras:
                dominio.append(("commercial_partner_id.vat", "in", vats))
            move = self.env["account.move"].search(dominio, limit=1)
            documento = self.env["mail.message.dte.document"]
            if compras:
                documento = documento.search([
                    ("company_id", "=", company.id),
                    ("number", "=", r.folio),
                    ("document_class_id.sii_code", "=", r.tipo_dte),
                    "|", ("partner_id.vat", "in", vats), ("new_partner", "=like", "%s %%" % r.rut),
                ], limit=1)
                move = move or documento.move_id
            valores = {"move_id": move.id, "document_id": documento.id, "odoo_neto": 0, "odoo_iva": 0, "odoo_total": 0}
            if move:
                totales = move.totales_por_movimiento()
                valores.update(odoo_neto=totales["neto"], odoo_iva=totales["iva"],
                               odoo_total=abs(move.amount_total_signed))
                iguales = all(abs(a - b) < 1 for a, b in (
                    (valores["odoo_total"], r.total), (valores["odoo_iva"], r.iva), (valores["odoo_neto"], r.neto)))
                valores["estado"] = "coincide" if iguales else "diferencia"
            elif documento.source == "rcv":
                valores["estado"] = "sin_xml"
            else:
                valores["estado"] = "recibido" if documento else "falta_odoo"
            r.write(valores)

    def _vincular_factura(self):
        lineas = self.filtered(lambda l: l.move_id and l.document_id and not l.document_id.move_id)
        for r in lineas:
            r.document_id.move_id = r.move_id
        return lineas

    def _crear_predocumento(self):
        Partner = self.env["res.partner"]
        lineas = self.filtered(lambda l: l.estado == "falta_odoo" and l.libro == "compras"
                               and l.estado_sii != "RECLAMADO" and l.document_class_id and l.folio)
        for r in lineas:
            company = r.revision_id.company_id
            partner = Partner._buscar_por_vat(r.rut, [("parent_id", "=", False)]) if r.rut else Partner
            documento = self.env["mail.message.dte.document"].with_company(company).create({
                "source": "rcv",
                "company_id": company.id,
                "document_class_id": r.document_class_id.id,
                "number": r.folio,
                "date": r.fecha,
                "amount": r.total,
                "partner_id": partner.id,
                "new_partner": "%s %s" % (r.rut, r.razon_social or ""),
            })
            r.write({"document_id": documento.id, "estado": "sin_xml"})
        return lineas

    def _xml_descargable(self):
        return self.filtered(lambda l: l.puede_descargar and l.tipo_dte and (l.folio or "").isdigit())

    def _xml_del_sii(self):
        Wizard = self.env["sii.dte.trafquin.wizard"]
        Partner = self.env["res.partner"]
        compras = self.libro == "compras"
        company = self.revision_id.company_id
        Wizard._exigir_produccion(company)
        datos = {"folio": int(self.folio), "tipo_doc": self.tipo_dte, "formato": "json", "incluir_xml": True}
        if compras and self.rut:
            datos["rut_contraparte"] = self.rut
        data = Wizard._llamar_trafquin(company, "/api/xml/%s" % ("recibidos" if compras else "emitidos"), datos)
        company._informar_mipyme(data.get("mipyme"))
        rut = Partner._rut_clave(self.rut) if compras else ""
        for doc in data.get("documentos") or []:
            mismo = (int(doc.get("tipo_dte") or 0), int(doc.get("folio") or 0)) == (self.tipo_dte, int(self.folio))
            if mismo and doc.get("xml_b64") and (not compras or Partner._rut_clave(doc.get("rut_emisor") or "") == rut):
                return doc, []
        return False, Wizard._avisos(data)

    def _descargar_xml(self):
        Wizard = self.env["sii.dte.trafquin.wizard"]
        origen = "recibidos" if self.libro == "compras" else "emitidos"
        try:
            with self.env.cr.savepoint():
                doc, avisos = self._xml_del_sii()
                if not doc:
                    return "no_disponible", "\n".join([_(XML_NO_EN_SII)] + avisos)
                estado, _registro, mensaje = Wizard._registrar_documento(self.revision_id.company_id, origen, doc)
                if estado == "error":
                    raise UserError(mensaje)
        except UserError as e:
            datos = getattr(e, "datos", {})
            if datos.get("codigo") != "XML_NO_DISPONIBLE":
                return "error", str(e)
            if datos.get("mipyme") is False:
                self.revision_id.company_id._informar_mipyme(False)
                return "no_disponible", _(XML_NO_MIPYME)
            return "no_disponible", "%s\n%s" % (_(XML_NO_EN_SII), datos.get("message") or "")
        if estado == "existente":
            return "descargado", _("El XML ya estaba registrado en Odoo")
        return "descargado", _("XML descargado y registrado")

    def action_descargar_xml(self):
        lineas = self._xml_descargable()
        if not lineas:
            raise UserError(_("No hay XML por descargar para este documento"))
        for r in lineas:
            estado, resultado = r._descargar_xml()
            r.write({"xml_estado": estado, "xml_resultado": resultado})
        lineas._cruzar()
        lineas._vincular_factura()

    def _proveedor(self):
        Partner = self.env["res.partner"]
        partner = self.document_id.partner_id.commercial_partner_id or (
            Partner._buscar_por_vat(self.rut, [("parent_id", "=", False)]) if self.rut else Partner)
        if partner or not self.rut:
            return partner
        return Partner.create({
            "name": self.razon_social or self.rut,
            "vat": Partner._vats_busqueda(self.rut)[0],
            "document_number": self.rut,
            "document_type_id": self.env.ref("l10n_cl_fe.dt_RUT").id,
            "responsability_id": self.env.ref("l10n_cl_fe.res_IVARI").id,
            "company_type": "company",
        })

    def _valores_factura_rcv(self):
        company = self.revision_id.company_id
        Upload = self.env["sii.dte.upload_xml.wizard"].with_company(company)
        diario = self.env["account.journal"].search([
            ("company_id", "=", company.id),
            ("type", "=", "purchase"),
            ("document_class_ids", "=", self.document_class_id.id),
        ], limit=1)
        if not diario:
            raise UserError(_("No existe un diario de compras para %s") % self.document_class_id.name)
        partner = self._proveedor()
        if not partner:
            raise UserError(_("El documento del RCV no trae el RUT del proveedor"))
        otros = round(self.total - self.exento - self.neto - self.iva, 2)
        lineas = []
        if self.neto:
            iva = self.env["account.tax"]
            if self.iva:
                iva = company.account_purchase_tax_id.filtered(lambda t: t.sii_code == 14 and t.amount == 19) or \
                    Upload._buscar_impuesto(type="purchase", amount=19, sii_code=14, company_id=company)
            lineas.append((_("Neto según el RCV"), self.neto, iva))
        if self.exento:
            exento = Upload._buscar_impuesto(type="purchase", amount=0, sii_code=0, company_id=company)
            lineas.append((_("Exento según el RCV"), self.exento, exento))
        if otros:
            lineas.append((_("Otros impuestos según el RCV"), otros, self.env["account.tax"]))
        return {
            "move_type": "in_refund" if self.document_class_id.es_nc() else "in_invoice",
            "company_id": company.id,
            "journal_id": diario.id,
            "partner_id": partner.id,
            "document_class_id": self.document_class_id.id,
            "sii_document_number": int(self.folio),
            "invoice_date": self.fecha,
            "date": self.fecha,
            "invoice_origin": _("RCV %s sin XML") % self.revision_id.periodo,
            "invoice_line_ids": [(0, 0, {
                "name": nombre, "quantity": 1, "price_unit": monto, "tax_ids": [(6, 0, impuestos.ids)],
            }) for nombre, monto, impuestos in lineas],
        }

    def action_registrar_sin_xml(self):
        self.ensure_one()
        if not self.sin_descarga:
            raise UserError(_("Este documento ya está en Odoo o su XML se puede descargar del SII"))
        move = self.env["account.move"].with_company(self.revision_id.company_id).create(self._valores_factura_rcv())
        if self.document_id and not self.document_id.move_id:
            self.document_id.move_id = move
        self._cruzar()
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": move.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_pedir_xml(self):
        plantilla = self.env.ref("l10n_cl_fe.mail_template_pedir_xml_rcv")
        for r in self:
            if not r.sin_descarga:
                raise UserError(_("El documento %s %s ya está en Odoo") % (r.document_class_id.name, r.folio))
            partner = r._proveedor()
            correo = partner.dte_email or partner.email
            if not correo:
                raise UserError(_("El proveedor %s no tiene correo DTE ni correo") % (partner.name or r.rut))
            plantilla.send_mail(r.id, email_values={"email_to": correo, "recipient_ids": []})
            r.xml_pedido_fecha = fields.Datetime.now()

    def action_abrir(self):
        self.ensure_one()
        registro = self.move_id or self.document_id
        if not registro:
            raise UserError(_("El documento no está en Odoo"))
        return {
            "type": "ir.actions.act_window",
            "res_model": registro._name,
            "res_id": registro.id,
            "view_mode": "form",
            "target": "current",
        }


class SiiRevisionBoletaLinea(models.Model):
    _name = "sii.revision.boleta.linea"
    _description = "Día del libro de boletas"
    _order = "tipo_dte, fecha"

    revision_id = fields.Many2one("sii.revision.periodo", required=True, ondelete="cascade")
    fecha = fields.Date(help="Vacío en el tipo 48: el SII solo informa el total del mes")
    tipo_dte = fields.Integer(string="Tipo")
    cantidad_sii = fields.Integer(string="Cantidad SII")
    cantidad_odoo = fields.Integer(string="Cantidad Odoo")
    folio_desde = fields.Integer()
    folio_hasta = fields.Integer()
    exento = fields.Float()
    neto = fields.Float()
    iva = fields.Float(string="IVA SII")
    total = fields.Float(string="Total SII")
    odoo_iva = fields.Float(string="IVA Odoo")
    odoo_total = fields.Float(string="Total Odoo")
    diferencia = fields.Float(compute="_compute_diferencia", store=True)
    estado = fields.Selection(ESTADOS_CRUCE, string="Cruce", compute="_compute_diferencia", store=True)
    detalle_sii = fields.Text(string="Fila del SII")

    @api.depends("total", "odoo_total", "cantidad_sii", "cantidad_odoo", "iva", "odoo_iva")
    def _compute_diferencia(self):
        for r in self:
            r.diferencia = r.total - r.odoo_total
            if not (r.cantidad_sii or r.total or r.cantidad_odoo or r.odoo_total):
                r.estado = "coincide"
            elif not r.cantidad_sii and not r.total:
                r.estado = "falta_sii"
            elif not r.cantidad_odoo and not r.odoo_total:
                r.estado = "falta_odoo"
            elif abs(r.diferencia) < 1 and abs(r.iva - r.odoo_iva) < 1 and r.cantidad_sii == r.cantidad_odoo:
                r.estado = "coincide"
            else:
                r.estado = "diferencia"

    @api.model
    def _valores_sii(self, d):
        return {
            "cantidad_sii": int(d.get("cantidad") or 0),
            "folio_desde": int(d.get("folio_desde") or 0),
            "folio_hasta": int(d.get("folio_hasta") or 0),
            "exento": float(d.get("monto_exento") or 0),
            "neto": float(d.get("monto_neto") or 0),
            "iva": float(d.get("monto_iva") or 0),
            "total": float(d.get("monto_total") or 0),
        }

    @api.model
    def _valores_resumen(self, fila):
        sii = fila.get("sii") or {}

        def monto(*claves):
            for clave in claves:
                if sii.get(clave) is not None:
                    try:
                        return float(sii.get(clave))
                    except (TypeError, ValueError):
                        continue
            return 0.0

        return {
            "fecha": False,
            "tipo_dte": 48,
            "cantidad_sii": int(fila.get("cantidad") or 0),
            "exento": monto("rsmnMntExe", "monto_exento"),
            "neto": monto("rsmnMntNeto", "monto_neto"),
            "iva": monto("rsmnMntIVA", "monto_iva"),
            "total": monto("rsmnMntTotal", "monto_total"),
            "detalle_sii": json.dumps(sii, ensure_ascii=False, indent=1) if sii else False,
        }

    def action_ver_odoo(self):
        self.ensure_one()
        revision = self.revision_id
        if self.tipo_dte == 48:
            return {
                "type": "ir.actions.act_window",
                "name": _("Comprobantes de pago electrónico"),
                "res_model": "account.move.boleta_voucher",
                "view_mode": "list,form",
                "domain": [("company_id", "=", revision.company_id.id),
                           ("periodo", "=", revision._periodo_libro())],
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Consumo de folios del %s") % self.fecha,
            "res_model": "account.move.consumo_folios",
            "view_mode": "list,form",
            "domain": [("id", "in", revision._consumos_folios().filtered(
                lambda cf: cf.fecha_inicio == self.fecha).ids)],
        }


class SiiRevisionF29Enviar(models.TransientModel):
    _name = "sii.revision.f29.enviar"
    _description = "Confirmar ajustes de la Declaración Mensual F29"

    revision_id = fields.Many2one("sii.revision.periodo", required=True)
    resumen = fields.Text(compute="_compute_resumen")

    @api.depends("revision_id")
    def _compute_resumen(self):
        for r in self:
            r.resumen = "\n".join(
                "%s %s: %s → %s (%s)" % (l.codigo, l.glosa or "", l.valor_sii, l.valor_propuesto, l.motivo)
                for l in r.revision_id.f29_linea_ids._ajustadas()
            )

    def action_confirmar(self):
        self.ensure_one()
        return self.revision_id._llevar_f29() or {"type": "ir.actions.act_window_close"}
