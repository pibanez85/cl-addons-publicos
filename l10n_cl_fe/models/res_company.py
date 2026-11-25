import hashlib
import re
from datetime import timedelta

from odoo import api, fields, models
from odoo.tools.translate import _

from .ir_module_module import es_incompatible


SISTEMAS_XML = [("mipyme", "Sistema MIPYME del SII"), ("propio", "Software propio o de mercado")]


class DTECompany(models.Model):
    _inherit = "res.company"

    @api.depends("country_id")
    def _compute_uninstalled_l10n_module_ids(self):
        # Odoo 20 instala estos módulos al crear una compañía chilena (install_l10n_modules);
        # l10n_cl y l10n_latam* son incompatibles con l10n_cl_fe.
        super()._compute_uninstalled_l10n_module_ids()
        for company in self:
            company.uninstalled_l10n_module_ids = company.uninstalled_l10n_module_ids.filtered(
                lambda m: not es_incompatible(m.name))

    def _get_default_tp_type(self):
        try:
            return self.env.ref("l10n_cl_fe.res_IVARI")
        except Exception:
            return self.env["sii.responsability"]

    def _get_default_doc_type(self):
        try:
            return self.env.ref("l10n_cl_fe.dt_RUT")
        except Exception:
            return self.env["sii.document_type"]

    sii_firma_ids = fields.Many2many(
        "sii.firma",
        relation="res_company_sii_firma_rel",
        column1="res_company_id",
        column2="sii_firma_id",
        string="Firmas Electrónicas",
    )
    dte_email_id = fields.Many2one(
        "mail.alias",
        string="DTE EMail",
        ondelete="restrict",
        help="The email address associated with electronica invoice, where all emails vendors would send Exchange DTE, for automatic reception in Odoo.",
    )
    dte_service_provider = fields.Selection(
        [
            ("SIICERT", "SII - Certification process"),
            ("SII", "www.sii.cl")
        ],
        string="DTE Service Provider",
        help="""Please select your company service \
provider for DTE service.
    """,
        default="SIICERT",
    )
    dte_resolution_number = fields.Char(
        string="SII Exempt Resolution Number",
        help="""This value must be provided \
and must appear in your pdf or printed tribute document, under the electronic \
stamp to be legally valid.""",
        default="0",
    )
    dte_resolution_date = fields.Date("SII Exempt Resolution Date",)
    sii_regional_office_id = fields.Many2one("sii.regional.offices", string="SII Regional Office",)
    company_activities_ids = fields.Many2many(
        "partner.activities", related="partner_id.acteco_ids", string="Activities Names", readonly=False,
    )
    responsability_id = fields.Many2one(
        related="partner_id.responsability_id",
        string="Responsability",
        default=lambda self: self._get_default_tp_type(),
        readonly=False,
    )
    start_date = fields.Date(related="partner_id.start_date", string="Start-up Date", readonly=False,)
    invoice_vat_discrimination_default = fields.Selection(
        [
            ("no_discriminate_default", "Yes, No Discriminate Default"),
            ("discriminate_default", "Yes, Discriminate Default"),
        ],
        string="Invoice VAT discrimination default",
        default="no_discriminate_default",
        required=True,
        help="""Define behaviour on invoices reports. Discrimination or not \
 will depend in partner and company responsability and SII letters\
        setup:
            * If No Discriminate Default, if no match found it won't \
            discriminate by default.
            * If Discriminate Default, if no match found it would \
            discriminate by default.
            """,
    )
    activity_description = fields.Many2one(
        string="Glosa Giro",
        related="partner_id.activity_description",
        comodel_name="sii.activity.description",
        readonly=False,
    )
    city_id = fields.Many2one(related="partner_id.city_id", comodel_name="res.city", string="City (SII)", readonly=False,)
    document_number = fields.Char(
        related="partner_id.document_number", string="Document Number", readonly=False,
    )
    document_type_id = fields.Many2one(
        related="partner_id.document_type_id",
        comodel_name="sii.document_type",
        string="Document type",
        default=lambda self: self._get_default_doc_type(),
        readonly=False,
    )
    sucursal_ids = fields.One2many(
        'sii.sucursal',
        'company_id',
        string="Sucursales de la compañia",
    )
    sucursal_id = fields.Many2one(
        'sii.sucursal',
        string="Sucursal SII de esta compañia",
    )
    medios_de_pago_electronico = fields.Many2many(
        'account.journal',
        string="Medios de pago Electrónico",
        domain=[('type', 'not in', ['sale', 'purchase'])],
    )
    medios_de_pago_internet = fields.Many2many(
        'account.journal',
        'res_company_medios_pago_internet_rel',
        'company_id',
        'journal_id',
        string="Medios de pago Electrónico por Internet",
        domain=[('type', 'not in', ['sale', 'purchase'])],
    )
    modelo_emision_boleta = fields.Selection(
        [
            ('voucher', 'Voucher'),
            ('boleta', 'Boleta'),
        ],
        string="Modelo de emisión de boleta",
        default='voucher',
        required=True,
        help="Atributo del contribuyente en el SII (https://www4.sii.cl/actatributointernetui/#!/inicio). "
             "Voucher: cada pago electrónico es un comprobante de pago (tipo 48) que el SII considera como boleta; "
             "la boleta se emite solo por pagos en efectivo o transferencia. "
             "Boleta: el voucher es solo comprobante de pago y siempre se emite boleta.",
    )
    dte_lines = fields.Integer(
        string="Cantidad de Líneas",
        default=60,
    )
    dte_lines_factor = fields.Float(
        string="Factor de Cálculo Líneas",
        default=50.0,
    )
    dte_lines_action = fields.Selection(
        [
            ('create', 'Auto Crear otro documento'),
            ('stop', 'Detener Creación'),
            ('autopack', 'Agrupar líneas en el XML (auto-pack)'),
        ],
        string="Acción a ejecutar",
        default="autopack",
    )
    url_remote_partners = fields.Char(
        string="Url Remote Partners",
        default="https://sre.cl/api/company_info"
    )
    token_remote_partners = fields.Char(
        string="Token Remote Partners",
        default="token_publico",
    )
    cf_autosend = fields.Boolean(
        string="AutoEnviar Consumo de Folios",
        default=False,
    )
    token_remote_partners = fields.Char(
        string="Token Remote Partners",
        default="token_publico",
    )
    # Privacidad: los servicios externos que reciben datos de contactos o de la compañía
    # (sre.cl, apicaf.cl) quedan apagados hasta que la compañía los habilite.
    sync_remote_partners = fields.Boolean(
        string="Consultar y sincronizar contactos con sre.cl",
        default=False,
        help="Envía a sre.cl el RUT de los contactos para completar sus datos y sincroniza la "
             "ficha de los contactos con su base. Desactivado: no se envía nada a sre.cl.",
    )
    use_apicaf = fields.Boolean(
        string="Solicitar folios (CAF) con apicaf.cl",
        default=False,
        help="Permite pedir, reobtener y anular folios en el SII a través de apicaf.cl, que recibe "
             "la firma electrónica y el RUT de la compañía. Desactivado: el asistente no se abre "
             "y no se repone ni anula folios automáticamente.",
    )
    url_apicaf = fields.Char(
        string="URL APICAF",
        default='https://apicaf.cl/api/caf',
    )
    token_apicaf = fields.Char(
        string="Token APICAF",
        default='token_publico',
    )
    url_trafquin = fields.Char(string="URL trafquin", default="https://trafquin.cl")
    token_trafquin = fields.Char(string="Token trafquin")
    sii_clave_tributaria = fields.Char(string="Clave tributaria SII", groups="base.group_system", copy=False)
    sii_clave_rechazada = fields.Char(groups="base.group_system", copy=False)
    sii_clave_rechazo_fecha = fields.Datetime(groups="base.group_system", copy=False)
    sii_clave_aviso = fields.Char(compute="_compute_sii_clave_aviso", groups="base.group_system")
    sii_sistema_xml = fields.Selection(
        SISTEMAS_XML,
        string="Sistema de facturación",
        help="Con el sistema MIPYME el SII guarda los XML recibidos y se pueden descargar. Con software propio "
             "el XML de un documento recibido solo llega por correo del emisor. Vacío: si la compañía timbra "
             "documentos en Odoo es software propio; si no, se usa la ficha de la compañía o lo que informe trafquin.",
    )
    sii_sistema_xml_trafquin = fields.Selection(SISTEMAS_XML, string="Sistema según trafquin", readonly=True, copy=False)
    sii_sistema_xml_aviso = fields.Char(compute="_compute_sii_sistema_xml_aviso")
    f29_cuenta_debito_id = fields.Many2one("account.account", string="Cuenta IVA Débito Fiscal", groups="base.group_system",
        check_company=True, copy=False)
    f29_cuenta_credito_id = fields.Many2one("account.account", string="Cuenta IVA Crédito Fiscal", groups="base.group_system",
        check_company=True, copy=False)
    f29_cuenta_remanente_id = fields.Many2one("account.account", string="Cuenta Remanente Crédito Fiscal", groups="base.group_system",
        check_company=True, copy=False)
    f29_cuenta_ppm_id = fields.Many2one("account.account", string="Cuenta PPM pagado", groups="base.group_system",
        check_company=True, copy=False)
    f29_cuenta_retencion_id = fields.Many2one("account.account", string="Cuenta retención de segunda categoría", groups="base.group_system",
        check_company=True, copy=False)
    f29_cuenta_impuesto_unico_id = fields.Many2one("account.account", string="Cuenta impuesto único", groups="base.group_system",
        check_company=True, copy=False)
    f29_cuenta_por_pagar_id = fields.Many2one("account.account", string="Cuenta F29 por pagar", groups="base.group_system",
        check_company=True, copy=False)
    f29_cuenta_recargo_id = fields.Many2one("account.account", string="Cuenta de reajuste, intereses y multas", groups="base.group_system",
        check_company=True, copy=False)
    f29_cuenta_reajuste_id = fields.Many2one("account.account", string="Cuenta reajuste remanente",
        groups="base.group_system", check_company=True, copy=False)
    f29_cuenta_postergado_id = fields.Many2one("account.account", string="Cuenta IVA postergado por pagar",
        groups="base.group_system", check_company=True, copy=False)
    f29_diario_id = fields.Many2one("account.journal", string="Diario del asiento F29", groups="base.group_system",
        check_company=True, copy=False)
    f29_tesoreria_id = fields.Many2one("res.partner", string="Tesorería General de la República",
        groups="base.group_system", copy=False)

    @api.model
    def _buscar_por_rut(self, rut):
        """Compañía con ese RUT (p. ej. el ``RUTRecep`` de un DTE recibido): por ``vat`` o por el RUT
        de su contacto. La emisión usa ``document_number``, que puede estar sin ``vat`` si la compañía
        se creó por ORM (el ``vat`` lo completa solo el onchange del formulario)."""
        Partner = self.env["res.partner"]
        vats = Partner._vats_busqueda(rut)
        formateado = Partner._formatear_rut(rut)
        if not formateado:
            return self.browse()
        return self.search(["|", ("vat", "in", vats), ("partner_id.document_number", "=", formateado)], limit=1)

    @api.onchange("document_number", "document_type_id")
    def onchange_document(self):
        if self.document_number and (self.document_type_id == self.env.ref("l10n_cl_fe.dt_RUT")
            or self.document_type_id == self.env.ref("l10n_cl_fe.dt_RUN")):
            document_number = (re.sub("[^1234567890Kk]", "", str(self.document_number))).zfill(9).upper()
            if not self.partner_id.check_vat_cl(document_number):
                self.vat = ""
                self.document_number = ""
                return {"warning": {"title": _("Rut Erróneo"), "message": _("Rut Erróneo"),}}
            vat = "CL%s" % document_number
            exist = self.env["res.partner"].search(
                [("vat", "=", vat), ("vat", "!=", "CL555555555"), ("commercial_partner_id", "!=", self.id),], limit=1,
            )
            if exist:
                self.vat = ""
                self.document_number = ""
                return {
                    "warning": {
                        "title": "Informacion para el Usuario",
                        "message": _("El usuario %s está utilizando este documento") % exist.name,
                    }
                }
            self.vat = vat
            self.document_number = self.env["res.partner"]._formatear_rut(document_number)
        elif self.document_number and self.document_type_id == self.env.ref("l10n_cl_fe.dt_Sigd"):
            self.document_number = ""
        else:
            self.vat = ""

    @api.onchange("city_id")
    def _asign_city(self):
        if self.city_id:
            self.country_id = self.city_id.state_id.country_id.id
            self.state_id = self.city_id.state_id.id
            self.city = self.city_id.name

    def _huella_clave_sii(self):
        clave = self.sudo().sii_clave_tributaria
        return hashlib.sha256(clave.encode()).hexdigest() if clave else False

    def _clave_sii_rechazada(self):
        self.ensure_one()
        huella = self._huella_clave_sii()
        return bool(huella) and self.sudo().sii_clave_rechazada == huella

    def _bloqueo_trafquin_hasta(self):
        fecha = self.sudo().sii_clave_rechazo_fecha
        hasta = fecha and fecha + timedelta(hours=1)
        return hasta if hasta and hasta > fields.Datetime.now() else False

    @api.depends("sii_clave_tributaria", "sii_clave_rechazada", "sii_clave_rechazo_fecha")
    def _compute_sii_clave_aviso(self):
        for company in self:
            hasta = company._bloqueo_trafquin_hasta()
            if company._clave_sii_rechazada():
                company.sii_clave_aviso = _("El SII rechazó esta clave: cámbiela")
            elif hasta and company.sudo().sii_clave_tributaria:
                company.sii_clave_aviso = _(
                    "La clave anterior fue rechazada: trafquin no probará la nueva clave hasta las %s"
                ) % fields.Datetime.context_timestamp(company, hasta).strftime("%H:%M")
            else:
                company.sii_clave_aviso = False

    def _marcar_clave_sii_rechazada(self):
        self.ensure_one()
        if self._bloqueo_trafquin_hasta() and self.sudo().sii_clave_rechazada != self._huella_clave_sii():
            return False
        self.sudo().write({
            "sii_clave_rechazada": self._huella_clave_sii(),
            "sii_clave_rechazo_fecha": fields.Datetime.now(),
        })
        return True

    def _rut_ingreso_sii(self):
        self.ensure_one()
        return self.partner_id.rut()

    def _timbra_documentos(self):
        self.ensure_one()
        if self.env["dte.caf"].sudo().search_count([("company_id", "=", self.id)], limit=1):
            return True
        for modelo, dominio in (
            ("account.move", [("move_type", "in", ["out_invoice", "out_refund"])]),
            ("stock.picking", []),
            ("pos.order", []),
        ):
            Modelo = self.env.get(modelo)
            if Modelo is None or "sii_barcode" not in Modelo._fields:
                continue
            if Modelo.sudo().search_count(
                    [("company_id", "=", self.id), ("sii_barcode", "not in", [False, ""])] + dominio, limit=1):
                return True
        return False

    def _sistema_xml(self):
        self.ensure_one()
        if self.sii_sistema_xml:
            return self.sii_sistema_xml
        if self._timbra_documentos():
            return "propio"
        if self.partner_id.es_mipyme:
            return "mipyme"
        return self.sii_sistema_xml_trafquin or False

    @api.depends("sii_sistema_xml", "sii_sistema_xml_trafquin", "partner_id.es_mipyme")
    def _compute_sii_sistema_xml_aviso(self):
        nombres = dict(SISTEMAS_XML)
        for company in self:
            company.sii_sistema_xml_aviso = False
            if not company.id or company.sii_sistema_xml:
                continue
            if company._timbra_documentos():
                company.sii_sistema_xml_aviso = _(
                    "Timbra documentos en Odoo: se considera software propio, aunque trafquin informa que usa "
                    "el sistema MIPYME del SII"
                ) if company.sii_sistema_xml_trafquin == "mipyme" else _(
                    "Timbra documentos en Odoo: se considera software propio")
            elif company._sistema_xml():
                company.sii_sistema_xml_aviso = _("Se considera: %s") % nombres[company._sistema_xml()]

    def _informar_mipyme(self, mipyme):
        if mipyme is None:
            return
        self.sudo().sii_sistema_xml_trafquin = "mipyme" if mipyme else "propio"
