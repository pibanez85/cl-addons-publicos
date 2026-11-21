# Copyright 2015 ACSONE SA/NV (<http://acsone.eu>)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).

from collections import OrderedDict

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ReportAction(models.Model):
    _inherit = "ir.actions.report"

    report_type = fields.Selection(
        selection_add=[("xlsx", "XLSX")], ondelete={"xlsx": "set default"}
    )

    @api.model
    def _render_xlsx(self, docids, data):
        report_model_name = "report.%s" % self.report_name
        report_model = self.env.get(report_model_name)
        if report_model is None:
            raise UserError(_("%s model was not found") % report_model_name)
        return report_model.with_context(
            active_model=self.model
        ).create_xlsx_report(  # noqa
            docids, data
        )

    @api.model
    def _get_report_from_name(self, report_name):
        res = super(ReportAction, self)._get_report_from_name(report_name)
        if res:
            return res
        report_obj = self.env["ir.actions.report"]
        qwebtypes = ["xlsx"]
        conditions = [
            ("report_type", "in", qwebtypes),
            ("report_name", "=", report_name),
        ]
        context = self.env["res.users"].context_get()
        return report_obj.with_context(context).search(conditions, limit=1)

    def render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        return self._render_qweb_pdf(report_ref, res_ids, data)

    @api.model
    def _l10n_cl_fe_es_angosto(self, paperformat):
        return bool(
            paperformat
            and paperformat.format == "custom"
            and paperformat.page_width
            and paperformat.page_width < 120
        )

    def get_paperformat(self):
        paperformat = super().get_paperformat()
        formato = self.env.context.get("l10n_cl_fe_formato")
        angosto = self._l10n_cl_fe_es_angosto(paperformat)
        if formato == "completo" and angosto:
            return self.env.company.paperformat_id or paperformat
        if formato == "ticket" and not angosto:
            principal = self.env.ref(
                "account.account_invoices", raise_if_not_found=False
            )
            if principal and self._l10n_cl_fe_es_angosto(principal.sudo().paperformat_id):
                return principal.sudo().paperformat_id
        return paperformat

    def _render_qweb_pdf_prepare_streams(self, report_ref, data, res_ids=None):
        report = self._get_report(report_ref)
        if (
            not res_ids
            or report.model != "account.move"
            or not self._is_invoice_report(report_ref)
        ):
            return super()._render_qweb_pdf_prepare_streams(
                report_ref, data, res_ids=res_ids
            )
        moves = self.env["account.move"].browse(res_ids)
        tickets = moves.filtered(
            lambda m: m._get_name_invoice_report() == "l10n_cl_fe.report_ticket"
        )
        grupos = [
            (formato, grupo)
            for formato, grupo in (("ticket", tickets), ("completo", moves - tickets))
            if grupo
        ]
        resultados = {}
        for formato, grupo in grupos:
            ref = report_ref
            if isinstance(report_ref, models.Model):
                ref = report_ref.with_context(l10n_cl_fe_formato=formato)
            resultados[formato] = super(
                ReportAction, self.with_context(l10n_cl_fe_formato=formato)
            )._render_qweb_pdf_prepare_streams(
                ref, data, res_ids=res_ids if len(grupos) == 1 else grupo.ids
            )
        if len(grupos) == 1:
            return resultados[grupos[0][0]]
        streams = OrderedDict()
        for res_id in res_ids:
            formato = "ticket" if res_id in tickets.ids else "completo"
            resultado = resultados[formato]
            if res_id in resultado:
                streams[res_id] = resultado[res_id]
            elif False in resultado and ("l10n_cl_fe", formato) not in streams:
                streams[("l10n_cl_fe", formato)] = resultado[False]
        return streams
