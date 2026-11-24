from odoo import api, fields, models


class PartnerActivities(models.Model):
    _name = "partner.activities"
    _description = "SII Economical Activities"


    @api.depends('code', 'name')
    def _compute_display_name(self):
        for r in self:
            r.display_name = r.code and "[" + r.code + "] " + r.name or ""

    @api.model
    def _name_search(self, name, domain=None, operator="ilike", limit=100, order=None):
        domain = domain or []
        recs = self.browse()
        if name:
            recs = self.search(["|", ("name", "=", name), ("code", "=", name)] + domain, limit=limit)
        if not recs:
            recs = self.search(["|", ("name", operator, name), ("code", operator, name)] + domain, limit=limit)
        return recs.ids

    code = fields.Char(string="Activity Code", required=True,)
    parent_id = fields.Many2one("partner.activities", string="Parent Activity", ondelete="cascade",)
    name = fields.Char(string="Nombre Completo", required=True, translate=True,)
    vat_affected = fields.Selection(
        (("SI", "Si"), ("NO", "No"), ("ND", "ND"),), string="VAT Affected", required=True, default="SI",
    )
    tax_category = fields.Selection(
        (("1", "1"), ("2", "2"), ("ND", "ND"),), string="TAX Category", required=True, default="1",
    )
    internet_available = fields.Boolean(string="Available at Internet", default=True,)
    active = fields.Boolean(string="Active", help="Allows you to hide the activity without removing it.", default=True,)
