import logging

from odoo import SUPERUSER_ID, fields, models

_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = "res.users"

    sii_firma_ids = fields.Many2many(
        "sii.firma",
        relation="res_users_sii_firma_rel",
        column1="res_users_id",
        column2="sii_firma_id",
        string="Firmas Electrónicas Autorizadas",
    )
    sii_firma_aviso_id = fields.Many2one("sii.firma", copy=False)
    sii_firma_aviso_fecha = fields.Date(copy=False)

    def get_digital_signature(self, company_id):
        user_id = self.id
        if user_id == SUPERUSER_ID or self.env.user.has_group('base.group_portal'):
            user_id = self.env.ref("base.user_admin").id
        signature = self.env["sii.firma"].sudo().search(
            [
                ("user_ids", "=", user_id),
                ("company_ids", "=", company_id.id),
                ("state", "=", "valid"),
            ],
            limit=1,
            order="priority ASC",
        )
        if signature:
            signature.check_signature()
            if signature.active:
                signature.notify_use_expiration()
                return signature
        return self.env["sii.firma"]
