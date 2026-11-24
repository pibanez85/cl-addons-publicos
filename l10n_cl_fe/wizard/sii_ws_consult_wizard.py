##############################################################################
# For copyright and license notices, see __odoo__.py file in module root
# directory
##############################################################################
from odoo import _, fields, models
from odoo.exceptions import UserError


class sii_ws_consult_wizard(models.TransientModel):
    _name = "sii.ws.consult.wizard"
    _description = "SII WS Consult Wizard"

    number = fields.Integer("Number", required=True,)


    def confirm(self):
        self.ensure_one()
        journal_sii_document_class_id = self.env.context.get("active_id", False)
        if not journal_sii_document_class_id:
            raise UserError(_("No Journal Document Class as active_id on context"))
        journal_doc_class = self.env["account.journal.sii_document_class"].browse(journal_sii_document_class_id)
        return journal_doc_class.get_pysiiws_consult_invoice(self.name)
