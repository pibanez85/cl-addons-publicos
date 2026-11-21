from markupsafe import escape

from odoo import http
from odoo.http import request

URL_LOGIN_CLAVE = "https://zeusr.sii.cl/cgi_AUT2000/CAutInicio.cgi"

PAGINA = """<!DOCTYPE html>
<html><head><meta charset="utf-8"/><meta name="referrer" content="no-referrer"/><title>SII</title></head>
<body><form id="f" method="post" action="%(accion)s" autocomplete="off">%(campos)s
<noscript><button type="submit">Ingresar al SII</button></noscript></form>
<script>document.getElementById("f").submit();</script></body></html>"""


class SiiPropuestaF29(http.Controller):

    @http.route("/l10n_cl_fe/sii/propuesta_f29/<int:revision_id>/<string:token>", type="http", auth="user",
                methods=["GET"])
    def propuesta_f29(self, revision_id, token, **kw):
        revision = request.env["sii.revision.periodo"].browse(revision_id).exists()
        formulario = revision._formulario_ingreso_sii(token) if revision else False
        if not formulario:
            return request.redirect(revision._url_propuesta_sii() if revision else "/web", local=False)
        campos = "".join(
            '<input type="hidden" name="%s" value="%s"/>' % (escape(k), escape(v)) for k, v in formulario.items()
        )
        return request.make_response(PAGINA % {"accion": URL_LOGIN_CLAVE, "campos": campos}, headers=[
            ("Content-Type", "text/html; charset=utf-8"),
            ("Cache-Control", "no-store, no-cache, must-revalidate, private"),
            ("Pragma", "no-cache"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Content-Type-Options", "nosniff"),
        ])
