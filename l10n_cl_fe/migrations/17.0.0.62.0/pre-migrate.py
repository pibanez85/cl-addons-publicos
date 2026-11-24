MODELOS_MULTIEMPRESA = (
    "account.move.book",
    "account.move.book.honorarios",
    "account.move.consumo_folios",
    "account.move.boleta_voucher",
    "dte.caf",
    "mail.message.dte",
    "mail.message.dte.document",
    "sii.xml.envio",
    "sii.cola_envio",
    "sii.respuesta.cliente",
    "sii.sucursal",
    "account.tax.mepco",
    "account.journal.sii_document_class",
)


def migrate(cr, version):
    cr.execute("""
        DELETE FROM ir_rule r
         USING ir_model m
         WHERE m.id = r.model_id
           AND (m.model IN %s OR m.model LIKE 'sii.revision.%%')
           AND r.name LIKE 'Multiempresa: %%'
           AND NOT EXISTS (
               SELECT 1 FROM ir_model_data d WHERE d.model = 'ir.rule' AND d.res_id = r.id
           )
    """, [MODELOS_MULTIEMPRESA])
