import logging
from datetime import datetime

import dateutil.relativedelta as relativedelta

from odoo import SUPERUSER_ID, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.translate import _

_logger = logging.getLogger(__name__)

# El .p12, su clave y la llave privada: solo contadores y administradores. La firma de
# documentos lee estos campos con sudo (res.users.get_digital_signature).
GRUPOS_SECRETOS = "account.group_account_manager,base.group_system"
try:
    from facturacion_electronica.firma import Firma
    # Alias: el modelo de abajo también se llama SignatureCert y tapaba la clase de la biblioteca
    # (action_process instanciaba el modelo de Odoo y no se podía cargar ningún .p12).
    from facturacion_electronica.signature_cert import SignatureCert as FeSignatureCert
except ImportError:
    _logger.warning("Problema a cargar facturacion_electronica")



class SignatureCert(models.Model):
    _name = "sii.firma"
    _description = "Certificado Digital para Firma Electrónica SII"

    _DIAS_AVISO_INTENSIVO = 3

    def _expiration_message(self, days_left):
        return _(
            "La firma electrónica '%(name)s' (RUT %(rut)s) vence el %(date)s "
            "(%(days)s días restantes). ⚠ Si no la renueva a tiempo, NO podrá "
            "emitir documentos tributarios electrónicos (facturas, boletas, "
            "notas de crédito/débito, guías) al SII hasta que se actualice."
        ) % {
            "name": self.name,
            "rut": self.subject_serial_number or "-",
            "date": self.expire_date.strftime("%d-%m-%Y"),
            "days": days_left,
        }

    def alerta_vencimiento(self):
        self.ensure_one()
        if not self.expire_date or not self.user_ids:
            return
        today = fields.Date.context_today(self)
        days_left = (self.expire_date - today).days
        if days_left > 30:
            return
        Activity = self.env["mail.activity"].sudo()
        activity_type = self.env.ref(
            "mail.mail_activity_data_warning", raise_if_not_found=False,
        ) or self.env.ref(
            "mail.mail_activity_data_todo", raise_if_not_found=False,
        )
        if not activity_type:
            return
        summary = _("Firma Electrónica próxima a vencer")
        res_model_id = self.env["ir.model"]._get(self._name).id
        note = self._expiration_message(days_left)
        intensivo = days_left <= self._DIAS_AVISO_INTENSIVO
        for user in self.user_ids:
            base_domain = [
                ("res_model_id", "=", res_model_id),
                ("res_id", "=", self.id),
                ("user_id", "=", user.id),
                ("activity_type_id", "=", activity_type.id),
                ("summary", "=", summary),
            ]
            if intensivo:
                day_start = datetime.combine(today, datetime.min.time())
                day_end = day_start + relativedelta.relativedelta(days=1)
                today_activity = Activity.search(
                    base_domain + [
                        ("create_date", ">=", day_start),
                        ("create_date", "<", day_end),
                    ], limit=1,
                )
                if today_activity:
                    today_activity.write({
                        "date_deadline": self.expire_date, "note": note,
                    })
                    continue
                Activity.create({
                    "res_model_id": res_model_id,
                    "res_id": self.id,
                    "user_id": user.id,
                    "activity_type_id": activity_type.id,
                    "summary": summary,
                    "note": note,
                    "date_deadline": self.expire_date,
                })
                continue
            existing = Activity.search(base_domain, limit=1)
            if existing:
                existing.write({
                    "date_deadline": self.expire_date, "note": note,
                })
                continue
            Activity.create({
                "res_model_id": res_model_id,
                "res_id": self.id,
                "user_id": user.id,
                "activity_type_id": activity_type.id,
                "summary": summary,
                "note": note,
                "date_deadline": self.expire_date,
            })

    def notify_use_expiration(self):
        self.ensure_one()
        if not self.expire_date:
            return
        today = fields.Date.context_today(self)
        days_left = (self.expire_date - today).days
        if days_left > 30:
            return
        intensivo = days_left <= self._DIAS_AVISO_INTENSIVO
        user = self.env.user.sudo()
        if not intensivo:
            if user.sii_firma_aviso_id == self and user.sii_firma_aviso_fecha == today:
                return
            user.write({"sii_firma_aviso_id": self.id, "sii_firma_aviso_fecha": today})
        message = self._expiration_message(days_left)
        notif_type = "danger" if intensivo else "warning"
        try:
            self.env["bus.bus"]._sendone(
                self.env.user.partner_id,
                "simple_notification",
                {
                    "title": _("Firma Electrónica por vencer"),
                    "message": message,
                    "type": notif_type,
                    "sticky": True,
                },
            )
        except Exception as e:
            _logger.warning("No se pudo enviar toast de firma: %s", e)

    @api.model
    def _cron_alerta_vencimiento(self):
        firmas = self.search([("active", "=", True), ("state", "=", "valid")])
        for firma in firmas:
            try:
                firma.alerta_vencimiento()
            except Exception as e:
                _logger.warning(
                    "No se pudo crear alerta de vencimiento para firma %s: %s",
                    firma.display_name, e,
                )

    def _firma_autorizada(self, company):
        """La firma (con sudo) si el usuario actual puede usarla en ``company``; si no, vacío.

        Mismas reglas que ``res.users.get_digital_signature``: el usuario (o el admin, si es el
        superusuario o un usuario portal) está en ``user_ids``, la compañía en ``company_ids``,
        la firma vigente y activa. En modo sudo (procesos internos) se acepta la firma indicada.
        Sirve para no confiar en un ``firma_id`` que viene en el contexto de una llamada RPC.
        """
        firma = self.sudo().exists()[:1]
        if not firma or not company:
            return self.env["sii.firma"]
        if self.env.su:
            return firma
        user = self.env.user
        if user.id == SUPERUSER_ID or user.has_group("base.group_portal"):
            user = self.env.ref("base.user_admin")
        if user not in firma.user_ids or company not in firma.company_ids:
            return self.env["sii.firma"]
        firma.check_signature()
        if firma.state != "valid" or not firma.active:
            return self.env["sii.firma"]
        return firma

    def check_signature(self):
        for s in self.sudo():
            expired = s.expire_date < fields.Date.context_today(self)
            state = "expired" if expired else "valid"
            if s.state != state:
                s.state = state
                s.active = not expired

    @api.onchange("subject_serial_number")
    def set_state(self):
        if self.subject_serial_number:
            check_rut = rut = self.subject_serial_number.replace(".", "").upper()
            if len(rut) == 9:
                check_rut = "0" + rut
            if "-" not in check_rut or not self.env.user.partner_id.check_vat_cl(check_rut.replace("-", "")):
                raise UserError(_("Not Valid Subject Serial Number"))
            self.subject_serial_number = rut
        elif self.file_content:
            self.state = "incomplete"

    name = fields.Char(string="File Name", required=True,)
    file_content = fields.Binary(string="Signature File", help="Upload the Signature File", groups=GRUPOS_SECRETOS)
    password = fields.Char(string="Password", groups=GRUPOS_SECRETOS)
    emision_date = fields.Date(string="Emision Date", help="Not Before this Date", readonly=True,)
    expire_date = fields.Date(string="Expire Date", help="Not After this Date", readonly=True,)
    state = fields.Selection(
        [("unverified", "Unverified"), ("incomplete", "Incomplete"), ("valid", "Valid"), ("expired", "Expired")],
        string="state",
        default="unverified",
        help="""Draft: means it has not been checked yet.\nYou must press the\
"check" button.""",
    )
    subject_title = fields.Char(string="Subject Title", readonly=True)
    subject_c = fields.Char(string="Subject Country", readonly=True)
    subject_serial_number = fields.Char(
        string="Subject Serial Number",
        readonly=False,
    )
    subject_common_name = fields.Char(string="Subject Common Name", readonly=True)
    subject_email_address = fields.Char(string="Subject Email Address", readonly=True)
    issuer_country = fields.Char(string="Issuer Country", readonly=True)
    issuer_serial_number = fields.Char(string="Issuer Serial Number", readonly=True)
    issuer_common_name = fields.Char(string="Issuer Common Name", readonly=True)
    issuer_email_address = fields.Char(string="Issuer Email Address", readonly=True)
    issuer_organization = fields.Char(string="Issuer Organization", readonly=True)
    cert_serial_number = fields.Char(string="Serial Number", readonly=True)
    cert_signature_algor = fields.Char(string="Signature Algorithm", readonly=True)
    cert_version = fields.Char(string="Version", readonly=True)
    cert_hash = fields.Char(string="Hash", readonly=True)
    private_key_bits = fields.Char(string="Private Key Bits", readonly=True)
    private_key_check = fields.Char(string="Private Key Check", readonly=True)
    private_key_type = fields.Char(string="Private Key Type", readonly=True)
    cert = fields.Text(string="Certificate", readonly=True, groups=GRUPOS_SECRETOS)
    priv_key = fields.Text(string="Private Key", readonly=True, groups=GRUPOS_SECRETOS)
    user_ids = fields.Many2many("res.users", string="Authorized Users", default=lambda self: [self.env.uid],)
    company_ids = fields.Many2many(
        "res.company", string="Authorized Companies", default=lambda self: [self.env.company.id], required=True,
    )
    priority = fields.Integer(string="Priority", default=1,)
    active = fields.Boolean(string="Active", default=True,)

    _order = "priority DESC"

    @api.constrains("name", "subject_serial_number", "active")
    def _check_unique_active(self):
        for rec in self:
            if not rec.active:
                continue
            duplicate = self.search([
                ("id", "!=", rec.id),
                ("name", "=", rec.name),
                ("subject_serial_number", "=", rec.subject_serial_number),
                ("active", "=", True),
            ], limit=1)
            if duplicate:
                raise ValidationError(_(
                    "Ya existe una firma activa con el mismo archivo y RUT (%s)."
                ) % rec.name)


    def action_process(self):
        if self.subject_serial_number:
            return self.check_signature()
        firma = FeSignatureCert({
            "string_firma": self.file_content.to_base64(),
            "string_password": self.password or None,
            "init_signature": True,
        })
        if firma.errores:
            raise UserError(
                "Error al abrir la firma, posiblmente ha ingresado\
 mal la clave de la firma o el archivo no es compatible."
            )
        self.write(
            {
                "emision_date": firma.not_before,
                "expire_date": firma.not_after,
                "subject_c": firma.subject_c,
                "subject_title": firma.subject_title,
                "subject_common_name": firma.subject_common_name,
                "subject_serial_number": firma.subject_serial_number,
                "subject_email_address": firma.subject_email_address,
                "issuer_country": firma.issuer_country,
                "issuer_organization": firma.issuer_organization,
                "issuer_common_name": firma.issuer_common_name,
                "issuer_serial_number": firma.issuer_serial_number,
                "issuer_email_address": firma.issuer_email_address,
                "cert_serial_number": str(firma.cert_serial_number),
                "cert_signature_algor": firma.cert_signature_algor,
                "cert_version": str(firma.cert_version),
                "cert_hash": firma.cert_hash,
                "private_key_bits": str(firma.private_key_bits),
                "private_key_check": True,
                "private_key_type": firma.private_key_type,
                "priv_key": firma.priv_key.decode(),
                "cert": firma.cert.decode(),
                "password": False,
            }
        )
        self.set_state()
        # Deja la firma vigente (o vencida) de inmediato: antes quedaba "unverified" hasta
        # volver a pulsar el botón y get_digital_signature no la encontraba.
        self.check_signature()

    def parametros_firma(self):
        return {
            "priv_key": self.priv_key,
            "cert": self.cert,
            "rut_firmante": self.subject_serial_number,
            "init_signature": False,
        }

    def firmar(self, string, uri=False, type="doc"):
        firma = Firma(self.parametros_firma())
        return firma.firmar(string=string, uri=uri, type=type)

    def generar_firma(self, ddxml, privkey=False):
        params = self.parametros_firma()
        if privkey:
            params["priv_key"] = privkey
        firma = Firma(params)
        return firma.generar_firma(texto=ddxml.decode())
