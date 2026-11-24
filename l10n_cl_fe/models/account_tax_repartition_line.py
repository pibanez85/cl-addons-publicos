# -*- coding: utf-8 -*-
from odoo import models, fields, _


class AccountTaxRepartitionLine(models.Model):
    _inherit = "account.tax.repartition.line"

    sii_type = fields.Selection(
        [
            ('N', 'Normal'),
            ('A','Anticipado'),
            ('R','Retención'),
        ],
        string="Tipo de impuesto para el SII",
        default='N',
    )
    credec = fields.Boolean(
        string="Credec"
    )

    def es_retencion(self):
        return self.sii_type in ['A', 'R']
