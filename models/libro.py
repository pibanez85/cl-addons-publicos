import logging
from datetime import datetime

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.translate import _

_logger = logging.getLogger(__name__)

try:
    from facturacion_electronica import facturacion_electronica as fe
except Exception as e:
    _logger.warning("Problema al cargar Facturación electrónica: %s" % str(e))

allowed_docs = [
    29,
    30,
    32,
    33,
    34,
    35,
    38,
    39,
    40,
    41,
    43,
    45,
    46,
    48,
    53,
    55,
    56,
    60,
    61,
    101,
    102,
    103,
    104,
    105,
    106,
    108,
    109,
    110,
    111,
    112,
    175,
    180,
    185,
    900,
    901,
    902,
    903,
    904,
    905,
    906,
    907,
    909,
    910,
    911,
    914,
    918,
    919,
    920,
    921,
    922,
    924,
    500,
    501,
]


class Libro(models.Model):
    _name = "account.move.book"
    _order = "periodo_tributario desc, id desc"
    _inherit = ['l10n_cl_fe.dte.book.mixin']
    _description = "Libro de Compra / Venta DTE"

    _dte_cola_field = 'book_ids'
    _dte_xml_envio_field = 'book_ids'
    _dte_label = "Libro"

    move_ids = fields.Many2many("account.move")

    tipo_libro = fields.Selection(
        [("ESPECIAL", "Especial"), ("MENSUAL", "Mensual"), ("RECTIFICA", "Rectifica"),],
        string="Tipo de Libro",
        default="MENSUAL",
        required=True,
    )
    tipo_operacion = fields.Selection(
        [("COMPRA", "Compras"), ("VENTA", "Ventas"), ("BOLETA", "Boleta Electrónica"),],
        string="Tipo de operación",
        required=True,
        default="COMPRA",
    )
    tipo_envio = fields.Selection(
        [("AJUSTE", "Ajuste"), ("TOTAL", "Total"), ("PARCIAL", "Parcial"), ("TOTAL", "Total"),],
        string="Tipo de Envío",
        default="TOTAL",
        required=True,
        readonly=True,

    )
    folio_notificacion = fields.Char(
        string="Folio de Notificación", readonly=True,
    )
    impuestos = fields.One2many("account.move.book.tax", "book_id", string="Detalle Impuestos",)
    currency_id = fields.Many2one(
        "res.currency",
        string="Moneda",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    total_afecto = fields.Monetary(string="Total Afecto", readonly=True, compute="set_resumen", store=True,)
    total_exento = fields.Monetary(string="Total Exento", readonly=True, compute="set_resumen", store=True,)
    total_iva = fields.Monetary(string="Total IVA", readonly=True, compute="set_resumen", store=True,)
    total_otros_imps = fields.Monetary(
        string="Total Otros Impuestos", readonly=True, compute="set_resumen", store=True,
    )
    total = fields.Monetary(string="Grand Total", readonly=True, compute="set_resumen", store=True,)
    periodo_tributario = fields.Char(
        string="Periodo Tributario",
        required=True,
        default=lambda *a: datetime.now().strftime("%Y-%m"),
    )
    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        required=True,
        default=lambda self: self.env.company.id,
        readonly=True,

    )
    name = fields.Char(string="Detalle", required=True, readonly=True, )
    fact_prop = fields.Float(string="Factor proporcionalidad", readonly=True, )
    nro_segmento = fields.Integer(string="Número de Segmento", readonly=True, )
    date = fields.Date(
        string="Fecha",
        required=True,
        readonly=True,
        default=lambda self: fields.Date.context_today(self),
    )
    boletas = fields.One2many(
        "account.move.book.boletas",
        "book_id",
        string="Boletas",
        readonly=True,

    )
    codigo_rectificacion = fields.Char(string="Código de Rectificación",)

    @api.onchange("periodo_tributario", "tipo_operacion", "company_id")
    def set_movimientos(self):
        if not self.periodo_tributario or not self.tipo_operacion:
            return
        current = datetime.strptime(self.periodo_tributario + "-01", "%Y-%m-%d")
        next_month = current + relativedelta(months=1)
        docs = [False, 70, 71, 35, 38, 39, 41]
        operator = "not in"
        query = [
            ("company_id", "=", self.company_id.id),
            ("invoice_date", "<", next_month.strftime("%Y-%m-%d")),
            ('sii_document_number', 'not in', [0, False])
        ]
        domain = "sale"
        if self.tipo_operacion in ["COMPRA"]:
            two_month = current + relativedelta(months=-2)
            query.append(("invoice_date", ">=", two_month.strftime("%Y-%m-%d")))
            domain = "purchase"
        elif self.tipo_operacion != "BOLETA":
            # en ventas no considerar las boletas
            query.append(("document_class_id.sii_code", "not in", (35, 38, 39, 41, 70, 71)))
        query.append(("journal_id.type", "=", domain))
        boleta_lines = [
            [5,],
        ]
        impuesto_lines = [
            [5,],
        ]
        if self.tipo_operacion in ["VENTA"]:
            cfs = self.env["account.move.consumo_folios"].search(
                [
                    ("sii_result", "not in", ["draft", "Anulado", "Rechazado"]),
                    ("fecha_inicio", ">=", current),
                    ("fecha_inicio", "<", next_month),
                ]
            )
            if cfs:
                cantidades = {}
                for cf in cfs:
                    for det in cf.detalles:
                        if det.tpo_doc.sii_code in [39, 41]:
                            if not cantidades.get((cf.id, det.tpo_doc)):
                                cantidades[(cf.id, det.tpo_doc)] = 0
                            cantidades[(cf.id, det.tpo_doc)] += det.cantidad
                lineas = {}
                for key, cantidad in cantidades.items():
                    cf = key[0]
                    tpo_doc = key[1]
                    impuesto = self.env["account.move.consumo_folios.impuestos"].search(
                        [("cf_id", "=", cf), ("tpo_doc.sii_code", "=", tpo_doc.sii_code)]
                    )
                    if not lineas.get(tpo_doc):
                        lineas[tpo_doc] = {
                            "cantidad": 0,
                            "neto": 0,
                            "monto_exento": 0,
                            "monto_impuesto": 0.0,
                            "monto_total": 0.0,
                        }
                    lineas[tpo_doc] = {
                        "cantidad": lineas[tpo_doc]["cantidad"] + cantidad,
                        "neto": lineas[tpo_doc]["neto"] + impuesto.monto_neto,
                        "monto_exento": lineas[tpo_doc]["monto_exento"] + impuesto.monto_exento,
                        "monto_impuesto": lineas[tpo_doc]["monto_impuesto"] + impuesto.monto_iva,
                        "monto_total": lineas[tpo_doc]["monto_total"] + impuesto.monto_total,
                    }
                for tpo_doc, det in lineas.items():
                    tax_id = (
                        self.env["account.tax"].search(
                            [
                                ("sii_code", "=", 14),
                                ("type_tax_use", "=", "sale"),
                                ("company_id", "=", self.company_id.id),
                            ],
                            limit=1,
                        )
                        if tpo_doc.sii_code == 39
                        else self.env["account.tax"].search(
                            [
                                ("sii_code", "=", 0),
                                ("type_tax_use", "=", "sale"),
                                ("company_id", "=", self.company_id.id),
                            ],
                            limit=1,
                        )
                    )
                    line = {
                        "currency_id": self.env.company.currency_id,
                        "tipo_boleta": tpo_doc.id,
                        "cantidad_boletas": det["cantidad"],
                        "neto": det["neto"] or det["monto_exento"],
                        "monto_impuesto": det["monto_impuesto"],
                        "monto_total": det["monto_total"],
                        "impuesto": tax_id.id,
                    }
                    boleta_lines.append([0, 0, line])
        elif self.tipo_operacion in ["BOLETA"]:
            docs = [35, 38, 39, 41]
            cfs = self.env["account.move.consumo_folios"].search(
                [
                    ("sii_result", "not in", ["draft", "Anulado", "Rechazado"]),
                    ("fecha_inicio", ">=", current),
                    ("fecha_inicio", "<", next_month),
                ]
            )
            monto_iva = 0
            monto_exento = 0
            for cf in cfs:
                for i in cf.impuestos:
                    monto_iva += i.monto_iva
                    monto_exento += i.monto_exento
            impuesto_lines.extend(
                [
                    [
                        0,
                        0,
                        {
                            "tax_id": self.env["account.tax"].search(
                                [
                                    ("sii_code", "=", 14),
                                    ("type_tax_use", "=", "sale"),
                                    ("company_id", "=", self.company_id.id),
                                ],
                                limit=1,
                            )
                            .id,
                            "credit": monto_iva,
                            "currency_id": self.env.company.currency_id.id,
                        },
                    ],
                    [
                        0,
                        0,
                        {
                            "tax_id": self.env["account.tax"].search(
                                [
                                    ("sii_code", "=", 0),
                                    ("type_tax_use", "=", "sale"),
                                    ("company_id", "=", self.company_id.id),
                                ],
                                limit=1,
                            )
                            .id,
                            "credit": monto_exento,
                            "currency_id": self.env.company.currency_id.id,
                        },
                    ],
                ]
            )
            operator = "in"
        if self.tipo_operacion in ["VENTA", "BOLETA"]:
            query.append(("invoice_date", ">=", current.strftime("%Y-%m-%d")))
        query.append(("document_class_id.sii_code", operator, docs))
        self.boletas = boleta_lines
        self.impuestos = impuesto_lines
        self.move_ids = self.env["account.move"].search(query)

    def _get_imps(self):
        imp = {}
        for move in self.move_ids:
            move_imps = move._get_move_imps()
            for key, i in move_imps.items():
                if key not in imp:
                    del i['code']
                    imp[key] = i
                else:
                    imp[key]["credit"] += i["credit"]
                    imp[key]["debit"] += i["debit"]
        return imp

    @api.onchange("move_ids")
    def set_resumen(self):
        for book in self:
            for mov in book._get_moves():
                totales = mov.totales_por_movimiento()
                book.total_afecto += totales["neto"]
                book.total_exento += totales["exento"]
                book.total_iva += totales["iva"]
                book.total_otros_imps += totales["otros_imps"]
                book.total += mov.amount_total

    @api.onchange("move_ids")
    def compute_taxes(self):
        if self.tipo_operacion not in ["BOLETA"]:
            imp = self._get_imps()
            if self.boletas:
                for bol in self.boletas:
                    if not imp.get(bol.impuesto.id):
                        imp[bol.impuesto.id] = {"credit": 0}
                    imp[bol.impuesto.id]["credit"] += bol.monto_impuesto
            if self.impuestos and isinstance(self.id, int):
                self.env.cr.execute("DELETE FROM account_move_book_tax WHERE book_id=%s", (self.id,))
                self.invalidate_recordset()
            lines = [
                [5,],
            ]
            for _key, i in imp.items():
                i["currency_id"] = self.env.company.currency_id.id
                lines.append([0, 0, i])
            self.impuestos = lines

    def unlink(self):
        for libro in self:
            if libro.sii_result not in ("draft", "cancel"):
                raise UserError(_("You cannot delete a Validated book."))
        return super(Libro, self).unlink()


    def _xml_download_path(self):
        return "libro"

    @api.onchange("periodo_tributario", "tipo_operacion")
    def _setName(self):
        self.name = self.tipo_operacion
        if self.periodo_tributario and self.name:
            self.name += " " + self.periodo_tributario

    def validar_libro(self):
        self._validar()
        return self.write({"sii_result": "NoEnviado"})

    def _get_moves(self):
        recs = self.with_context(lang="es_CL").move_ids.filtered(
            lambda a: a.is_invoice() and a.sii_document_number and\
            (not a.document_class_id.es_boleta() or (self.tipo_libro == 'BOLETA' and a.document_class_id.es_boleta()))
        )
        return recs

    def _validar(self):
        datos = self._get_datos_empresa()
        grupos = {}
        boletas = []
        recs = self._get_moves()
        for r in recs:
            grupos.setdefault(r.document_class_id.sii_code, [])
            grupos[r.document_class_id.sii_code].append(r.with_context(tax_detail=True)._dte())
        for b in self.boletas:
            boletas.append(b._dte())
        datos["Libro"] = {
            "PeriodoTributario": self.periodo_tributario,
            "TipoOperacion": self.tipo_operacion,
            "TipoLibro": self.tipo_libro,
            "FolioNotificacion": self.folio_notificacion,
            "TipoEnvio": self.tipo_envio,
            "CodigoRectificacion": self.codigo_rectificacion,
            "Documento": [{"TipoDTE": k, "documentos": v} for k, v in grupos.items()],
            "FctProp": self.fact_prop,
            "boletas": boletas,
        }
        datos["test"] = True
        result = fe.libro(datos)
        if result.get('errores'):
            raise UserError("Error al timbrar libro: %s" %str(result['errores']))
        envio_dte = result["sii_xml_request"]
        doc_id = "{}_{}".format(self.tipo_operacion, self.periodo_tributario)
        self.sii_xml_request = (
            self.env["sii.xml.envio"]
            .create({"xml_envio": envio_dte, "name": doc_id, "company_id": self.company_id.id,})
            .id
        )



