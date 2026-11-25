"""Base para pruebas de emisión de DTE: compañía emisora, certificado autofirmado y CAF de prueba.

``DteCase`` arma en ``setUpClass`` una compañía chilena lista para emitir 33/34/56/61:

- ``cls.company``: RUT 76.086.428-5, giro, ACTECO, dirección, resolución del SII y plan ``cl``.
- ``cls.cert``: un ``.p12`` autofirmado generado con ``cryptography`` y cargado en ``sii.firma``
  (``action_process``), autorizado para el admin (el superusuario de las pruebas firma como él).
- ``cls.caf(codigo, desde, hasta, vencido=False)``: crea un ``dte.caf`` de prueba con una llave
  RSA propia (DA/FRMA/RSASK). La firma ``FRMA`` no es del SII: sirve para un XML válido contra el
  XSD, no para el SII.
- ``cls.xsd(nombre)``: el ``etree.XMLSchema`` de ``facturacion_electronica/xsd/<nombre>``.

Los documentos se timbran con la firma y el CAF de prueba, pero no se envían: el envío al SII
queda en la cola (``sii.cola_envio``) que crea ``action_post``.
"""
import base64
import datetime
import os
from functools import lru_cache

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from dateutil.relativedelta import relativedelta
from lxml import etree

from odoo import fields
from odoo.tests.common import TransactionCase

NS = {"sii": "http://www.sii.cl/SiiDte"}
RUT_EMISOR = "76.086.428-5"
RUT_FIRMANTE = "11111111-1"
CLAVE_P12 = b"clave-de-prueba"


def _b64(numero):
    largo = (numero.bit_length() + 7) // 8
    return base64.b64encode(numero.to_bytes(largo, "big")).decode()


@lru_cache(maxsize=None)
def _schema(ruta):
    return etree.XMLSchema(etree.parse(ruta))


def p12_autofirmado(rut=RUT_FIRMANTE, clave=CLAVE_P12, dias=365):
    """Un .p12 autofirmado (bytes) con el RUT del titular en ``subject.serialNumber``."""
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "CL"),
        x509.NameAttribute(NameOID.COMMON_NAME, "Firmante de Prueba"),
        x509.NameAttribute(NameOID.SERIAL_NUMBER, rut),
        x509.NameAttribute(NameOID.EMAIL_ADDRESS, "firmante@example.com"),
    ])
    ahora = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nombre)
        .issuer_name(nombre)
        .public_key(llave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - datetime.timedelta(days=1))
        .not_valid_after(ahora + datetime.timedelta(days=dias))
        .sign(llave, hashes.SHA256())
    )
    return pkcs12.serialize_key_and_certificates(
        b"firma-prueba", llave, cert, None, serialization.BestAvailableEncryption(clave),
    )


def caf_xml(rut, razon_social, codigo, desde, hasta, fecha):
    """XML de un CAF con la estructura del SII (DA, FRMA, RSASK, RSAPUBK) y una llave propia."""
    llave = rsa.generate_private_key(public_exponent=3, key_size=1024)
    publica = llave.public_key().public_numbers()
    da = (
        "<DA><RE>%(rut)s</RE><RS>%(rs)s</RS><TD>%(td)s</TD><RNG><D>%(d)s</D><H>%(h)s</H></RNG>"
        "<FA>%(fa)s</FA><RSAPK><M>%(m)s</M><E>%(e)s</E></RSAPK><IDK>100</IDK></DA>"
    ) % {
        "rut": rut, "rs": razon_social[:40], "td": codigo, "d": desde, "h": hasta,
        "fa": fecha.strftime("%Y-%m-%d"), "m": _b64(publica.n), "e": _b64(publica.e),
    }
    # FRMA la pone el SII con su llave; aquí basta una firma RSA bien formada.
    frma = base64.b64encode(llave.sign(da.encode("ISO-8859-1"), padding.PKCS1v15(), hashes.SHA1())).decode()
    privada = llave.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    pem_publica = llave.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return (
        '<?xml version="1.0"?>\n<AUTORIZACION>\n<CAF version="1.0">\n%s\n'
        '<FRMA algoritmo="SHA1withRSA">%s</FRMA>\n</CAF>\n<RSASK>%s</RSASK>\n'
        "<RSAPUBK>%s</RSAPUBK>\n</AUTORIZACION>\n"
    ) % (da, frma, privada, pem_publica)


