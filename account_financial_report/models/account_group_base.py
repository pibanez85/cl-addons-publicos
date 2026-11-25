# Part of the code below comes from Odoo 19.0 (addons/account/models/account_account.py),
# Copyright Odoo S.A., licensed under LGPL-3.
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
"""Account groups by code prefix (``account.group``).

Odoo 20 removed ``account.group`` (and ``account.account.group_id``) in favour of a
hierarchy of parent accounts. The trial balance of this module groups accounts by code
prefix, so the model is kept here with the behaviour it had in Odoo 19.
"""

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain
from odoo.tools import SQL


class AccountGroup(models.Model):
    _name = "account.group"
    _description = "Account Group"
    _order = "code_prefix_start"
    _check_company_auto = True
    _check_company_domain = models.check_company_domain_parent_of

    parent_id = fields.Many2one(
        "account.group",
        index=True,
        ondelete="cascade",
        readonly=True,
        check_company=True,
    )
    name = fields.Char(required=True, translate=True)
    code_prefix_start = fields.Char(
        compute="_compute_code_prefix_start",
        readonly=False,
        store=True,
        precompute=True,
    )
    code_prefix_end = fields.Char(
        compute="_compute_code_prefix_end",
        readonly=False,
        store=True,
        precompute=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        readonly=True,
        default=lambda self: self.env.company.root_id,
    )

    _check_length_prefix = models.Constraint(
        "CHECK(char_length(COALESCE(code_prefix_start, '')) "
        "= char_length(COALESCE(code_prefix_end, '')))",
        "The length of the starting and the ending code prefix must be the same",
    )

    @api.depends("code_prefix_start")
    def _compute_code_prefix_end(self):
        for group in self:
            if not group.code_prefix_end or (
                group.code_prefix_start
                and group.code_prefix_end < group.code_prefix_start
            ):
                group.code_prefix_end = group.code_prefix_start

    @api.depends("code_prefix_end")
    def _compute_code_prefix_start(self):
        for group in self:
            if not group.code_prefix_start or (
                group.code_prefix_end
                and group.code_prefix_start > group.code_prefix_end
            ):
                group.code_prefix_start = group.code_prefix_end

    @api.depends("code_prefix_start", "code_prefix_end")
    def _compute_display_name(self):
        for group in self:
            prefix = group.code_prefix_start and str(group.code_prefix_start)
            if prefix and group.code_prefix_end != group.code_prefix_start:
                prefix += "-" + str(group.code_prefix_end)
            group.display_name = " ".join(filter(None, [prefix, group.name]))

    @api.model
    def _search_display_name(self, operator, value):
        if operator in Domain.NEGATIVE_OPERATORS:
            return NotImplemented
        if operator == "in":
            return [
                "|",
                (
                    "code_prefix_start",
                    "in",
                    [(name or "").split(" ")[0] for name in value],
                ),
                ("name", "in", value),
            ]
        if operator == "ilike" and isinstance(value, str):
            return [
                "|",
                ("code_prefix_start", "=ilike", value + "%"),
                ("name", operator, value),
            ]
        return [("name", operator, value)]

    @api.constrains("code_prefix_start", "code_prefix_end")
    def _constraint_prefix_overlap(self):
        self.flush_model()
        query = SQL(
            """
            SELECT other.id FROM account_group this
            JOIN account_group other
              ON char_length(other.code_prefix_start) = char_length(this.code_prefix_start)
             AND other.id != this.id
             AND other.company_id = this.company_id
             AND (
                other.code_prefix_start <= this.code_prefix_start
                AND this.code_prefix_start <= other.code_prefix_end
                OR
                other.code_prefix_start >= this.code_prefix_start
                AND this.code_prefix_end >= other.code_prefix_start
            )
            WHERE this.id IN %s
            """,
            tuple(self.ids),
        )
        if self.env.execute_query(query):
            raise ValidationError(
                self.env._("Account Groups with the same granularity can't overlap")
            )

    def _sanitize_vals(self, vals):
        if (
            vals.get("code_prefix_start")
            and "code_prefix_end" in vals
            and not vals["code_prefix_end"]
        ):
            del vals["code_prefix_end"]
        if (
            vals.get("code_prefix_end")
            and "code_prefix_start" in vals
            and not vals["code_prefix_start"]
        ):
            del vals["code_prefix_start"]
        return vals

    @api.constrains("parent_id")
    def _check_parent_not_circular(self):
        if self._has_cycle():
            raise ValidationError(self.env._("You cannot create recursive groups."))

    @api.model_create_multi
    def create(self, vals_list):
        groups = super().create([self._sanitize_vals(vals) for vals in vals_list])
        groups._adapt_parent_account_group()
        return groups

    def write(self, vals):
        res = super().write(self._sanitize_vals(vals))
        if "code_prefix_start" in vals or "code_prefix_end" in vals:
            self._adapt_parent_account_group()
        return res

    def unlink(self):
        for record in self:
            children = self.search([("parent_id", "=", record.id)])
            children.write({"parent_id": record.parent_id.id})
        return super().unlink()

    def _adapt_parent_account_group(self, company=None):
        """Ensure consistency of the hierarchy of account groups.

        Find and set the most specific parent for each group: the one with the longest
        prefixes, with the starting prefix smaller than the child prefixes and the
        ending prefix greater.
        """
        if self.env.context.get("delay_account_group_sync"):
            return
        company_ids = company.ids if company else self.company_id.ids
        if not company_ids:
            return
        self.flush_model()
        query = SQL(
            """
            WITH relation AS (
                SELECT DISTINCT ON (child.id)
                       child.id AS child_id,
                       parent.id AS parent_id
                  FROM account_group parent
            RIGHT JOIN account_group child
                    ON char_length(parent.code_prefix_start)
                       < char_length(child.code_prefix_start)
                   AND parent.code_prefix_start
                       <= LEFT(child.code_prefix_start, char_length(parent.code_prefix_start))
                   AND parent.code_prefix_end
                       >= LEFT(child.code_prefix_end, char_length(parent.code_prefix_end))
                   AND parent.id != child.id
                   AND parent.company_id = child.company_id
                 WHERE child.company_id IN %s
              ORDER BY child.id, char_length(parent.code_prefix_start) DESC
            )
            UPDATE account_group child
               SET parent_id = relation.parent_id
              FROM relation
             WHERE child.id = relation.child_id
               AND child.parent_id IS DISTINCT FROM relation.parent_id
         RETURNING child.id
            """,
            tuple(company_ids),
        )
        if self.env.execute_query(query):
            self.invalidate_model(["parent_id"])


