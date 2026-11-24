from datetime import date, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_PATRIMONIAL_PREFIXES = ("asset_", "liability_")
_PATRIMONIAL_EXACT = ("equity", "equity_unaffected")
_RESULT_PREFIXES = ("income", "expense")


def _is_patrimonial(account_type):
    if not account_type:
        return False
    if account_type in _PATRIMONIAL_EXACT:
        return True
    return account_type.startswith(_PATRIMONIAL_PREFIXES)


def _is_result(account_type):
    if not account_type:
        return False
    return account_type.startswith(_RESULT_PREFIXES)


class Balance8ColumnasWizard(models.TransientModel):
    _name = "l10n_cl_fe.balance8col.wizard"
    _description = "Balance de 8 Columnas (Chile)"

    company_id = fields.Many2one(
        "res.company",
        string="Compañía",
        required=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        readonly=True,
    )
    date_from = fields.Date(
        string="Desde", required=True,
        default=lambda self: date(date.today().year, 1, 1),
    )
    date_to = fields.Date(
        string="Hasta", required=True,
        default=lambda self: date.today(),
    )
    target_move = fields.Selection(
        [("posted", "Solo asientos contabilizados"), ("all", "Todos los asientos")],
        string="Asientos",
        required=True,
        default="posted",
    )
    only_legal = fields.Boolean(
        string="Solo documentos legales",
        default=True,
        help="Incluye únicamente asientos cuyo account.move tenga "
             "document_class_id (factura, boleta, NC/ND, etc). Los asientos "
             "internos sin documento tributario quedan excluidos del balance.",
    )
    display_account = fields.Selection(
        [
            ("all", "Todas las cuentas"),
            ("movement", "Con movimientos"),
            ("not_zero", "Saldo distinto de cero"),
        ],
        string="Mostrar cuentas",
        required=True,
        default="movement",
    )
    journal_ids = fields.Many2many(
        "account.journal",
        "l10n_cl_fe_balance8col_journal_rel",
        "wizard_id",
        "journal_id",
        string="Diarios",
        help="Si se deja vacío, se incluyen todos los diarios de la compañía.",
    )
    excluded_journal_ids = fields.Many2many(
        "account.journal",
        "l10n_cl_fe_balance8col_excluded_journal_rel",
        "wizard_id",
        "journal_id",
        string="Diarios excluidos",
        help="Diarios que no se incluirán en el reporte.",
    )
    line_ids = fields.One2many(
        "l10n_cl_fe.balance8col.line",
        "wizard_id",
        string="Líneas",
    )
    representante_name = fields.Char(string="Representante Legal")
    representante_rut = fields.Char(string="RUT Representante")
    contador_name = fields.Char(string="Contador")
    contador_rut = fields.Char(string="RUT Contador")
    contador_registro = fields.Char(string="N° Registro Contador")
    lugar_emision = fields.Char(
        string="Lugar de Emisión",
        default=lambda self: self.env.company.city or "",
    )

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for rec in self:
            if rec.date_from and rec.date_to and rec.date_from > rec.date_to:
                raise ValidationError(_("La fecha 'Desde' no puede ser mayor a 'Hasta'."))

    @api.constrains("journal_ids", "excluded_journal_ids")
    def _check_journals(self):
        for rec in self:
            overlap = rec.journal_ids & rec.excluded_journal_ids
            if overlap:
                raise ValidationError(_(
                    "Los siguientes diarios no pueden estar incluidos y excluidos a la vez: %s"
                ) % ", ".join(overlap.mapped("name")))

    def _set_period(self, date_from, date_to):
        self.ensure_one()
        self.write({"date_from": date_from, "date_to": date_to})
        return False

    def action_period_current_year(self):
        today = date.today()
        return self._set_period(date(today.year, 1, 1), today)

    def action_period_previous_year(self):
        today = date.today()
        return self._set_period(
            date(today.year - 1, 1, 1),
            date(today.year - 1, 12, 31),
        )

    def action_period_current_month(self):
        today = date.today()
        if today.month == 12:
            last_day = date(today.year, 12, 31)
        else:
            last_day = date(today.year, today.month + 1, 1) - timedelta(days=1)
        return self._set_period(date(today.year, today.month, 1), last_day)

    def action_period_previous_month(self):
        today = date.today()
        first_current = date(today.year, today.month, 1)
        last_prev = first_current - timedelta(days=1)
        first_prev = date(last_prev.year, last_prev.month, 1)
        return self._set_period(first_prev, last_prev)

    def _build_data(self):
        return {
            "date_from": fields.Date.to_string(self.date_from),
            "date_to": fields.Date.to_string(self.date_to),
            "target_move": self.target_move,
            "only_legal": self.only_legal,
            "display_account": self.display_account,
            "company_id": self.company_id.id,
            "company_name": self.company_id.name,
            "journal_ids": self.journal_ids.ids,
            "excluded_journal_ids": self.excluded_journal_ids.ids,
            "wizard_id": self.id,
            "representante_name": self.representante_name or "",
            "representante_rut": self.representante_rut or "",
            "contador_name": self.contador_name or "",
            "contador_rut": self.contador_rut or "",
            "contador_registro": self.contador_registro or "",
            "lugar_emision": self.lugar_emision or "",
        }

    def _query_account_movements(self, accounts):
        if not accounts:
            return {}
        params = [tuple(accounts.ids), self.company_id.id, self.date_from, self.date_to]
        sql = """
            SELECT aml.account_id AS id,
                   SUM(aml.debit)  AS debit,
                   SUM(aml.credit) AS credit
              FROM account_move_line aml
        """
        if self.only_legal:
            sql += " JOIN account_move am ON am.id = aml.move_id AND am.document_class_id IS NOT NULL "
        sql += """
             WHERE aml.account_id IN %s
               AND aml.company_id = %s
               AND aml.date BETWEEN %s AND %s
               AND aml.parent_state = ANY(%s)
        """
        states = ["posted"] if self.target_move == "posted" else ["posted", "draft"]
        params.append(states)
        if self.journal_ids:
            sql += " AND aml.journal_id IN %s"
            params.append(tuple(self.journal_ids.ids))
        if self.excluded_journal_ids:
            sql += " AND aml.journal_id NOT IN %s"
            params.append(tuple(self.excluded_journal_ids.ids))
        sql += " GROUP BY aml.account_id"
        self.env.cr.execute(sql, tuple(params))
        return {row["id"]: row for row in self.env.cr.dictfetchall()}

    def _build_line_values(self):
        accounts = self.env["account.account"].search(
            [("company_ids", "parent_of", self.company_id.id),
             ("account_type", "!=", "off_balance")],
            order="code",
        )
        movements = self._query_account_movements(accounts)
        company_currency = self.company_id.currency_id
        rows = []
        for account in accounts:
            mv = movements.get(account.id, {"debit": 0.0, "credit": 0.0})
            debit = float(mv["debit"] or 0.0)
            credit = float(mv["credit"] or 0.0)
            balance = debit - credit
            currency = account.currency_id or company_currency
            has_movement = not (currency.is_zero(debit) and currency.is_zero(credit))
            has_balance = not currency.is_zero(balance)
            if self.display_account == "movement" and not has_movement:
                continue
            if self.display_account == "not_zero" and not has_balance:
                continue
            patrimonial = _is_patrimonial(account.account_type)
            result = _is_result(account.account_type)
            deudor = balance if balance > 0 else 0.0
            acreedor = -balance if balance < 0 else 0.0
            rows.append({
                "wizard_id": self.id,
                "account_id": account.id,
                "code": account.code,
                "name": account.name,
                "account_type": account.account_type,
                "currency_id": company_currency.id,
                "debit": debit,
                "credit": credit,
                "deudor": deudor,
                "acreedor": acreedor,
                "activo": deudor if patrimonial else 0.0,
                "pasivo": acreedor if patrimonial else 0.0,
                "perdida": deudor if result else 0.0,
                "ganancia": acreedor if result else 0.0,
            })
        return rows

    def _ensure_lines(self):
        self.ensure_one()
        if self.line_ids:
            return
        rows = self._build_line_values()
        if rows:
            self.env["l10n_cl_fe.balance8col.line"].create(rows)

    def action_compute(self):
        self.ensure_one()
        self.line_ids.unlink()
        self._ensure_lines()
        return False

    def action_print_pdf(self):
        self._ensure_lines()
        return self.env.ref(
            "l10n_cl_fe.action_report_balance_8_columnas"
        ).report_action(self, data={"form": self._build_data()})

    def action_export_xlsx(self):
        self._ensure_lines()
        return self.env.ref(
            "l10n_cl_fe.action_report_balance_8_columnas_xlsx"
        ).report_action(self, data={"form": self._build_data()})