class DteCase(TransactionCase):
    """Compañía emisora con certificado y diario de ventas con folios para 33, 34, 56 y 61."""

    CODIGOS = (33, 34, 56, 61)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["res.lang"]._activate_lang("es_CL")
        clp = cls.env.ref("base.CLP")
        clp.active = True
        santiago = cls.env.ref("l10n_cl_fe.CL13101")
        giro = cls.env["sii.activity.description"].create({"name": "Servicios informáticos"})
        acteco = cls.env.ref("l10n_cl_fe.eco_acti_620200")
        cls.company = cls.env["res.company"].create({
            "name": "Emisora DTE de Prueba SpA",
            "country_id": cls.env.ref("base.cl").id,
            "currency_id": clp.id,
            "document_number": RUT_EMISOR,
            "street": "Av. Libertador Bernardo O'Higgins 1234",
            "city": "Santiago",
            "city_id": santiago.id,
            "state_id": cls.env.ref("base.state_cl_13").id,
            "activity_description": giro.id,
            "company_activities_ids": [(6, 0, acteco.ids)],
            "dte_service_provider": "SIICERT",
            "dte_resolution_number": "0",
            "dte_resolution_date": "2014-08-22",
            "tax_calculation_rounding_method": "round_globally",
        })
        cls.env["account.chart.template"].try_loading("cl", cls.company, install_demo=False)
        cls.company.tax_calculation_rounding_method = "round_globally"
        admin = cls.env.ref("base.user_admin")
        admin.write({"company_ids": [(4, cls.company.id)]})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=cls.company.ids))

        cls.cert = cls.env["sii.firma"].create({
            "name": "firma_prueba.p12",
            "file_content": base64.b64encode(p12_autofirmado()).decode(),
            "password": CLAVE_P12.decode(),
            "user_ids": [(6, 0, admin.ids)],
            "company_ids": [(6, 0, cls.company.ids)],
        })
        cls.cert.action_process()

        cls.diario = cls.env["account.journal"].search([
            ("company_id", "=", cls.company.id), ("type", "=", "sale"),
        ], limit=1)
        cls.diario.write({
            "use_documents": True,
            "journal_activities_ids": [(6, 0, acteco.ids)],
        })
        cls.clases = {}
        cls.secuencias = {}
        cls.diario_clases = {}
        for codigo in cls.CODIGOS:
            clase = cls.env["sii.document_class"].search([("sii_code", "=", codigo)], limit=1)
            secuencia = cls.env["ir.sequence"].create({
                "name": "Folios - %s" % clase.name,
                "padding": 6,
                "implementation": "no_gap",
                "sii_document_class_id": clase.id,
                "company_id": cls.company.id,
                "is_dte": True,
            })
            cls.clases[codigo] = clase
            cls.secuencias[codigo] = secuencia
            cls.diario_clases[codigo] = cls.env["account.journal.sii_document_class"].create({
                "sii_document_class_id": clase.id,
                "sequence_id": secuencia.id,
                "journal_id": cls.diario.id,
                "sequence": codigo,
            })
        cls.diario.document_class_ids = [(6, 0, [c.id for c in cls.clases.values()])]

        plantilla = cls.env["account.chart.template"].with_company(cls.company)
        cls.iva = plantilla.ref("IVAV_19")
        cls.exento = plantilla.ref("exento_venta")
        cls.cliente = cls.env["res.partner"].create({
            "name": "Cliente Receptor Ltda.",
            "is_company": True,
            "document_number": "12.345.678-5",
            "activity_description": cls.env["sii.activity.description"].create({"name": "Comercio"}).id,
            "street": "Calle Falsa 123",
            "city": "Santiago",
            "city_id": santiago.id,
            "country_id": cls.env.ref("base.cl").id,
        })
        cls.producto = cls.env["product.product"].create({
            "name": "Servicio de prueba", "type": "service", "company_id": cls.company.id, "taxes_id": [(6, 0, cls.iva.ids)],
        })
        cls.producto_exento = cls.env["product.product"].create({
            "name": "Servicio exento de prueba", "type": "service", "company_id": cls.company.id, "taxes_id": [(6, 0, cls.exento.ids)],
        })

    @classmethod
    def caf(cls, codigo, desde, hasta, vencido=False):
        """CAF de prueba para ``codigo`` (folios ``desde``..``hasta``) en la secuencia del diario.

        Vencido: autorizado hace 7 meses (el SII da 6 meses a facturas y notas)."""
        hoy = fields.Date.context_today(cls.env["res.company"])
        fecha = hoy - relativedelta(months=7) if vencido else hoy - relativedelta(days=1)
        xml = caf_xml(
            cls.company.partner_id.rut(), cls.company.name, codigo, desde, hasta, fecha,
        )
        caf = cls.env["dte.caf"].create({
            "filename": "caf_%s_%s_%s_%s_%s.xml" % (cls.company.id, codigo, desde, hasta, int(vencido)),
            "caf_string": xml,
            "company_id": cls.company.id,
            "sequence_id": cls.secuencias[codigo].id,
        })
        if caf.state != "in_use":
            caf.load_caf()
        return caf

    @classmethod
    def xsd(cls, nombre):
        """Esquema ``etree.XMLSchema`` de ``facturacion_electronica/xsd/<nombre>`` (con o sin .xsd)."""
        import facturacion_electronica

        if not nombre.endswith(".xsd"):
            nombre += ".xsd"
        ruta = os.path.join(os.path.dirname(facturacion_electronica.__file__), "xsd", nombre)
        return _schema(ruta)

    # --- ayudas para las pruebas ---

    def factura(self, lineas, codigo=33, **extra):
        """Borrador de ``codigo`` con ``lineas`` = [(producto, cantidad, precio[, descuento])]."""
        valores = []
        for linea in lineas:
            producto, cantidad, precio = linea[:3]
            valores.append((0, 0, {
                "product_id": producto.id,
                "name": producto.name,
                "quantity": cantidad,
                "price_unit": precio,
                "discount": linea[3] if len(linea) > 3 else 0,
                "tax_ids": [(6, 0, producto.taxes_id.filtered(lambda t: t.company_id == self.company).ids)],
            }))
        return self.env["account.move"].create(dict({
            "move_type": "out_invoice",
            "partner_id": self.cliente.id,
            "company_id": self.company.id,
            "journal_id": self.diario.id,
            "invoice_date": fields.Date.context_today(self.env["res.company"]),
            "use_documents": True,
            "journal_document_class_id": self.diario_clases[codigo].id,
            "document_class_id": self.clases[codigo].id,
            "invoice_line_ids": valores,
        }, **extra))

    def xml_dte(self, documento):
        """El DTE timbrado del documento como árbol lxml."""
        self.assertTrue(documento.sii_xml_dte, "el documento no quedó timbrado")
        # El DTE va en ISO-8859-1 (SII) y puede no traer la declaración XML.
        parser = etree.XMLParser(encoding="ISO-8859-1")
        return etree.fromstring(documento.sii_xml_dte.encode("ISO-8859-1"), parser)

    def assertXsd(self, documento, nombre="DTE_v10"):
        xml = self.xml_dte(documento)
        esquema = self.xsd(nombre)
        if not esquema.validate(xml):
            self.fail("XML inválido contra %s: %s" % (nombre, esquema.error_log))
        return xml

    def totales(self, xml):
        """Encabezado/Totales del DTE como dict {etiqueta: int}."""
        nodo = xml.find(".//sii:Encabezado/sii:Totales", NS)
        return {etree.QName(n).localname: int(float(n.text)) for n in nodo}

    def envios(self):
        return (self.env["sii.xml.envio"].search_count([]), self.env["sii.cola_envio"].search_count([]))
