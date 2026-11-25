import urllib

from odoo import http
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.exceptions import AccessError, MissingError
from odoo.http import request
from odoo.http.stream import content_disposition
from odoo.tools.misc import formatLang


class Boleta(http.Controller):
    @http.route(["/boleta"], type="http", auth="public", website=True)
    def input_document(self, **post):
        if "boleta" not in post:
            return request.render("l10n_cl_fe.boleta_layout")
        return request.redirect("/boleta/{}?{}".format(post["boleta"], urllib.parse.urlencode(post)))

    def _get_domain_account_invoice(self, folio, post_values):
        domain = [("sii_document_number", "=", folio)]
        if post_values.get("date", ""):
            domain.append(("date", "=", post_values.get("date", "")))
        if post_values.get("amount_total", ""):
            domain.append(("amount_total", "=", post_values.get("amount_total", "")))
        if post_values.get("sii_codigo", ""):
            domain.append(("document_class_id.sii_code", "=", int(post_values.get("sii_codigo", ""))))
        else:
            domain.append(("document_class_id.sii_code", "in", [39, 41, 61]))
        return domain

    def get_orders(self, folio, post):
        Model = request.env["account.move"].sudo()
        domain = self._get_domain_account_invoice(folio, post)
        orders = Model.search(domain, limit=1)
        return orders

    @http.route(["/boleta/<int:folio>"], type="http", auth="public", website=True)
    def view_document(self, folio=None, **post):
        if "otra_boleta" in post:
            return request.redirect("/boleta/%s" % (post["otra_boleta"]))
        orders = self.get_orders(folio, post)
        values = {
            "docs": orders,
            "formatLang": formatLang,
            "print_error": not bool(orders),
        }
        return request.render("l10n_cl_fe.boleta_layout", values)

    def _get_report(self, document):
        return request.env["ir.actions.report"].sudo()._render_qweb_pdf("account.account_invoices", [document.id])[0]

    @http.route(["/download/boleta"], type="http", auth="public", website=True)
    def download_boleta(self, model="account.move", model_id=None, access_token=None, **post):
        """PDF de una factura/boleta. Público para la consulta de boletas (/boleta), pero solo
        para quien puede leer el documento o trae su access_token (control de acceso del portal)."""
        if model != "account.move":
            raise request.not_found()
        try:
            document = CustomerPortal()._document_check_access("account.move", int(model_id), access_token)
        except (AccessError, MissingError, TypeError, ValueError):
            raise request.not_found()
        file_name = document._get_report_base_filename()
        pdf = self._get_report(document)
        pdfhttpheaders = [
            ("Content-Type", "application/pdf"),
            ("Content-Length", len(pdf)),
            ("Content-Disposition", content_disposition("%s.pdf" % file_name)),
        ]
        return request.make_response(pdf, headers=pdfhttpheaders)
