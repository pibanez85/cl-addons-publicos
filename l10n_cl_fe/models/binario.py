"""Ayudas para los campos binarios de Odoo 20.

Desde Odoo 20 un campo ``fields.Binary`` entrega un ``BinaryValue`` (contenido en bruto, no
base64) y al escribir acepta ``BinaryValue`` o un ``str`` en base64; los ``bytes`` sueltos se
rechazan (salvo en ``ir.attachment.raw``).
"""
import base64

from odoo.tools.binary import BinaryBytes, BinaryValue

__all__ = ["BinaryBytes", "contenido_binario"]


def contenido_binario(valor):
    """Bytes en bruto de un valor binario.

    Acepta un ``BinaryValue`` (lo que entrega un campo binario en Odoo 20) o, por compatibilidad
    con quien todavía pasa base64, un ``str``/``bytes`` codificado en base64.
    """
    if not valor:
        return b""
    if isinstance(valor, BinaryValue):
        return valor.content
    return base64.b64decode(valor)
