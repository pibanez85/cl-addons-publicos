import math
import re
import urllib

from odoo import fields, http
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.exceptions import AccessError, MissingError
from odoo.http import request
from odoo.http.stream import content_disposition
from odoo.tools.misc import formatLang


# Consulta pública de boletas: solo boletas afectas/exentas y sus notas de crédito.
CODIGOS_CONSULTA = (39, 41, 61)
CAMPOS_CONSULTA = ("sii_codigo", "date", "amount_total", "rut_emisor")


class Boleta(http.Controller):
    @http.route(["/boleta"], type="http", auth="public", website=True)
    def input_document(self, **post):
        if "boleta" not in post:
            return request.render("l10n_cl_fe.boleta_layout")
        return request.redirect("/boleta/{}?{}".format(
            post["boleta"], urllib.parse.urlencode(self._params_consulta(post))))

    def _params_consulta(self, post):
        return {campo: post[campo] for campo in CAMPOS_CONSULTA if post.get(campo)}

    @staticmethod
    def _rut(valor):
        return re.sub(r"[^0-9K]", "", (valor or "").upper()).lstrip("0")

    def _get_company(self, rut_emisor):
        rut = self._rut(rut_emisor)
        if not rut:
            return request.env["res.company"]
        companies = request.env["res.company"].sudo().search([])
        return companies.filtered(lambda c: self._rut(c.document_number) == rut)[:1]

    def _get_domain_account_invoice(self, folio, post_values):
        """Dominio de la consulta, o None si faltan datos: la búsqueda es con sudo y pública, así
        que exige folio, fecha, monto total exacto y RUT del emisor, y solo CODIGOS_CONSULTA."""
        try:
            sii_codigo = int(post_values.get("sii_codigo") or 39)
            amount_total = float(post_values.get("amount_total") or "")
            date = fields.Date.to_date(post_values.get("date") or None)
        except (TypeError, ValueError):
            return None
        if not folio or not date or not math.isfinite(amount_total) or sii_codigo not in CODIGOS_CONSULTA:
            return None
        company = self._get_company(post_values.get("rut_emisor"))
        if not company:
            return None
        return [
            ("sii_document_number", "=", folio),
            ("company_id", "=", company.id),
            ("state", "=", "posted"),
            ("date", "=", date),
            ("amount_total", "=", amount_total),
            ("document_class_id.sii_code", "=", sii_codigo),
        ]

    def get_orders(self, folio, post):
        domain = self._get_domain_account_invoice(folio, post)
        if domain is None:
            return request.env["account.move"]
        return request.env["account.move"].sudo().search(domain, limit=1)

    @http.route(["/boleta/<int:folio>"], type="http", auth="public", website=True)
    def view_document(self, folio=None, **post):
        if "otra_boleta" in post:
            return request.redirect("/boleta/%s" % (post["otra_boleta"]))
        orders = self.get_orders(folio, post)
        values = {
            "docs": orders,
            "formatLang": formatLang,
            "print_error": not bool(orders),
            # El PDF se pide con los mismos datos de la consulta y se valida de nuevo: no se
            # entrega ni se guarda un access_token del portal.
            "report_url": "/boleta/%s/pdf?%s" % (folio, urllib.parse.urlencode(self._params_consulta(post))),
        }
        return request.render("l10n_cl_fe.boleta_layout", values)

    @http.route(["/boleta/<int:folio>/pdf"], type="http", auth="public", website=True)
    def download_document(self, folio=None, **post):
        document = self.get_orders(folio, post)
        if not document:
            raise request.not_found()
        return self._pdf_response(document)

    def _get_report(self, document):
        return request.env["ir.actions.report"].sudo()._render_qweb_pdf("account.account_invoices", [document.id])[0]

    @http.route(["/download/boleta"], type="http", auth="public", website=True)
    def download_boleta(self, model="account.move", model_id=None, access_token=None, **post):
        """PDF de una factura/boleta para quien puede leerla o trae su access_token (control de
        acceso del portal). La consulta pública /boleta usa /boleta/<folio>/pdf."""
        if model != "account.move":
            raise request.not_found()
        try:
            document = CustomerPortal()._document_check_access("account.move", int(model_id), access_token)
        except (AccessError, MissingError, TypeError, ValueError):
            raise request.not_found()
        return self._pdf_response(document)

    def _pdf_response(self, document):
        file_name = document._get_report_base_filename()
        pdf = self._get_report(document)
        pdfhttpheaders = [
            ("Content-Type", "application/pdf"),
            ("Content-Length", len(pdf)),
            ("Content-Disposition", content_disposition("%s.pdf" % file_name)),
        ]
        return request.make_response(pdf, headers=pdfhttpheaders)
