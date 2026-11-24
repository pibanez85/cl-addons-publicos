from odoo import fields, models


class DteTransporteMixin(models.AbstractModel):
    _name = "l10n_cl_fe.dte.transporte.mixin"
    _description = "Datos de transporte del DTE"

    transport_type = fields.Selection(
        [
            ('1', 'Despacho por cuenta del receptor'),
            ('2', 'Despacho por cuenta del emisor a instalaciones del cliente'),
            ('3', 'Despacho por cuenta del emisor a otras instalaciones'),
        ],
        string="Tipo de Despacho",
        copy=False,
    )
    patente = fields.Char(string="Patente", copy=False, help="Si aún no se conoce, ingrese DIFERIDO o SIN DATO")
    patente_carro = fields.Char(string="Patente Carro", copy=False)
    chofer = fields.Many2one(
        "res.partner",
        string="Chofer",
        copy=False,
        domain="['|', '|', ('parent_id', '=', False), ('user_ids', '!=', False), ('document_number', '!=', False)]",
        help="Contacto del conductor. Debe poder llevar RUT propio: un contacto "
             "sin empresa madre o el contacto de un usuario interno",
    )
    transportista_id = fields.Many2one("res.partner", string="Transportista", copy=False)
    transportista_por_confirmar = fields.Boolean(string="Transportista por confirmar", copy=False)
    fecha_salida = fields.Datetime(string="Fecha y hora de salida", copy=False)
    fecha_llegada = fields.Date(string="Fecha de llegada", copy=False)

    def _campos_transporte(self):
        return [
            'transport_type', 'patente', 'patente_carro', 'chofer', 'transportista_id',
            'transportista_por_confirmar', 'fecha_salida', 'fecha_llegada',
        ]

    def _valores_transporte(self, modelo):
        self.ensure_one()
        if not self.transport_type:
            return {}
        destino = self.env[modelo]._fields
        campos = [c for c in self._campos_transporte() if c in destino and self[c]]
        return self._convert_to_write({c: self[c] for c in campos})
