from odoo.tests.common import tagged

from .common_dte import DteCase


@tagged("post_install", "-at_install")
class TestReversion(DteCase):
    """Asistente de reversión (``account.move.reversal``) con los botones de Odoo 20.

    Odoo 20 llama a ``reverse_moves(is_modify=...)`` desde ``refund_moves`` y ``modify_moves``
    ("Revertir y crear borrador"); el override del módulo debe aceptar ese parámetro."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.caf(33, 1, 10)
        cls.caf(61, 1, 10)

    def _factura_publicada(self, **extra):
        factura = self.factura([(self.producto, 2, 1000)], **extra)
        factura.action_post()
        self.assertEqual(factura.state, "posted")
        return factura

    def _asistente(self, factura, **valores):
        return self.env["account.move.reversal"].with_context(
            active_model="account.move", active_ids=factura.ids,
        ).create(dict({
            "move_ids": [(6, 0, factura.ids)],
            "journal_id": factura.journal_id.id,
            "reason": "Anula factura",
        }, **valores))

    def _chileno(self, factura):
        return self._asistente(
            factura, refund_method="cl_refund", tipo_nota=self.clases[61].id, cl_refund="1",
        )

    def _nuevo_borrador(self, accion, factura):
        borrador = self.env["account.move"].browse(accion["res_id"])
        self.assertEqual(borrador.state, "draft")
        self.assertEqual(borrador.move_type, "out_invoice")
        self.assertEqual(borrador.partner_id, factura.partner_id)
        self.assertEqual(borrador.invoice_line_ids.mapped("quantity"), [2])
        return borrador

    def test_modificar_modo_normal(self):
        """Modo normal (sin documento SII): lo resuelve el asistente de Odoo."""
        factura = self._factura_publicada(
            use_documents=False, journal_document_class_id=False, document_class_id=False,
        )
        accion = self._asistente(factura, refund_method="normal").modify_moves()
        self._nuevo_borrador(accion, factura)
        self.assertEqual(factura.payment_state, "reversed")

    def test_modificar_modo_chileno(self):
        """Nota de crédito 61 que anula la factura y un borrador nuevo para corregirla."""
        factura = self._factura_publicada()
        accion = self._chileno(factura).modify_moves()
        self._nuevo_borrador(accion, factura)
        nota = factura.reversal_move_ids
        self.assertEqual(len(nota), 1)
        self.assertEqual(nota.state, "posted")
        self.assertEqual(nota.move_type, "out_refund")
        self.assertEqual(nota.document_class_id, self.clases[61])
        self.assertEqual(factura.payment_state, "reversed")

    def test_reembolso_modo_chileno(self):
        """``refund_moves`` deja la nota de crédito en borrador y la factura sin conciliar."""
        factura = self._factura_publicada()
        accion = self._chileno(factura).refund_moves()
        nota = self.env["account.move"].browse(accion["res_id"])
        self.assertEqual(nota.state, "draft")
        self.assertEqual(nota.move_type, "out_refund")
        self.assertEqual(nota.document_class_id, self.clases[61])
        self.assertEqual(factura.payment_state, "not_paid")
