import logging

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round

_logger = logging.getLogger(__name__)

# Indicadores que publica la API de la CMF (ex SBIF): moneda -> (recurso, clave del JSON).
CMF_INDICADORES = {
    "UF": ("uf", "UFs"),
    "UTM": ("utm", "UTMs"),
    "USD": ("dolar", "Dolares"),
    "EUR": ("euro", "Euros"),
}
CMF_URL = "https://api.cmfchile.cl/api-sbifv3/recursos_api"


def float_round_custom(value, precision_digits=None, precision_rounding=None, rounding_method="HALF-UP"):
    result = float_round(value, precision_digits, precision_rounding, rounding_method)
    if precision_rounding == 1 or (precision_digits is not None and precision_digits == 0):
        return int(result)
    return result


class ResCurrency(models.Model):
    _inherit = "res.currency"

    code = fields.Char(string="Código",)
    abreviatura = fields.Char(string="Abreviatura",)
    rate = fields.Float(
        compute="_compute_current_rate",
        string="Current Rate",
        digits="Currency Rate",
        help="The rate of the currency to the currency of rate 1.",
    )
    rounding = fields.Float(string="Rounding Factor", digits=(12, 14), default=0.01)

    
    def round(self, amount):
        """Return ``amount`` rounded  according to ``self``'s rounding rules.

           :param float amount: the amount to round
           :return: rounded float
        """
        # TODO: Need to check why it calls round() from sale.py, _amount_all() with *No* ID after below commits,
        # https://github.com/odoo/odoo/commit/36ee1ad813204dcb91e9f5f20d746dff6f080ac2
        # https://github.com/odoo/odoo/commit/0b6058c585d7d9a57bd7581b8211f20fca3ec3f7
        # Removing self.ensure_one() will make few test cases to break of modules event_sale, sale_mrp and stock_dropshipping.
        # self.ensure_one()
        return float_round_custom(amount, precision_rounding=self.rounding)

    # --- Valores desde la API de la CMF (ex SBIF; absorbe l10n_cl_currency_rate_sbif) ---

    def _cmf_valores(self, fecha=None):
        """Valores en pesos que publica la CMF para esta moneda: lista de (fecha, valor).

        Sin fecha, el vigente; con fecha, el de ese día (la UTM, el de ese mes). De las
        fechas futuras solo hay UF publicada: se pide la serie posterior a hoy.
        """
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        apikey = icp.get_str("sbif.financial.indicators.apikey")
        if not apikey or self.name not in CMF_INDICADORES:
            return []
        recurso, clave = CMF_INDICADORES[self.name]
        ruta = [(icp.get_str("sbif.financial.indicators.baseurl") or CMF_URL).rstrip("/"), recurso]
        hoy = fields.Date.context_today(self)
        if fecha and fecha > hoy:
            if recurso != "uf":
                return []
            ruta.append("posteriores")
            fecha = hoy
        if fecha:
            ruta.append(fecha.strftime("%Y/%m"))
            if recurso != "utm":
                ruta.append(fecha.strftime("dias/%d"))
        try:
            resp = requests.get("/".join(ruta), params={"apikey": apikey, "formato": "json"}, timeout=30)
            if resp.status_code == 404:
                # Sin dato para esa fecha: fin de semana (dólar, euro) o aún no publicado.
                return []
            resp.raise_for_status()
            return [
                (fields.Date.to_date(dato["Fecha"]), float(dato["Valor"].replace(".", "").replace(",", ".")))
                for dato in resp.json()[clave]
            ]
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            # Los errores de requests incluyen la URL con la clave: no va al registro.
            _logger.warning("CMF: no se pudo obtener %s: %s", self.name, str(error).replace(apikey, "***"))
            return []

    def _cmf_registrar(self, valores):
        """Guarda los valores como tasa (1 / valor) en cada compañía que lleva pesos."""
        self.ensure_one()
        Rate = self.env["res.currency.rate"].sudo()
        companias = self.env["res.company"].sudo().search([("currency_id.name", "=", "CLP")])
        for fecha, valor in valores:
            if valor <= 0:
                continue
            for compania in companias:
                tasa = Rate.search(
                    [("currency_id", "=", self.id), ("company_id", "=", compania.id), ("name", "=", fecha)],
                    limit=1,
                )
                if tasa:
                    tasa.rate = 1 / valor
                else:
                    Rate.create({"name": fecha, "rate": 1 / valor, "currency_id": self.id, "company_id": compania.id})

    def _cmf_actualizar(self, fecha=None):
        """Trae de la CMF y guarda el valor de cada moneda; devuelve las que se actualizaron."""
        actualizadas = self.browse()
        for moneda in self.filtered(lambda c: c.name in CMF_INDICADORES):
            valores = moneda._cmf_valores(fecha)
            if valores:
                moneda._cmf_registrar(valores)
                actualizadas |= moneda
        return actualizadas

    @api.model
    def currency_schedule_update(self):
        """Cron diario (mismo nombre que en l10n_cl_currency_rate_sbif, cuyo cron se conserva)."""
        if not self.env["ir.config_parameter"].sudo().get_str("sbif.financial.indicators.apikey"):
            _logger.info("CMF: sin clave de API configurada; no se actualizan las monedas")
            return self.browse()
        return self.search([("name", "in", list(CMF_INDICADORES))])._cmf_actualizar()

    def action_update_currency(self):
        if not self.env["ir.config_parameter"].sudo().get_str("sbif.financial.indicators.apikey"):
            raise UserError("Configure la clave de la API de la CMF en Ajustes > Facturación Electrónica.")
        if not self._cmf_actualizar():
            raise UserError(
                "La CMF no devolvió el valor de %s. Revise la clave de la API y el registro del servidor."
                % ", ".join(self.mapped("name"))
            )


class CurrencyRate(models.Model):
    _inherit = "res.currency.rate"

    rate = fields.Float(
        digits="Currency Rate", help="The rate of the currency to the currency of rate 1"
    )
