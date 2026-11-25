"""Recepción de DTE de proveedores: un EnvioDTE recibido queda como factura de proveedor en borrador.

Contrato (lo usa el agente de la etapa 1): con el método de recepción "Crear Factura Solamente"
(``account.dte_receipt_method = create_move``, o la acción ``create_move`` del asistente de carga),
cada DTE recibido queda como ``account.move`` ``in_invoice`` en borrador con ``document_class_id``,
``sii_document_number`` y el proveedor buscado (o creado) por su RUT. El acuse de recibo del envío
(``RespuestaDTE``) se genera al recibir; la aceptación comercial queda para después.

Fixture ``data/envio_dte_33.xml``: EnvioDTE de intercambio emitido con este mismo módulo por un
proveedor de prueba (RUT 96.806.980-2) a la compañía de ``DteCase`` (76.086.428-5): una factura 33,
folio 4521, dos líneas (una con 15% de descuento), neto 55.901, IVA 10.621, total 66.522. Se generó
con ``DteCase`` (RUT del emisor cambiado), ``action_post`` y ``sii.xml.envio.crear_intercambio``;
está firmado con un certificado autofirmado y un CAF de prueba (válido contra ``EnvioDTE_v10.xsd``,
no ante el SII). El módulo no verifica la firma de los DTE recibidos (ver PORT_NOTES).
``data/envio_malformado.xml``: un EnvioDTE truncado (XML mal formado).
"""
import base64
import os
import uuid
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import patch

from lxml import etree

from odoo import fields
from odoo.tools import file_open
from odoo.tests import Form
from odoo.tests.common import tagged

from facturacion_electronica import facturacion_electronica as fe

from .common_dte import DteCase

RUT_PROVEEDOR = "96.806.980-2"
VAT_PROVEEDOR = "CL968069802"
FOLIO = 4521
NETO, IVA, TOTAL = 55901, 10621, 66522


def _dato(nombre):
    with file_open("l10n_cl_fe/tests/data/%s" % nombre, "rb") as f:
        return f.read()