class AccountAccount(models.Model):
    _inherit = "account.account"

    group_id = fields.Many2one(
        "account.group",
        compute="_compute_account_group",
        help="Account prefixes can determine account groups.",
    )

    @api.depends_context("company")
    @api.depends("code")
    def _compute_account_group(self):
        accounts_with_code = self.filtered(lambda a: a.code)
        (self - accounts_with_code).group_id = False
        if not accounts_with_code:
            return
        codes = accounts_with_code.mapped("code")
        account_code_values = SQL(",".join(["(%s)"] * len(codes)), *codes)
        results = self.env.execute_query(
            SQL(
                """
                 SELECT DISTINCT ON (account_code.code)
                        account_code.code,
                        agroup.id AS group_id
                   FROM (VALUES %(account_code_values)s) AS account_code (code)
              LEFT JOIN account_group agroup
                     ON agroup.code_prefix_start
                        <= LEFT(account_code.code, char_length(agroup.code_prefix_start))
                        AND agroup.code_prefix_end
                        >= LEFT(account_code.code, char_length(agroup.code_prefix_end))
                        AND agroup.company_id = %(root_company_id)s
               ORDER BY account_code.code, char_length(agroup.code_prefix_start) DESC,
                        agroup.id
                """,
                account_code_values=account_code_values,
                root_company_id=self.env.company.root_id.id,
            )
        )
        group_by_code = dict(results)
        for account in accounts_with_code:
            account.group_id = group_by_code[account.code]
