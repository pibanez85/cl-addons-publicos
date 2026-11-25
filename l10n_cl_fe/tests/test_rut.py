from odoo.exceptions import ValidationError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRut(TransactionCase):
    """El RUT (document_number) se guarda en una sola forma canónica: puntos de miles, guion y
    dígito verificador en mayúscula (``76.086.428-5``), y se valida el dígito verificador."""

    def _guardado(self, rut, nombre="Contacto RUT"):
        return self.env["res.partner"].create({"name": nombre, "document_number": rut}).document_number

    def test_rut_formatos(self):
        for rut in ("76.086.428-5", "760864285", "76086428-5", " 76086428-5 "):
            with self.subTest(rut=rut):
                self.assertEqual(self._guardado(rut), "76.086.428-5")
        for rut in ("10.000.013-k", "10000013-K"):
            with self.subTest(rut=rut):
                self.assertEqual(self._guardado(rut), "10.000.013-K")
        # Al escribir también se normaliza, y un RUT de menos de 10 millones no lleva cero inicial.
        socio = self.env["res.partner"].create({"name": "Persona"})
        socio.document_number = "12345674"
        self.assertEqual(socio.document_number, "1.234.567-4")
        # El formulario (onchange) deja la misma forma que el ORM.
        with Form(self.env["res.partner"]) as contacto:
            contacto.name = "Desde formulario"
            contacto.document_number = "10000013k"
            contacto.dte_email = "dte@formulario.cl"
            self.assertEqual(contacto.document_number, "10.000.013-K")
        # La forma que va al XML del DTE (RUTEmisor/RUTRecep) sale de rut(): sin puntos.
        self.assertEqual(contacto.record.rut(), "10000013-K")

    def test_rut_invalido(self):
        with self.assertRaises(ValidationError):
            self._guardado("76.086.428-4")
        socio = self.env["res.partner"].create({"name": "Otro", "document_number": "76.086.428-5"})
        with self.assertRaises(ValidationError):
            socio.document_number = "760864284"

    def test_otros_documentos_no_se_tocan(self):
        sin_rut = self.env.ref("l10n_cl_fe.dt_Sigd")
        socio = self.env["res.partner"].create({
            "name": "Extranjero", "document_type_id": sin_rut.id, "document_number": " AB-123 ",
        })
        self.assertEqual(socio.document_number, " AB-123 ")