@tagged("post_install", "-at_install")
class TestRecepcionDte(DteCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_str("account.dte_receipt_method", "create_move")
        cls.clase_33 = cls.clases[33]
        cls.diario_compras = cls.env["account.journal"].search([
            ("company_id", "=", cls.company.id), ("type", "=", "purchase"),
        ], limit=1)
        # Como lo deja la configuración de diarios: el diario de compras recibe los tipos de documento
        # y tiene las actividades (las respuestas al proveedor firman con los datos del "emisor").
        cls.diario_compras.write({
            "document_class_ids": [(6, 0, [c.id for c in cls.clases.values()])],
            "journal_activities_ids": [(6, 0, cls.diario.journal_activities_ids.ids)],
        })
        cls.dte_33 = _dato("envio_dte_33.xml")
        cls.malformado = _dato("envio_malformado.xml")

    # --- ayudas ---

    def _subir(self, contenido, nombre="envio_dte_33.xml"):
        """Carga del XML con el asistente, como lo hace el usuario: solo subir y crear la factura."""
        wizard = self.env["sii.dte.upload_xml.wizard"].create({
            "xml_file": base64.b64encode(contenido).decode(),
            "filename": nombre,
            "type": "compras",
            "action": "create_move",
            "option": "upload",
        })
        return wizard.confirm(ret=True)

    def _correo(self, *adjuntos):
        """Un correo de intercambio que llega al buzón DTE: la pasarela de correo (fetchmail →
        ``message_process``) crea un ``mail.message.dte`` y publica el mensaje con los adjuntos."""
        correo = MIMEMultipart()
        correo["From"] = "Proveedora Sur <facturacion@proveedor-sur.example>"
        correo["To"] = "dte@emisora.example"
        correo["Subject"] = "Envío de DTE %s" % ", ".join(nombre for nombre, _contenido in adjuntos)
        correo["Message-Id"] = "<%s@proveedor-sur.example>" % uuid.uuid4()
        correo.attach(MIMEText("Adjunto envío de DTE", "plain", "utf-8"))
        for nombre, contenido in adjuntos:
            parte = MIMEApplication(contenido, "xml", Name=nombre)
            parte["Content-Disposition"] = 'attachment; filename="%s"' % nombre
            correo.attach(parte)
        dte = self.env["mail.thread"].message_process("mail.message.dte", correo.as_bytes())
        self.assertEqual(dte._name, "mail.message.dte")
        return dte

    def _variante(self, folio, cambios=()):
        """El EnvioDTE del fixture con otro folio y otros textos. La firma deja de calzar con el
        contenido; el módulo no la verifica."""
        contenido = self.dte_33.replace(b"<Folio>%d</Folio>" % FOLIO, b"<Folio>%d</Folio>" % folio)
        contenido = contenido.replace(b'ID="T33F%d"' % FOLIO, b'ID="T33F%d"' % folio)
        for antes, despues in cambios:
            self.assertIn(antes, contenido)
            contenido = contenido.replace(antes, despues)
        return contenido

    def _como_tipo(self, codigo, folio):
        """El DTE del fixture como otro tipo de documento (56 o 61) del mismo proveedor, mismos
        montos y una referencia a la factura 33 folio 4521."""
        contenido = self._variante(folio, [
            (b"<TipoDTE>33</TipoDTE>", b"<TipoDTE>%d</TipoDTE>" % codigo),
            (b'ID="T33F%d"' % folio, b'ID="T%dF%d"' % (codigo, folio)),
            (b"<TD>33</TD><F>%d</F>" % FOLIO, b"<TD>%d</TD><F>%d</F>" % (codigo, folio)),
            (b"  <TED version", b"  <Referencia><NroLinRef>1</NroLinRef><TpoDocRef>33</TpoDocRef>"
                                b"<FolioRef>%d</FolioRef><FchRef>2026-11-25</FchRef><CodRef>3</CodRef>"
                                b"<RazonRef>Ajuste de precio</RazonRef></Referencia>\n  <TED version" % FOLIO),
        ])
        return contenido

    def _envio_con_dos_dte(self):
        """EnvioDTE con el DTE del fixture y una copia con el folio 4522."""
        raiz = etree.fromstring(self.dte_33)
        set_dte = raiz.find("{http://www.sii.cl/SiiDte}SetDTE")
        copia = etree.fromstring(self._variante(4522)).find(
            "{http://www.sii.cl/SiiDte}SetDTE/{http://www.sii.cl/SiiDte}DTE")
        set_dte.append(copia)
        return etree.tostring(raiz, xml_declaration=True, encoding="ISO-8859-1")

    def _facturas(self, folio=FOLIO, codigo=33):
        return self.env["account.move"].search([
            ("company_id", "=", self.company.id),
            ("move_type", "in", ("in_invoice", "in_refund")),
            ("document_class_id", "=", self.clases[codigo].id),
            ("sii_document_number", "=", folio),
        ])

    def _proveedores(self):
        return self.env["res.partner"].search([("vat", "=", VAT_PROVEEDOR)])

    # --- pruebas ---

    def test_xml_crea_factura_borrador(self):
        self.assertFalse(self._proveedores(), "el proveedor no debe existir antes de recibir el DTE")
        self._subir(self.dte_33)
        factura = self._facturas()
        self.assertEqual(len(factura), 1, "el DTE recibido debe quedar como una factura de proveedor")
        self.assertEqual((factura.move_type, factura.state), ("in_invoice", "draft"))
        self.assertEqual(factura.document_class_id, self.clase_33)
        self.assertEqual(factura.sii_document_number, FOLIO)
        self.assertEqual(factura.journal_id.type, "purchase")
        proveedor = self._proveedores()
        self.assertEqual(len(proveedor), 1, "el proveedor se crea una sola vez, por su RUT")
        self.assertEqual(factura.partner_id, proveedor)
        self.assertEqual(proveedor.document_number, RUT_PROVEEDOR)
        self.assertEqual(proveedor.name, "Proveedora de Servicios del Sur SpA")
        self.assertEqual(str(factura.invoice_date), "2026-11-25")
        # Los montos del DTE del proveedor (documento legal), peso por peso.
        self.assertEqual(
            (factura.amount_untaxed, factura.amount_tax, factura.amount_total), (NETO, IVA, TOTAL))
        documento = self.env["mail.message.dte.document"].search([("move_id", "=", factura.id)])
        self.assertEqual(len(documento), 1, "el documento recibido queda enlazado a la factura")
        self.assertEqual((documento.number, documento.amount), (str(FOLIO), TOTAL))

    def test_proveedor_existente_por_rut(self):
        # Creado por ORM/JSON-RPC solo con el RUT (document_number): el vat se completa.
        existente = self.env["res.partner"].create({
            "name": "Proveedora Sur (ya registrada)", "is_company": True, "document_number": RUT_PROVEEDOR,
        })
        self.assertEqual(existente.vat, VAT_PROVEEDOR)
        self._subir(self.dte_33)
        self.assertEqual(self._facturas().partner_id, existente)
        self.assertEqual(self._proveedores(), existente, "no se crea otro contacto con el mismo RUT")
        self.assertEqual(self.env["res.partner"].search_count([("document_number", "=", RUT_PROVEEDOR)]), 1)

    def test_iva_del_proveedor_prevalece(self):
        # El proveedor calculó el IVA con 1 peso de diferencia: manda el DTE (documento legal).
        self._subir(self._variante(4522, [
            (b"<IVA>10621</IVA>", b"<IVA>10622</IVA>"),
            (b"<MntTotal>66522</MntTotal>", b"<MntTotal>66523</MntTotal>"),
            (b"<VlrPagar>66522</VlrPagar>", b"<VlrPagar>66523</VlrPagar>"),
        ]))
        factura = self._facturas(4522)
        self.assertEqual(factura.state, "draft")
        self.assertEqual((factura.amount_untaxed, factura.amount_tax, factura.amount_total), (NETO, 10622, 66523))

    def test_total_que_no_cuadra_no_crea_factura(self):
        # Un total que no se explica por el neto y el IVA no deja una factura con otro monto.
        self._subir(self._variante(4523, [(b"<MntTotal>66522</MntTotal>", b"<MntTotal>67522</MntTotal>")]))
        self.assertFalse(self._facturas(4523))
        documento = self.env["mail.message.dte.document"].search([("number", "=", "4523")])
        self.assertEqual(len(documento), 1, "el documento recibido queda registrado para revisarlo")
        self.assertFalse(documento.move_id)
        self.assertTrue(documento.message_ids.filtered(lambda m: "No se pudo cuadrar" in (m.body or "")))

    def test_envio_con_varios_dte(self):
        self._subir(self._envio_con_dos_dte(), nombre="envio_dos_dte.xml")
        for folio in (FOLIO, 4522):
            factura = self._facturas(folio)
            self.assertEqual((len(factura), factura.state), (1, "draft"), folio)
            documento = self.env["mail.message.dte.document"].search([("number", "=", str(folio))])
            self.assertEqual(documento.move_id, factura, "cada documento recibido enlaza su propia factura")

    def test_dte_duplicado(self):
        self._subir(self.dte_33)
        # El mismo DTE (RUT emisor, tipo y folio) otra vez, con el mismo nombre de archivo y con otro.
        self._subir(self.dte_33)
        self._subir(self.dte_33, nombre="reenvio_proveedor.xml")
        self._correo(("dte_reenviado.xml", self.dte_33))
        self.assertEqual(len(self._facturas()), 1)
        self.assertEqual(len(self._proveedores()), 1)
        self.assertEqual(self.env["mail.message.dte.document"].search_count([
            ("number", "=", str(FOLIO)), ("document_class_id", "=", self.clase_33.id)]), 1)

    def test_correo_de_intercambio_crea_factura_borrador(self):
        dte = self._correo(("envio_dte_33.xml", self.dte_33))
        factura = self._facturas()
        self.assertEqual(len(factura), 1)
        self.assertEqual((factura.move_type, factura.state, factura.partner_id.vat), ("in_invoice", "draft", VAT_PROVEEDOR))
        self.assertEqual(dte.document_ids.move_id, factura)

    def test_correo_con_dos_envios(self):
        dte = self._correo(("envio_dte_33.xml", self.dte_33), ("envio_4522.xml", self._variante(4522)))
        self.assertEqual((len(self._facturas()), len(self._facturas(4522))), (1, 1))
        envios = self.env["mail.message.dte"].search([("id", "=", dte.id)]) | self.env["mail.message.dte"].search(
            [("mail_id", "in", dte.message_ids.ids)])
        self.assertEqual(len(envios), 2, "cada EnvioDTE del correo queda en su propio registro")
        self.assertEqual(len(envios.xml_id), 2)

    def test_xml_malformado_no_rompe_lote(self):
        # Un lote: un correo con el XML malformado primero y el EnvioDTE bueno después.
        dte = self._correo(("envio_malformado.xml", self.malformado), ("envio_dte_33.xml", self.dte_33))
        self.assertEqual(len(self._facturas()), 1)
        self.assertEqual(self._facturas().state, "draft")
        errores = dte.message_ids.filtered(lambda m: "envio_malformado.xml" in (m.body or ""))
        self.assertEqual(len(errores), 1, "el error del XML malformado debe quedar en el chatter del envío")
        self.assertIn("No se pudo procesar", errores.body)

    def test_xml_malformado_en_procesamiento_masivo(self):
        # Lote de envíos ya recibidos (asistente "Procesar DTE" sobre varios mail.message.dte).
        Dte = self.env["mail.message.dte"]
        malo = Dte.create({"name": "envio_malformado.xml", "company_id": self.company.id})
        bueno = Dte.create({"name": "envio_dte_33.xml", "company_id": self.company.id})
        for dte, contenido in ((malo, self.malformado), (bueno, self.dte_33)):
            dte.xml_id = self.env["ir.attachment"].create({
                "name": dte.name, "raw": contenido, "res_model": dte._name, "res_id": dte.id,
            })
        (malo | bueno).process_message(pre=True, option="upload", dte_receipt_method="create_move")
        self.assertEqual(len(self._facturas()), 1)
        self.assertTrue(
            malo.message_ids.filtered(lambda m: "No se pudo procesar" in (m.body or "")),
            "el error del envío malformado debe quedar en su chatter",
        )

    def test_notas_de_debito_y_credito_del_proveedor(self):
        """Una nota de débito (56) del proveedor aumenta la deuda y el IVA crédito: es una factura
        de proveedor (``in_invoice``); solo la nota de crédito (61) es ``in_refund``.

        Factura 33 folio 4521, nota de débito 56 folio 4600 y nota de crédito 61 folio 4601, cada
        una neto 55.901, IVA 10.621, total 66.522 (cifras del fixture). Libro de compras =
        33 + 56 - 61 = neto 55.901, IVA 10.621, total 66.522. Contabilidad (saldo deudor):
        IVA crédito = 10.621 + 10.621 - 10.621 = 10.621; proveedor = -66.522. Con la 56 como
        rectificativa, la contabilidad daría IVA crédito -10.621 y proveedor 66.522 (el libro no
        cambia: suma la 56 por su tipo de documento)."""
        self._subir(self.dte_33)
        self._subir(self._como_tipo(56, 4600), nombre="nd_56.xml")
        self._subir(self._como_tipo(61, 4601), nombre="nc_61.xml")
        factura, debito, credito = self._facturas(), self._facturas(4600, 56), self._facturas(4601, 61)
        self.assertEqual(
            [(m.move_type, m.state, m.amount_total) for m in (factura, debito, credito)],
            [("in_invoice", "draft", 66522), ("in_invoice", "draft", 66522), ("in_refund", "draft", 66522)],
        )
        self.assertEqual(debito.partner_id, factura.partner_id)
        movimientos = factura | debito | credito
        movimientos.action_post()

        with Form(self.env["account.move.book"]) as f:
            f.tipo_operacion = "COMPRA"
            f.periodo_tributario = fields.Date.to_string(factura.invoice_date)[:7]
        libro = f.record
        self.assertEqual(libro.move_ids, movimientos)
        self.assertEqual(
            (libro.total_afecto, libro.total_iva, libro.total_exento, libro.total), (55901, 10621, 0, 66522))

        lineas = movimientos.line_ids
        iva = lineas.filtered(lambda l: l.tax_line_id.amount == 19)
        self.assertEqual(iva.account_id.mapped("code"), [iva.account_id[:1].code])
        self.assertEqual(sum(iva.mapped("balance")), 10621, "IVA crédito: 33 + 56 - 61")
        self.assertEqual(sum(debito.line_ids.filtered(lambda l: l.tax_line_id).mapped("balance")), 10621)
        self.assertEqual(sum(credito.line_ids.filtered(lambda l: l.tax_line_id).mapped("balance")), -10621)
        por_pagar = lineas.filtered(lambda l: l.account_id.account_type == "liability_payable")
        self.assertEqual(sum(por_pagar.mapped("balance")), -66522, "deuda con el proveedor: 33 + 56 - 61")
        self.assertEqual(sum(lineas.mapped("balance")), 0)

    def test_respuestas_xsd(self):
        import facturacion_electronica

        carpeta = os.path.join(os.path.dirname(facturacion_electronica.__file__), "xsd")
        faltan = [x for x in ("RespuestaEnvioDTE_v10.xsd", "EnvioRecibos_v10.xsd")
                  if not os.path.exists(os.path.join(carpeta, x))]
        if faltan:
            self.skipTest("Faltan los XSD %s en facturacion_electronica/xsd/" % ", ".join(faltan))
        dte = self._correo(("envio_dte_33.xml", self.dte_33))
        factura = self._facturas()
        self.assertEqual(len(factura), 1)
        # Acuse de recibo del envío (RespuestaDTE/RecepcionEnvio), generado al recibir.
        acuse = self.env["ir.attachment"].search([
            ("res_model", "=", "mail.message.dte"), ("res_id", "=", dte.id), ("name", "=like", "recepcion_envio_%"),
        ])
        self.assertEqual(len(acuse), 1, "al recibir se genera el acuse de recibo del envío")
        self._assert_xsd(acuse.raw, "RespuestaEnvioDTE_v10")
        # Recibo de mercaderías (EnvioRecibos) y respuesta comercial (RespuestaDTE/ResultadoDTE),
        # sin consultar al SII (el reclamo se simula).
        clave = "RUT96806980-2T33F%s" % FOLIO
        with patch.object(fe, "ingreso_reclamo_documento", return_value={clave: {"respuesta": {"codResp": 0}}}), \
                patch.object(fe, "consulta_reclamo_documento", return_value={clave: {}}):
            self.env["sii.dte.validar.wizard"].create({
                "move_ids": [(6, 0, factura.ids)], "tipo": "account.move",
                "action": "ambas", "claim": "ACD", "estado_dte": "0",
            }).confirm()
        adjuntos = self.env["ir.attachment"].search([("res_model", "=", "account.move"), ("res_id", "=", factura.id)])
        recibo = adjuntos.filtered(lambda a: a.name.startswith("recepcion_mercaderias_"))
        comercial = adjuntos.filtered(lambda a: a.name.startswith("validacion_comercial_"))
        self.assertEqual((len(recibo), len(comercial)), (1, 1), adjuntos.mapped("name"))
        self._assert_xsd(recibo.raw, "EnvioRecibos_v10")
        self._assert_xsd(comercial.raw, "RespuestaEnvioDTE_v10")

    def _rechazar(self, **valores):
        """Opción "Rechazar" del asistente de carga (sin consultar al SII: el reclamo se simula)."""
        wizard = self.env["sii.dte.upload_xml.wizard"].create(dict({
            "type": "compras", "action": "create_move", "option": "reject"}, **valores))
        respuesta = {"respuesta": {"codResp": 0}}
        with patch.object(fe, "ingreso_reclamo_documento", side_effect=lambda datos: {
                "RUT%(RUTEmisor)sT%(TipoDTE)sF%(Folio)s" % d: respuesta for d in datos["DTEClaim"]}) as reclamo:
            wizard.confirm(ret=True)
        return reclamo

    def _assert_rechazo(self, documento, folio):
        self.assertEqual(documento.state, "rejected")
        self.assertFalse(documento.move_id, "un DTE rechazado no se registra como factura")
        self.assertEqual(documento.claim_ids.mapped("claim"), ["RCD"])
        rechazo = self.env["ir.attachment"].search([
            ("res_model", "=", "mail.message.dte.document"), ("res_id", "=", documento.id),
            ("name", "=like", "rechazo_comercial_%"),
        ])
        self.assertEqual(len(rechazo), 1, "el rechazo genera la respuesta comercial (RespuestaDTE)")
        self._assert_xsd(rechazo.raw, "RespuestaEnvioDTE_v10")
        xml = etree.fromstring(getattr(rechazo.raw, "content", rechazo.raw))
        caratula = {etree.QName(n).localname: n.text for n in xml.find(".//{*}Caratula")}
        self.assertEqual((caratula["RutResponde"], caratula["RutRecibe"]), ("76086428-5", "96806980-2"))
        resultado = {etree.QName(n).localname: n.text for n in xml.find(".//{*}ResultadoDTE")}
        self.assertEqual(
            {k: resultado[k] for k in ("TipoDTE", "Folio", "RUTEmisor", "RUTRecep", "MntTotal", "EstadoDTE", "CodRchDsc")},
            {"TipoDTE": "33", "Folio": str(folio), "RUTEmisor": "96806980-2", "RUTRecep": "76086428-5",
             "MntTotal": str(TOTAL), "EstadoDTE": "2", "CodRchDsc": "-1"},
        )

    def test_rechazar_dte_recibido(self):
        """"Rechazar" en el asistente de carga: al subir el XML y sobre un documento ya recibido.
        Queda el documento recibido rechazado (sin factura), el reclamo RCD ingresado al SII y la
        respuesta comercial con EstadoDTE 2 para el proveedor, válida contra el XSD."""
        import facturacion_electronica

        if not os.path.exists(os.path.join(
                os.path.dirname(facturacion_electronica.__file__), "xsd", "RespuestaEnvioDTE_v10.xsd")):
            self.skipTest("Falta el XSD RespuestaEnvioDTE_v10.xsd en facturacion_electronica/xsd/")
        Documento = self.env["mail.message.dte.document"]
        # Al subir el XML con la opción "Rechazar".
        reclamo = self._rechazar(xml_file=base64.b64encode(self.dte_33).decode(), filename="envio_dte_33.xml")
        documento = Documento.search([("number", "=", str(FOLIO))])
        self.assertEqual(len(documento), 1)
        self._assert_rechazo(documento, FOLIO)
        self.assertEqual(reclamo.call_args.args[0]["DTEClaim"][0]["Claim"], "RCD")
        self.assertFalse(self._facturas())
        # Sobre un documento ya recibido (recepción manual: solo el documento, sin factura).
        self.env["sii.dte.upload_xml.wizard"].create({
            "xml_file": base64.b64encode(self._variante(4522)).decode(), "filename": "envio_4522.xml",
            "type": "compras", "action": "manual", "option": "upload",
        }).confirm(ret=True)
        recibido = Documento.search([("number", "=", "4522")])
        self.assertEqual((len(recibido), recibido.state), (1, "draft"))
        self._rechazar(document_id=recibido.id)
        self._assert_rechazo(recibido, 4522)
        # Botón "Rechazar" del documento recibido.
        self.env["sii.dte.upload_xml.wizard"].create({
            "xml_file": base64.b64encode(self._variante(4523)).decode(), "filename": "envio_4523.xml",
            "type": "compras", "action": "manual", "option": "upload",
        }).confirm(ret=True)
        boton = Documento.search([("number", "=", "4523")])
        with patch.object(fe, "ingreso_reclamo_documento", return_value={
                "RUT96806980-2T33F4523": {"respuesta": {"codResp": 0}}}):
            boton.reject_document()
        self._assert_rechazo(boton, 4523)

    def _assert_xsd(self, contenido, nombre):
        contenido = getattr(contenido, "content", contenido)  # raw de ir.attachment: BinaryValue
        xml = etree.fromstring(contenido)
        esquema = self.xsd(nombre)
        if not esquema.validate(xml):
            self.fail("XML inválido contra %s: %s\n%s" % (nombre, esquema.error_log, contenido.decode("ISO-8859-1")))
