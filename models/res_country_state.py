from odoo import fields, models


class ResState(models.Model):
    _inherit = "res.country.state"

    child_ids = fields.One2many("res.country.state.provincia", "state_id", string="Child Provs",)

    def get_website_sale_cities(self, mode='billing'):
        return self.env['res.city'].sudo().search([('state_id', '=', self.id)])
