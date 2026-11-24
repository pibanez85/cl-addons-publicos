from odoo import api, fields, models
from odoo.exceptions import UserError

from .currency import CMF_URL

try:
    from facturacion_electronica import __version__
except ImportError:
    __version__ = "0.0.0"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    send_dte_method = fields.Selection([('manual', 'Manual'), ('inmediato', 'Inmediato'), ('diferido', 'Diferido')], string="Método de Envío DTE al SII", default='diferido',)
    auto_send_dte = fields.Integer(string="Tiempo de Espera para Enviar DTE automático al SII (en horas)", default=1,)
    auto_send_email = fields.Boolean(string="Enviar Email automático al Auto Enviar DTE al SII", default=True,)
    auto_send_persistencia = fields.Integer(string="Enviar Email automático al Cliente cada  n horas", default=24,)
    dte_email_id = fields.Many2one("mail.alias", related="company_id.dte_email_id", readonly=False)
    dte_lines = fields.Integer(related="company_id.dte_lines", string="Cantidad de Líneas", readonly=False)
    dte_lines_factor = fields.Float(related="company_id.dte_lines_factor", string="Factor de Cálculo Líneas", readonly=False)
    dte_lines_action = fields.Selection(
        related="company_id.dte_lines_action",
        string="Acción a ejecutar",
        readonly=False,
    )
    url_remote_partners = fields.Char(related="company_id.url_remote_partners", string="Url Remote Partners", readonly=False)
    token_remote_partners = fields.Char(related="company_id.token_remote_partners", string="Token Remote Partners", readonly=False)
    sync_remote_partners = fields.Boolean(related="company_id.sync_remote_partners", string="Sync Remote Partners", readonly=False)
    url_apicaf = fields.Char(related="company_id.url_apicaf", string="URL APICAF", readonly=False)
    token_apicaf = fields.Char(related="company_id.token_apicaf", string="Token APICAF", readonly=False)
    url_trafquin = fields.Char(related="company_id.url_trafquin", readonly=False)
    sii_sistema_xml = fields.Selection(related="company_id.sii_sistema_xml", readonly=False)
    sii_sistema_xml_aviso = fields.Char(related="company_id.sii_sistema_xml_aviso")
    f29_cuenta_debito_id = fields.Many2one(related="company_id.f29_cuenta_debito_id", readonly=False, groups="base.group_system")
    f29_cuenta_credito_id = fields.Many2one(related="company_id.f29_cuenta_credito_id", readonly=False, groups="base.group_system")
    f29_cuenta_remanente_id = fields.Many2one(related="company_id.f29_cuenta_remanente_id", readonly=False, groups="base.group_system")
    f29_cuenta_ppm_id = fields.Many2one(related="company_id.f29_cuenta_ppm_id", readonly=False, groups="base.group_system")
    f29_cuenta_retencion_id = fields.Many2one(related="company_id.f29_cuenta_retencion_id", readonly=False, groups="base.group_system")
    f29_cuenta_impuesto_unico_id = fields.Many2one(related="company_id.f29_cuenta_impuesto_unico_id", readonly=False, groups="base.group_system")
    f29_cuenta_por_pagar_id = fields.Many2one(related="company_id.f29_cuenta_por_pagar_id", readonly=False, groups="base.group_system")
    f29_cuenta_recargo_id = fields.Many2one(related="company_id.f29_cuenta_recargo_id", readonly=False, groups="base.group_system")
    f29_cuenta_reajuste_id = fields.Many2one(related="company_id.f29_cuenta_reajuste_id", readonly=False, groups="base.group_system")
    f29_cuenta_postergado_id = fields.Many2one(related="company_id.f29_cuenta_postergado_id", readonly=False, groups="base.group_system")
    f29_diario_id = fields.Many2one(related="company_id.f29_diario_id", readonly=False, groups="base.group_system")
    f29_tesoreria_id = fields.Many2one(related="company_id.f29_tesoreria_id", readonly=False, groups="base.group_system")
    token_trafquin = fields.Char(related="company_id.token_trafquin", readonly=False)
    sii_clave_tributaria = fields.Char(related="company_id.sii_clave_tributaria", readonly=False, groups="base.group_system")
    sii_clave_aviso = fields.Char(related="company_id.sii_clave_aviso", groups="base.group_system")
    cf_autosend = fields.Boolean(related="company_id.cf_autosend", string="AutoEnviar Consumo de Folios", readonly=False)
    fe_version = fields.Char(string="Versión FE instalado", readonly=True,)
    medios_de_pago_electronico = fields.Many2many(
        'account.journal',
        related="company_id.medios_de_pago_electronico",
        string="Medios de pago Electrónico",
    )
    modelo_emision_boleta = fields.Selection(related="company_id.modelo_emision_boleta", readonly=False)
    medios_de_pago_internet = fields.Many2many(
        'account.journal',
        related="company_id.medios_de_pago_internet",
        readonly=False,
    )
    dte_receipt_method = fields.Selection([('manual', 'Manual'), ('create_move', 'Crear Factura Solamente'), ('create_po', 'Crear Orden Compra'), ('both', 'Ambas (Factura y OC)')], default='manual', string="Método Recepción DTE")
    cola_envio_sin_firma_count = fields.Integer(
        string="Cola sin firma", compute="_compute_cola_envio_sin_firma_count",
    )

    def _compute_cola_envio_sin_firma_count(self):
        Cola = self.env["sii.cola_envio"]
        for rec in self:
            rec.cola_envio_sin_firma_count = Cola.search_count([
                ("firma_id", "=", False),
                ("active", "=", True),
            ])

    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        ICPSudo = self.env["ir.config_parameter"].sudo()
        account_send_dte_method = ICPSudo.get_str("account.send_dte_method", default='diferido')
        account_auto_send_dte = ICPSudo.get_int("account.auto_send_dte", default=1)
        account_auto_send_email = ICPSudo.get_bool("account.auto_send_email", default=True)
        account_auto_send_persistencia = ICPSudo.get_int("account.auto_send_persistencia", default=24)
        dte_receipt_method = ICPSudo.get_str("account.dte_receipt_method", default='manual')
        res.update(
            send_dte_method=account_send_dte_method,
            auto_send_email=account_auto_send_email,
            auto_send_dte=account_auto_send_dte,
            auto_send_persistencia=account_auto_send_persistencia,
            fe_version=__version__,
            dte_receipt_method=dte_receipt_method
        )
        return res

    def set_values(self):
        super(ResConfigSettings, self).set_values()
        ICPSudo = self.env["ir.config_parameter"].sudo()
        if self.dte_email_id and not self.external_email_server_default:
            raise UserError("Debe Cofigurar Servidor de Correo Externo en la pestaña Opciones Generales")
        ICPSudo.set_str("account.send_dte_method", self.send_dte_method)
        ICPSudo.set_int("account.auto_send_dte", self.auto_send_dte)
        ICPSudo.set_bool("account.auto_send_email", self.auto_send_email)
        ICPSudo.set_int("account.auto_send_persistencia", self.auto_send_persistencia)
        ICPSudo.set_str("account.dte_receipt_method", self.dte_receipt_method)

    # Actualización de UF, UTM, dólar y euro desde la API de la CMF (ex SBIF). No se
    # guardan en la tabla de ajustes: leen y escriben el parámetro, así el código se
    # puede desplegar en una carpeta compartida sin actualizar de inmediato cada base.
    sbif_token = fields.Char(string="Clave API CMF", compute="_compute_cmf", inverse="_inverse_cmf")
    sbif_url = fields.Char(string="URL API CMF", compute="_compute_cmf", inverse="_inverse_cmf")

    def _compute_cmf(self):
        icp = self.env["ir.config_parameter"].sudo()
        for rec in self:
            rec.sbif_token = icp.get_str("sbif.financial.indicators.apikey")
            rec.sbif_url = icp.get_str("sbif.financial.indicators.baseurl") or CMF_URL

    def _inverse_cmf(self):
        icp = self.env["ir.config_parameter"].sudo()
        for rec in self:
            icp.set_str("sbif.financial.indicators.apikey", rec.sbif_token or False)
            icp.set_str("sbif.financial.indicators.baseurl", rec.sbif_url or CMF_URL)

    def action_update_currencies(self):
        icp = self.env["ir.config_parameter"].sudo()
        actualizadas = self.env["res.currency"].currency_schedule_update()
        if actualizadas:
            mensaje, tipo = "Actualizadas: %s." % ", ".join(actualizadas.mapped("name")), "success"
        elif not icp.get_str("sbif.financial.indicators.apikey"):
            mensaje, tipo = "Ingrese la clave de la API de la CMF.", "warning"
        else:
            mensaje, tipo = "La CMF no devolvió valores; revise la clave y el registro del servidor.", "warning"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"title": "Indicadores CMF", "message": mensaje, "type": tipo, "sticky": False},
        }
