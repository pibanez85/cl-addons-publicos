import logging

from odoo import models

_logger = logging.getLogger(__name__)


class MailComposer(models.TransientModel):
    _inherit = "mail.compose.message"

    def _compute_attachment_ids(self):
        super()._compute_attachment_ids()
        atts = self.env.context.get("default_attachment_ids", [])
        if not atts:
            return
        att_ids = [a for a in atts if isinstance(a, int)]
        for composer in self:
            missing = [a for a in att_ids if a not in composer.attachment_ids.ids]
            if missing:
                composer.attachment_ids = [(4, a) for a in missing]
