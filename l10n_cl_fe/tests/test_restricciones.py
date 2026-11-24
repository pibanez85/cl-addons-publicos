from psycopg2 import IntegrityError

from odoo.tests.common import TransactionCase, tagged
from odoo.tools import mute_logger


@tagged("post_install", "-at_install")
class TestRestricciones(TransactionCase):
    """Odoo 20 ignora _sql_constraints: las restricciones únicas se declaran con models.Constraint."""

    def test_caf_no_se_puede_subir_dos_veces(self):
        # Un CAF repetido duplicaría el rango de folios autorizado por el SII.
        Caf = self.env["dte.caf"]
        Caf.create({"filename": "FoliosSII760864285331.xml", "company_id": self.env.company.id})
        with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError), self.cr.savepoint():
            Caf.create({"filename": "FoliosSII760864285331.xml", "company_id": self.env.company.id})

    def test_restricciones_unicas_existen_en_la_base(self):
        esperadas = {
            "dte.caf": "filename_unique",
            "account.move.comision": "name_uniq_per_move",
            "sii.revision.periodo": "periodo_unico",
            "sii.document_letter": "name_uniq",
            "sii.responsability": "name_uniq",
        }
        esperadas = list(esperadas.items()) + [("sii.responsability", "code_uniq")]
        for modelo, nombre in esperadas:
            with self.subTest(modelo=modelo):
                tabla = self.env[modelo]._table
                self.cr.execute(
                    "SELECT 1 FROM pg_constraint WHERE conname = %s", ["%s_%s" % (tabla, nombre)])
                self.assertTrue(self.cr.fetchone(), "%s_%s" % (tabla, nombre))