class Balance8ColumnasLine(models.TransientModel):
    _name = "l10n_cl_fe.balance8col.line"
    _description = "Balance de 8 Columnas - Línea"
    _order = "code"

    wizard_id = fields.Many2one(
        "l10n_cl_fe.balance8col.wizard",
        required=True,
        ondelete="cascade",
    )
    account_id = fields.Many2one("account.account", string="Cuenta")
    code = fields.Char(string="Código")
    name = fields.Char(string="Nombre")
    account_type = fields.Selection(
        selection=lambda self: self.env["account.account"]._fields["account_type"].selection,
        string="Tipo",
    )
    currency_id = fields.Many2one("res.currency", string="Moneda")
    debit = fields.Monetary(string="Debe", currency_field="currency_id")
    credit = fields.Monetary(string="Haber", currency_field="currency_id")
    deudor = fields.Monetary(string="Deudor", currency_field="currency_id")
    acreedor = fields.Monetary(string="Acreedor", currency_field="currency_id")
    activo = fields.Monetary(string="Activo", currency_field="currency_id")
    pasivo = fields.Monetary(string="Pasivo", currency_field="currency_id")
    perdida = fields.Monetary(string="Pérdida", currency_field="currency_id")
    ganancia = fields.Monetary(string="Ganancia", currency_field="currency_id")

    def action_print_pdf(self):
        wizard = self.mapped("wizard_id")
        wizard.ensure_one()
        return wizard.action_print_pdf()

    def action_export_xlsx(self):
        wizard = self.mapped("wizard_id")
        wizard.ensure_one()
        return wizard.action_export_xlsx()
