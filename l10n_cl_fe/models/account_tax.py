import logging
import re
from datetime import datetime, time
import dateutil.relativedelta as relativedelta
import pytz
from lxml import html

from collections import defaultdict

from odoo import api, models, fields, _, Command
from odoo.tools.misc import formatLang
from .currency import float_round_custom
from odoo.tools.float_utils import float_round as round
from odoo.exceptions import UserError
import math

_logger = logging.getLogger(__name__)
try:
    import urllib3

    urllib3.disable_warnings()
    pool = urllib3.PoolManager()
except ImportError:
    _logger.warning("no se ha cargado urllib3")
try:
    import pymupdf as fitz
except Exception as e:
    fitz = False
    _logger.warning("error en PyMUPDF: %s" % str(e))
try:
    from io import BytesIO
except Exception as e:
    _logger.warning("error en BytesIO: %s" % str(e))
try:
    from PIL import Image
except Exception as e:
    _logger.warning("error en PIl: %s" % str(e))
try:
    import pytesseract
except Exception as e:
    pytesseract = False
    _logger.warning("error en pytesseract: %s" % str(e))

meses = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}
_cache_page = {}
_cache_list_page = {}
_cache_page_2 = {}


class SIITax(models.Model):
    _inherit = "account.tax"

    sii_code = fields.Integer(
            string='SII Code',
        )
    no_rec = fields.Boolean(
            string="Es No Recuperable",
            default=False,
        )
    activo_fijo = fields.Boolean(
            string="Activo Fijo",
            default=False,
        )
    sii_detailed = fields.Boolean(
            string='Desglose de IVA',
            default=False,
        )
    uom_id = fields.Many2one(
        'uom.uom',
        string="Force Uom"
    )
    mepco = fields.Selection(
        [
            ('diesel', 'Diesel'),
            ('gasolina_93', 'Gasolina 93'),
            ('gasolina_97', 'Gasolina 97'),
        ],
        string="Indicador Mepco",
    )
    ind_exe = fields.Selection([
            ('1', 'No afecto o exento de IVA (10)'),
            ('2', 'Producto o servicio no es facturable'),
            ('3', 'Garantía de depósito por envases (Cervezas, Jugos, Aguas Minerales, Bebidas Analcohólicas u otros autorizados por Resolución especial)'),
            ('4', 'Ítem No Venta. (Para facturas y guías de despacho (ésta última con Indicador Tipo de Traslado de Bienes igual a 1) y este ítem no será facturado.'),
            ('5', 'Ítem a rebajar. Para guías de despacho NO VENTA que rebajan guía anterior. En el área de referencias se debe indicar la guía anterior.'),
            ('6', 'Producto o servicio no facturable negativo (excepto en liquidaciones-factura)'),
        ],
        string="Indicador Exento por defecto"
    )
    include_base_amount_cl = fields.Boolean(
        string="Base Precio Incluído Chileno"
    )
    mepco_origen = fields.Selection([
        ('sii', 'Página del SII'),
        ('diario', 'Página DiarioOficial.cl'),
        ('pdf', 'PDF subido según formato DiarioOficial'),
        ('manual', 'Manual'),
        ],
        string="Origen actualización Mepco",
        default=lambda self: 'diario' if self.mepco else 'manual',
    )

    @api.onchange('mepco_origen')
    def _verificar_dependencias_python(self):
        if self.mepco_origen in ['diario', 'pdf']:
            if not fitz:
                raise UserError("Debe instalar la dependencia python PyMuPDF y luego reiniciar el servicio de odoo")
            elif fitz.version[0] < "1.21.1":
                raise UserError("Debe actualizar la dependencia python PyMuPDF>=1.21.1 y luego reiniciar el servicio de odoo")
            if not pytesseract:
                raise UserError("Debe instalar la dependencia python pytesseract\
                                y tambien los paquetes deb con sudo apt install \
                                tesseract-ocr libtesseract-dev y luego reiniciar \
                                el servicio de odoo")
            elif pytesseract.__version__ < "0.3.10":
                raise UserError("Debe actualizar la dependencia python pytesseract>=0.3.10 y luego reiniciar el servicio de odoo")

    def es_iva(self):
        return self.sii_code in [14, 15]

    def es_iva_retencion(self):
        return self.sii_code == 15

    def es_adicional(self):
        return self.sii_code in [24, 25, 26, 27, 271]

    def es_especifico(self):
        return self.sii_code in [28, 35, 51]

    @api.onchange('sii_code', 'price_include')
    def autoseleccionar_detailed(self):
        self.sii_detailed = self.price_include and (self.es_adicional() or self.es_especifico())
        self.include_base_amount_cl = self.price_include

    def _fix_composed_included_tax(self, base, quantity, uom_id):
        composed_tax = {}
        price_included = False
        percent = 0.0
        rec = 0.0
        for tax in self.sorted(key=lambda r: r.sequence):
            if tax.price_include:
                price_included = True
            else:
                continue
            if tax.amount_type == "percent":
                percent += tax.amount
            else:
                amount_tax = tax.compute_factor(uom_id)
                rec += quantity * amount_tax
        if price_included:
            _base = base - rec
            common_base = _base / (1 + percent / 100.0)
            for tax in self.sorted(key=lambda r: r.sequence):
                if tax.amount_type == "percent":
                    composed_tax[tax.id] = common_base * (1 + tax.amount / 100)
        return composed_tax

    def compute_factor(self, uom_id):
        """Monto de un impuesto fijo expresado en la unidad de medida de la línea.

        Los impuestos específicos (p. ej. combustibles, MEPCO) se definen por una unidad
        (``uom_id`` del impuesto, p. ej. m3) y la línea puede venir en otra (p. ej. litros).
        """
        amount_tax = self.amount or 0.0
        if self.uom_id and uom_id and self.uom_id != uom_id:
            factor = self.uom_id._compute_quantity(1, uom_id)
            amount_tax = (amount_tax / factor)
        return amount_tax

    # Odoo 18+ calcula los impuestos con la API de "base lines" (_add_tax_details_in_base_line);
    # el compute_all/_compute_taxes_for_single_line/_prepare_tax_totals de 17.0 ya no se llaman.
    # Lo específico de Chile que sigue vigente se engancha en los puntos de extensión del motor:

    def _eval_taxes_computation_prepare_product_uom_fields(self):
        fields_to_load = super()._eval_taxes_computation_prepare_product_uom_fields()
        if any(tax.amount_type == 'fixed' and tax.uom_id for tax in self._flatten_taxes_and_sort_them()[0]):
            fields_to_load = set(fields_to_load) | {'id'}
        return fields_to_load

    def _eval_tax_amount_fixed_amount(self, batch, raw_base, evaluation_context):
        if self.amount_type == 'fixed' and self.uom_id:
            uom = self.env['uom.uom'].browse((evaluation_context.get('uom') or {}).get('id') or [])
            sign = -1 if evaluation_context['price_unit'] < 0.0 else 1
            return sign * evaluation_context['quantity'] * self.compute_factor(uom)
        return super()._eval_tax_amount_fixed_amount(batch, raw_base, evaluation_context)

    def _compute_amount_ret(self, base_amount, price_unit, quantity=1.0, product=None, partner=None, fixed_multiplicator=1, uom_id=None):
        if self.amount_type == "percent" and self.price_include:
            neto = base_amount / (1 + self.retencion / 100)
            tax = base_amount - neto
            return tax
        if (self.amount_type == "percent" and not self.price_include) or (
            self.amount_type == "division" and self.price_include
        ):
            return base_amount * self.retencion / 100

    def _list_from_diario(self, day, year, month, i=1):
        if i == 4:
            return {}
        date = datetime.strptime("{}-{}-{}".format(day, month, year), "%d-%m-%Y").astimezone(pytz.UTC)
        t = date - relativedelta.relativedelta(days=i)
        t_date = "date={}-{}-{}".format(t.strftime("%d"), t.strftime("%m"), t.strftime("%Y"))
        url = "https://www.diariooficial.interior.gob.cl/edicionelectronica/"
        data = _cache_list_page.get(t_date)
        if not data:
            resp = pool.request("GET", "{}select_edition.php?{}".format(url, t_date))
            data = _cache_list_page[t_date] = resp.data.decode("utf-8")
        target = 'a href="index.php[?]%s&edition=([0-9]*)&v=1"' % t_date
        url2 = re.findall(target, data)
        if not url2:
            return self._list_from_diario(day, year, month, (i+1))
        data2 = _cache_page_2.get(url2[0])
        if not data2:
            resp2 = pool.request("GET", "{}index.php?{}&edition={}".format(url, t_date, url2[0]))
            data2 = _cache_page_2[url2[0]] = resp2.data.decode("utf-8")
        # target = 'Determina el componente variable para el cálculo del impuesto específico establecido en la ley N° 18.502 [a-zA-Z \r\n</>="_0-9]* href="([a-zA-Z 0-9/.:]*)"'
        target = '18.502[\\W]* [a-zA-Z \r\n<\\/>="_0-9]* href="([a-zA-Z 0-9\\/.:]*)"'
        url3 = re.findall(target, data2)
        if not url3:
            _logger.warning("iter "+str(i))
            return self._list_from_diario(day, year, month, (i+1))
        return {date: url3[0].replace("http:", "https:")}

    def _get_from_diario(self, url):
        self._verificar_dependencias_python()
        data = _cache_page.get(url)
        if not data:
            resp = pool.request("GET", url)
            data = _cache_page[url] = resp.data
        doc = fitz.open(stream=data, filetype="pdf")
        imagenes = doc.load_page(1).get_images()
        if len(imagenes) > 2:
            imagen = imagenes[2]
            # Extrae la imagen y conviértela a texto utilizando pytesseract
            pix = fitz.Pixmap(doc, imagen[0])
            imagen_bytes = pix.tobytes("png")
            # Convierte la imagen a otro formato compatible con pytesseract
            imagen_pil = Image.open(BytesIO(imagen_bytes))
            imagen_pil = imagen_pil.convert('RGB')
            imagen_bytesio = BytesIO()
            imagen_pil.save(imagen_bytesio, format='PNG')
            imagen_bytes = imagen_bytesio.getvalue()

            # Extrae el texto de la imagen usando pytesseract
            texto = pytesseract.image_to_string(Image.open(BytesIO(imagen_bytes)))
            i = 0
            target_i = 0
            if self.mepco == "gasolina_97":
                target_i = 1
            elif self.mepco == "diesel":
                target_i = 2
            elif self.mepco == "gas_licuado":
                target_i = 3
            elif self.mepco == "gas_natural":
                target_i = 4
            val = False
            for l in texto.splitlines():
                if not l or l.isspace():
                    continue
                val = l.split(' ')[-1]
                try:
                    val = val.replace(',', '.')
                    float(val)
                    if i == target_i:
                        return val
                    i += 1
                except:
                    val = False
        else:
            target = "Gasolina Automotriz de[\n ]93 octanos[\n ]\\(*en UTM\\/m[\\w]\\)"
            if self.mepco == "gasolina_97":
                target = "Gasolina Automotriz de[\n ]97 octanos[\n ]\\(en UTM\\/m[\\w]\\)"
            elif self.mepco == "diesel":
                target = "Petr[\\w]leo [dD]i[\\w]sel[\n ]\\(en UTM\\/m[\\w]\\)"
            elif self.mepco == "gas_licuado":
                target = "Gas Licuado del Petróleo de Consumo[\n ]Vehicular[\n ]\\(en UTM\\/m[\\w]\\)"
            elif self.mepco == "gas_natural":
                target = "Gas Natural Comprimido de Consumo Vehicular"
            val = re.findall("%s\n[0-9.,-]*\n[0-9.,-]*\n([0-9.,-]*)" % target, doc.loadPage(1).getText())
        return val[0].replace(".", "").replace(",", ".")

    def _connect_sii(self, year, month):
        month = meses[int(month)].lower()
        url = "https://www.sii.cl/valores_y_fechas/mepco/mepco%s.htm" % year
        resp = pool.request("GET", url)
        sii = html.fromstring(resp.data)
        return sii.findall('.//div[@id="pp_%s"]/div/table' % (month))

    def _list_from_sii(self, year, month):
        tables = self._connect_sii(year, month)
        rangos = {}
        i = 0
        for r in tables:
            sub = r.find("tr/th")
            res = re.search(r"\d{1,2}\-\d{1,2}\-\d{4}", sub.text.lower())
            rangos[datetime.strptime(res[0], "%d-%m-%Y").astimezone(pytz.UTC)] = i
            i += 1
        return rangos

    def _get_from_sii(self, year, month, target):
        tables = self._connect_sii(year, month)
        line = 1
        if self.mepco == "gasolina_97":
            line = 3
        elif self.mepco == "diesel":
            line = 5
        val = tables[target].findall("tr")[line].findall("td")[4].text.replace(".", "").replace(",", ".")
        return val

    def prepare_mepco(self, date, currency_id=False):
        tz = pytz.timezone("America/Santiago")
        year = date.strftime("%Y")
        month = date.strftime("%m")
        day = date.strftime("%d")
        try:
            if self.mepco_origen in ['diario', 'pdf']:
                rangos = self._list_from_diario(day, year, month)
            elif self.mepco_origen == 'sii':
                rangos = self._list_from_sii(year, month)
        except Exception as e:
            _logger.warning("Error obteniendo mepco: ", exc_info=True)
            return {'found': self._target_mepco((date - relativedelta.relativedelta(days=1)), currency_id)}
        ant = datetime.now(tz)
        target = (ant, 0)
        for k, v in rangos.items():
            if k <= date < ant:
                target = (k, v)
                break
            ant = k
        if not rangos or target[0] > date:
            return {'found': self._target_mepco((date - relativedelta.relativedelta(days=1)), currency_id)}
        if self.mepco_origen in ['diario', 'pdf']:
            val = self._get_from_diario(target[1])
        elif self.mepco_origen == 'sii':
            val = self._get_from_sii(year, month, target[1])
        utm = self.env["res.currency"].sudo().search([("name", "=", "UTM")])
        amount = utm._convert(float(val), currency_id, self.company_id, date)
        return {
            "amount": amount,
            "date": target[0].strftime("%Y-%m-%d"),
            "name": target[0].strftime("%Y-%m-%d"),
            "type": self.mepco,
            "sequence": len(rangos),
            "company_id": self.company_id.id,
            "currency_id": currency_id.id,
            "factor": float(val),
        }

    def actualizar_mepco(self):
        self.verify_mepco(date_target=False, currency_id=False, force=True)

    def _target_mepco(self, date_target=False, currency_id=False, force=False):
        if not currency_id:
            currency_id = self.env["res.currency"].sudo().search([("name", "=", self.env.get("currency", "CLP"))])
        tz = pytz.timezone("America/Santiago")
        if date_target:
            user_zone = pytz.timezone(self.env.context.get("tz") or "UTC")
            date = date_target
            if not hasattr(date, "tzinfo"):
                date = datetime.combine(date, time.min)
            if tz != user_zone:
                date = date.astimezone(tz)
        else:
            date = datetime.now(tz)
        query = [
            ("date", "<=", date.strftime("%Y-%m-%d")),
            ("company_id", "=", self.company_id.id),
            ("type", "=", self.mepco),
        ]
        mepco = self.env["account.tax.mepco"].sudo().search(query, limit=1)
        if mepco:
            diff = date.date() - mepco.date
            if diff.days > 6:
                mepco = False
        if not mepco:
            mepco_data = self.prepare_mepco(date, currency_id)
            if mepco_data.get('found'):
                return mepco_data['found']
            query = [
                ("date", "=", mepco_data["date"]),
                ("company_id", "=", mepco_data["company_id"]),
                ("type", "=", mepco_data["type"]),
            ]
            mepco = self.env["account.tax.mepco"].sudo().search(query, limit=1)
            if not mepco:
                mepco = self.env["account.tax.mepco"].sudo().create(mepco_data)
        elif force:
            mepco_data = self.prepare_mepco(date, currency_id)
            if mepco_data.get('found'):
                return mepco_data['found']
            mepco.sudo().write(mepco_data)
        return mepco

    def verify_mepco(self, date_target=False, currency_id=False, force=False):
        mepco = self._target_mepco(date_target, currency_id, force)
        self.amount = mepco.amount

    def documentos_dte_admitidos(self):
        facturas = [30, 33]
        boleta = [35, 39]
        exportacion = [110]
        factura_compra = [45, 46]
        liquidacion = [43]
        factura_exenta = [32, 34]
        boleta_exenta = [38, 41]
        if self.es_adicional() or self.es_especifico() or self.sii_code in [14, 17, 18, 19, 23, 44, 45, 46, 50, 52, 53]:
            res = facturas + boleta + liquidacion
            if self.sii_code == 14:
                res += factura_compra
            return res   
        if self.sii_code in [15, 30, 31, 32, 33, 34, 36, 37, 38, 39, 41, 47, 48, 49]:
            return factura_compra
        return exportacion + factura_exenta + boleta_exenta + facturas + boleta + liquidacion
