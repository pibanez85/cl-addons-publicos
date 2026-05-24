from odoo import fields, models


class SOR(models.Model):
    _name = "sale.order.referencias"
    _inherit = "l10n_cl_fe.dte.referencia.mixin"
    _description = "Línea de Referencias DTE para Pedidos de Venta"

    so_id = fields.Many2one("sale.order", ondelete="cascade", index=True, copy=False, string="Documento")

    def _dte_referencia_parent(self):
        return self.so_id