class Boletas(models.Model):
    _name = "account.move.book.boletas"
    _description = "Línea de boletas mensuales para Libro de Ventas"

    currency_id = fields.Many2one(
        "res.currency",
        string="Moneda",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    tipo_boleta = fields.Many2one(
        "sii.document_class",
        string="Tipo de Boleta",
        required=True,
        domain=[("document_letter_id.name", "in", ["B", "M"])],
    )
    rango_inicial = fields.Integer(string="Rango Inicial", required=True)
    rango_final = fields.Integer(string="Rango Final", required=True)
    cantidad_boletas = fields.Integer(string="Cantidad Boletas", required=True)
    neto = fields.Monetary(string="Monto Neto", required=True)
    impuesto = fields.Many2one(
        "account.tax",
        string="Impuesto",
        required=True,
        domain=[("type_tax_use", "!=", "none"), "|", ("active", "=", False), ("active", "=", True)],
    )
    monto_impuesto = fields.Monetary(string="Monto Impuesto", required=True)
    monto_total = fields.Monetary(string="Monto Total", required=True)
    book_id = fields.Many2one("account.move.book")

    @api.onchange("rango_inicial", "rango_final")
    def get_cantidad(self):
        if not self.rango_inicial or not self.rango_final:
            return
        if self.rango_final < self.rango_inicial:
            raise UserError("¡El rango Final no puede ser menor al inicial")
        self.cantidad_boletas = self.rango_final - self.rango_inicial + 1


class ImpuestosLibro(models.Model):
    _name = "account.move.book.tax"
    _description = "línea de impuesto Libro CV"

    def get_monto(self):
        for t in self:
            t.amount = t.debit - t.credit
            if t.book_id.tipo_operacion in ["VENTA"]:
                t.amount = t.credit - t.debit

    tax_id = fields.Many2one("account.tax", string="Impuesto")
    credit = fields.Monetary(string="Créditos", default=0.00)
    debit = fields.Monetary(string="Débitos", default=0.00)
    amount = fields.Monetary(compute="get_monto", string="Monto")
    currency_id = fields.Many2one(
        "res.currency",
        string="Moneda",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    book_id = fields.Many2one("account.move.book", string="Libro")
