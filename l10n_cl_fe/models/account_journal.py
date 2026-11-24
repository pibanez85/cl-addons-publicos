import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountJournal(models.Model):
    _inherit = "account.journal"

    sucursal_id = fields.Many2one("sii.sucursal", string="Sucursal",)
    sii_code = fields.Char(related="sucursal_id.sii_code", string="Código SII Sucursal", readonly=True,)
    journal_document_class_ids = fields.One2many("account.journal.sii_document_class", "journal_id", "Documents Class",)
    document_class_ids = fields.Many2many("sii.document_class", string="Document Class IDs")
    use_documents = fields.Boolean(string="Use Documents?",)
    company_activity_ids = fields.Many2many("partner.activities", related="company_id.company_activities_ids")
    journal_activities_ids = fields.Many2many(
        "partner.activities",
        string="Journal Turns",
        help="""Select the turns you want to \
            invoice in this Journal""",
    )
    restore_mode = fields.Boolean(string="Restore Mode", default=False,)
    default_gd_account_id = fields.Many2one('account.account', string='Default GD Account',
        company_dependent=True, domain="[('active', '=', True), ('company_ids', 'parent_of', current_company_id)]"
    )
    default_gr_account_id = fields.Many2one('account.account', string='Default GR Account',
        company_dependent=True, domain="[('active', '=', True), ('company_ids', 'parent_of', current_company_id)]"
    )
    default_comision_account_id = fields.Many2one('account.account', string='Default Comision Account',
        company_dependent=True, domain="[('active', '=', True), ('company_ids', 'parent_of', current_company_id)]"
    )
    sii_declara_ventas = fields.Boolean(
        string="Declara ventas en el SII", compute="_compute_sii_declara_ventas",
        inverse="_inverse_sii_declara_ventas", search="_search_sii_declara_ventas",
        help="Diarios de banco de medios de pago que informan sus ventas al SII como comprobantes de pago "
             "electrónico (48), por ejemplo Transbank, Webpay, Flow o Khipu. No lo marque en medios que no "
             "declaran (por ejemplo Compraquí) ni en PayPal u otros medios extranjeros.",
    )
    sii_canal_48 = fields.Selection(
        [("presencial", "Presencial (máquinas de pago)"), ("internet", "Internet (pasarelas de pago)")],
        string="Canal en el SII", compute="_compute_sii_canal_48", inverse="_inverse_sii_canal_48",
    )

    @api.depends("company_id.medios_de_pago_electronico")
    def _compute_sii_declara_ventas(self):
        for r in self:
            r.sii_declara_ventas = r in r.company_id.medios_de_pago_electronico

    @api.depends("company_id.medios_de_pago_electronico", "company_id.medios_de_pago_internet")
    def _compute_sii_canal_48(self):
        for r in self:
            r.sii_canal_48 = False if r not in r.company_id.medios_de_pago_electronico else (
                "internet" if r in r.company_id.medios_de_pago_internet else "presencial")

    def _inverse_sii_declara_ventas(self):
        for r in self.filtered("company_id"):
            company = r.company_id.sudo()
            if r.sii_declara_ventas:
                company.medios_de_pago_electronico = [(4, r.id)]
            else:
                company.write({"medios_de_pago_electronico": [(3, r.id)], "medios_de_pago_internet": [(3, r.id)]})

    def _inverse_sii_canal_48(self):
        for r in self.filtered(lambda j: j.company_id and j.sii_declara_ventas):
            r.company_id.sudo().medios_de_pago_internet = [(4 if r.sii_canal_48 == "internet" else 3, r.id)]

    def _search_sii_declara_ventas(self, operator, value):
        if operator not in ("=", "!="):
            raise UserError(_("Operador no soportado: %s") % operator)
        value = bool(value)
        ids = self.env["res.company"].sudo().search([]).medios_de_pago_electronico.ids
        return [("id", "in" if (operator == "=") == value else "not in", ids)]

    @api.onchange("journal_document_class_ids")
    def set_documents(self):
        self.document_class_ids = []
        for r in self.journal_document_class_ids:
            self.document_class_ids += r.sii_document_class_id

    @api.onchange("journal_activities_ids")
    def max_actecos(self):
        if len(self.journal_activities_ids) > 4:
            raise UserError("Deben Ser máximo 4 actecos por Diario, seleccione los más significativos para este diario")

    @api.constrains('use_documents', 'type')
    def _check_use_documents_type(self):
        for journal in self:
            if journal.use_documents and journal.type not in ('sale', 'purchase'):
                raise UserError("El campo 'Use Documents' solo puede estar activo en diarios de tipo Venta o Compra.")

    @api.onchange('type')
    def _onchange_type_use_documents(self):
        if self.type not in ('sale', 'purchase') and self.use_documents:
            self.use_documents = False
            if self.journal_document_class_ids:
                raise UserError('Debe quitar los documentos de la pestaña "Documentos DTE"')

    @api.onchange('use_documents')
    def _onchange_use_documents(self):
        if self.use_documents and self.type not in ('sale', 'purchase'):
            self.use_documents = False
            raise UserError("El campo 'Use Documents' solo puede estar activo en diarios de tipo Venta o Compra.")

    @api.depends('name', 'currency_id', 'company_id', 'sucursal_id')
    def _compute_display_name(self):
        super()._compute_display_name()
        for journal in self:
            currency = journal.currency_id or journal.company_id.currency_id
            name = "{} ({})".format(journal.name, currency.name)
            if journal.sucursal_id and self.env.context.get("show_full_name", False):
                name = "{} ({})".format(name, journal.sucursal_id.name)
            journal.display_name = name
