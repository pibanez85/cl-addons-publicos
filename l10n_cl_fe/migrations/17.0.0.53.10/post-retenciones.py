import logging

from odoo import SUPERUSER_ID, Command, api

_logger = logging.getLogger(__name__)


def _iva_compra_line(env, tax, document_type):
    candidatos = env['account.tax'].with_context(active_test=False).search([
        ('company_id', '=', tax.company_id.id),
        ('type_tax_use', '=', 'purchase'),
        ('sii_code', '=', 14),
        ('amount', '>', 0),
        ('amount_type', '=', 'percent'),
    ], order='active desc, sequence, id')
    for iva in candidatos:
        lineas = iva.repartition_line_ids.filtered(
            lambda l: l.document_type == document_type and l.repartition_type == 'tax')
        if lineas and all(l.sii_type not in ('R', 'A') for l in lineas):
            return lineas[:1]
    return env['account.tax.repartition.line']


def _convertir(env, tax):
    cambios = []
    vals = {}
    comandos = []
    if tax.amount < 0:
        vals['amount'] = -tax.amount
        cambios.append(f"amount {tax.amount} -> {-tax.amount}")
    for linea in tax.repartition_line_ids.filtered(lambda l: l.sii_type == 'A' and not l.credec):
        comandos.append(Command.update(linea.id, {'credec': True}))
        cambios.append(f"credec en línea A {linea.id}")

    if tax.sii_code == 15:
        for document_type in ('invoice', 'refund'):
            lineas = tax.repartition_line_ids.filtered(lambda l: l.document_type == document_type)
            tax_r = lineas.filtered(lambda l: l.repartition_type == 'tax' and l.sii_type == 'R')
            tax_n = lineas.filtered(lambda l: l.repartition_type == 'tax' and l.sii_type not in ('R', 'A'))
            for linea in tax_r.filtered(lambda l: l.factor_percent > 0):
                comandos.append(Command.update(linea.id, {'factor_percent': -linea.factor_percent}))
                cambios.append(f"factor R {document_type} {linea.factor_percent} -> {-linea.factor_percent}")
            if tax_r and not tax_n:
                modelo = _iva_compra_line(env, tax, document_type)
                if not modelo:
                    _logger.warning(
                        "Retenciones: impuesto %s (%s) sin IVA Compra de referencia; "
                        "la línea N %s se crea sin cuenta", tax.id, tax.name, document_type)
                base = lineas.filtered(lambda l: l.repartition_type == 'base')[:1]
                comandos.append(Command.create({
                    'document_type': document_type,
                    'repartition_type': 'tax',
                    'factor_percent': sum(abs(l.factor_percent) for l in tax_r),
                    'sii_type': 'N',
                    'account_id': modelo.account_id.id,
                    'tag_ids': [Command.set(modelo.tag_ids.ids)],
                    'use_in_tax_closing': modelo.use_in_tax_closing if modelo else True,
                    'sequence': (base.sequence or 0) + 1,
                }))
                for linea in tax_r:
                    comandos.append(Command.update(linea.id, {'sequence': (base.sequence or 0) + 2}))
                cambios.append(f"línea N {document_type} agregada (cuenta {modelo.account_id.code or '-'})")

    if comandos:
        vals['repartition_line_ids'] = comandos
    if vals:
        tax.write(vals)
        _logger.info("Retenciones: impuesto %s (%s): %s", tax.id, tax.name, "; ".join(cambios))


def migrate(cr, version):
    if not version:
        return
    _logger.info(
        f"POST-MIGRATION: {version} -> 17.0.0.53.10 - impuestos de retención (R/A) a la "
        "convención de 16.0: monto positivo, Retención Total IVA con línea N +100 y R -100, "
        "credec en líneas A"
    )
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute("""
        SELECT DISTINCT tax_id
          FROM account_tax_repartition_line
         WHERE sii_type IN ('R', 'A')
           AND tax_id IS NOT NULL
    """)
    tax_ids = [r[0] for r in cr.fetchall()]
    for tax in env['account.tax'].with_context(active_test=False).browse(tax_ids):
        _convertir(env, tax)
