def migrate(cr, version):
    cr.execute("""
        UPDATE account_journal_sii_document_class d
           SET company_id = j.company_id
          FROM account_journal j
         WHERE j.id = d.journal_id
           AND d.company_id IS DISTINCT FROM j.company_id
    """)
