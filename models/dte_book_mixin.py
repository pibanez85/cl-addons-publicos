from odoo import models
from odoo.exceptions import UserError


class DteBookMixin(models.AbstractModel):
    _name = 'l10n_cl_fe.dte.book.mixin'
    _inherit = 'l10n_cl_fe.dte.envio.mixin'
    _description = 'Mixin para docs SII tipo libro (1 doc = 1 cola)'

    _dte_label = "Documento"

    def _dte_send_should_queue(self):
        if self.sii_result not in ('draft', 'NoEnviado', 'Rechazado'):
            raise UserError(f"El {self._dte_label} ya ha sido enviado")
        return True

    def _dte_clean_envio_if_needed(self):
        if not self.sii_xml_request or self.sii_xml_request.state != 'Rechazado':
            return
        if self._dte_send_xml_orphan():
            self.sii_xml_request.unlink()
        else:
            self.sii_xml_request = False

    def _dte_repair_for_send(self):
        self._validar()

    def _get_send_status(self):
        self.sii_xml_request.get_send_status()
        if self.sii_xml_request.state == 'Aceptado':
            self.sii_result = 'Proceso'
        else:
            self.sii_result = self.sii_xml_request.state

    def ask_for_dte_status(self):
        self._get_send_status()

    def get_sii_result(self):
        for r in self:
            if r.sii_xml_request.state == 'NoEnviado':
                r.sii_result = 'EnCola'
                continue
            r.sii_result = r.sii_xml_request.state
