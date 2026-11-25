import base64
import json
from datetime import date, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import Form
from odoo.tests.common import HttpCase, TransactionCase, new_test_user, tagged

from odoo.addons.l10n_cl_fe.wizard import trafquin


def _fecha_iso(texto):
    return date.fromisoformat(texto)

DTE = """<?xml version="1.0" encoding="ISO-8859-1"?>
<DTE xmlns="http://www.sii.cl/SiiDte" version="1.0"><Documento ID="F123T33">
<Encabezado><IdDoc><TipoDTE>33</TipoDTE><Folio>123</Folio><FchEmis>2026-10-01</FchEmis></IdDoc>
<Emisor><RUTEmisor>12345678-5</RUTEmisor><RznSoc>Proveedor Prueba SpA</RznSoc>
<GiroEmis>Servicios</GiroEmis><Acteco>620900</Acteco><DirOrigen>Calle 1</DirOrigen>
<CmnaOrigen>Santiago</CmnaOrigen><CiudadOrigen>Santiago</CiudadOrigen></Emisor>
<Receptor><RUTRecep>76086428-5</RUTRecep><RznSocRecep>Empresa</RznSocRecep>
<GiroRecep>Comercio</GiroRecep><DirRecep>Calle 2</DirRecep><CmnaRecep>Santiago</CmnaRecep></Receptor>
<Totales><MntNeto>1000</MntNeto><TasaIVA>19</TasaIVA><IVA>190</IVA><MntTotal>1190</MntTotal></Totales>
</Encabezado>
<Detalle><NroLinDet>1</NroLinDet><NmbItem>Servicio</NmbItem><QtyItem>1</QtyItem>
<PrcItem>1000</PrcItem><MontoItem>1000</MontoItem></Detalle>
</Documento></DTE>"""


class Respuesta:

    def __init__(self, status, datos):
        self.status = status
        self.data = json.dumps(datos).encode("utf-8")


class TrafquinBase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.partner_id.document_number = "76.086.428-5"
        cls.company.partner_id.vat = "CL760864285"
        cls.company.write({
            "dte_service_provider": "SII",
            "url_trafquin": "https://trafquin.test/",
            "token_trafquin": "secreto",
            "start_date": date(2020, 1, 1),
        })
        cls.Wizard = cls.env["sii.dte.trafquin.wizard"]
        cls.documento = {
            "tipo_dte": 33, "folio": 123, "rut_emisor": "12345678-5", "fecha_emision": "2026-10-01",
            "razon_social_emisor": "Proveedor Prueba SpA", "monto_total": 1190,
            "xml_b64": base64.b64encode(DTE.encode("ISO-8859-1")).decode("ascii"),
        }

    def _obtener(self, respuesta, origen="recibidos"):
        llamadas = []

        def request(metodo, url, body=None, headers=None):
            llamadas.append((url, json.loads(body)))
            return respuesta

        with patch.object(trafquin.pool, "request", side_effect=request), \
                patch.object(type(self.Wizard), "_firma_trafquin", return_value={"priv_key": "k"}):
            res = self.Wizard._obtener_xml(self.company, origen, date(2026, 10, 1), date(2026, 10, 7))
        return res, llamadas

    def _listado(self, *folios, completo=True, cursor=None):
        return Respuesta(200, {
            "documentos": [{
                "tipo_dte": 33, "folio": folio, "fecha": "2026-10-01", "rut_contraparte": "12345678-5",
                "razon_social": "Proveedor Prueba SpA", "monto": 1190, "estado": "DTE Recibido", "codigo": "c%s" % folio,
            } for folio in folios],
            "completo": completo, "cursor": cursor, "advertencias": [], "limitaciones": [],
        })

    def _con_trafquin(self, respuestas):
        llamadas = []

        def request(metodo, url, body=None, headers=None):
            ruta = url.split("/api/", 1)[1]
            llamadas.append((ruta, json.loads(body)))
            return respuestas[ruta].pop(0)

        return llamadas, patch.object(trafquin.pool, "request", side_effect=request)


@tagged("post_install", "-at_install")
class TestTrafquin(TrafquinBase):

    def test_registra_el_xml_una_sola_vez(self):
        respuesta = Respuesta(200, {"total": 1, "documentos": [self.documento], "advertencias": []})
        res, llamadas = self._obtener(respuesta)
        url, cuerpo = llamadas[0]
        self.assertEqual(url, "https://trafquin.test/api/xml/recibidos")
        self.assertEqual(cuerpo["rut"], "76086428-5")
        self.assertEqual(cuerpo["token"], "secreto")
        self.assertEqual((cuerpo["desde"], cuerpo["hasta"]), ("2026-10-01", "2026-10-07"))
        self.assertEqual((res["nuevos"], res["existentes"]), (1, 0))
        doc = self.env["mail.message.dte.document"].browse(res["document_ids"])
        self.assertEqual(doc.number, "123")
        self.assertEqual(doc.document_class_id.sii_code, 33)
        res, _llamadas = self._obtener(respuesta)
        self.assertEqual((res["nuevos"], res["existentes"]), (0, 1))
        self.assertEqual(res["document_ids"], doc.ids)

    def test_error_del_sii_se_muestra(self):
        respuesta = Respuesta(404, {
            "codigo": "RESPALDO_SIN_DATOS", "message": "Sin información",
            "mensaje_portal": "No hay datos"})
        with self.assertRaisesRegex(UserError, "RESPALDO_SIN_DATOS"):
            self._obtener(respuesta)

    def test_rango_largo_se_divide(self):
        respuesta = Respuesta(200, {"total": 0, "documentos": [], "advertencias": []})
        llamadas = []

        def request(metodo, url, body=None, headers=None):
            llamadas.append(json.loads(body))
            return respuesta

        with patch.object(trafquin.pool, "request", side_effect=request), \
                patch.object(type(self.Wizard), "_firma_trafquin", return_value={}):
            self.Wizard._obtener_xml(self.company, "recibidos", date(2026, 1, 1), date(2026, 3, 15))
        self.assertEqual([(c["desde"], c["hasta"]) for c in llamadas], [
            ("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-03-03"), ("2026-03-04", "2026-03-15")])

    def test_certificacion_no_consulta(self):
        self.company.dte_service_provider = "SIICERT"
        with self.assertRaises(UserError):
            self.Wizard._obtener_xml(self.company, "recibidos", date(2026, 10, 1), date(2026, 10, 7))

    def test_emitidos_se_publican_sin_reenviar_al_sii(self):
        respuesta = Respuesta(200, {"total": 1, "documentos": [self.documento], "advertencias": []})
        Upload = type(self.env["sii.dte.upload_xml.wizard"])
        contextos = []

        def crear(wiz):
            contextos.append(dict(wiz.env.context, pre_process=wiz.pre_process, tipo=wiz.type))
            return []

        with patch.object(Upload, "_inv_exist", return_value=self.env["account.move"]), \
                patch.object(Upload, "do_create_inv", autospec=True, side_effect=crear):
            res, llamadas = self._obtener(respuesta, "emitidos")
        self.assertEqual(llamadas[0][0], "https://trafquin.test/api/xml/emitidos")
        self.assertTrue(contextos[0].get("restore_mode"))
        self.assertFalse(contextos[0]["pre_process"])
        self.assertEqual(contextos[0]["tipo"], "ventas")
        self.assertEqual((res["nuevos"], res["move_ids"]), (0, []))
        self.assertTrue(any("No se pudo registrar" in a for a in res["advertencias"]))

    def test_listar_sin_fechas_por_folios_y_restaurar_seleccion(self):
        wizard = self.Wizard.create({
            "company_id": self.company.id, "origen": "recibidos", "folio_desde": 100, "folio_hasta": 200,
        })
        descarga = Respuesta(200, {"documentos": [self.documento], "completo": True, "cursor": None})
        llamadas, parche = self._con_trafquin({
            "xml/listar": [self._listado(123, 999)], "xml/descargar": [descarga]})
        with parche, patch.object(type(self.Wizard), "_firma_trafquin", return_value={}):
            listado = wizard.listar()
            linea = wizard.linea_ids
            self.assertEqual(listado["nuevos"], 1)
            self.assertEqual((linea.estado, linea.folio, linea.rut, str(linea.fecha), linea.total, linea.estado_sii),
                             ("nuevo", "123", "12345678-5", "2026-10-01", 1190, "DTE Recibido"))
            res = wizard.restaurar(linea.ids)
        ruta, cuerpo = llamadas[0]
        self.assertEqual(ruta, "xml/listar")
        self.assertNotIn("desde", cuerpo)
        self.assertEqual((cuerpo["origen"], cuerpo["folio_desde"], cuerpo["folio_hasta"]), ("compras", 100, 200))
        ruta, cuerpo = llamadas[1]
        self.assertEqual(ruta, "xml/descargar")
        self.assertEqual((cuerpo["origen"], cuerpo["folio"], cuerpo["tipo_doc"], cuerpo["rut_contraparte"]),
                         ("compras", 123, 33, "12345678-5"))
        self.assertNotIn("fecha", cuerpo)
        self.assertEqual((res["nuevos"], linea.estado, linea.document_id.number), (1, "restaurado", "123"))
        otro = self.Wizard.create({"company_id": self.company.id, "origen": "recibidos"})
        llamadas, parche = self._con_trafquin({"xml/listar": [self._listado(123)]})
        with parche, patch.object(type(self.Wizard), "_firma_trafquin", return_value={}):
            otro.listar()
        self.assertEqual((otro.linea_ids.estado, otro.linea_ids.document_id), ("existente", linea.document_id))

    def test_listar_sigue_con_el_cursor(self):
        wizard = self.Wizard.create({"company_id": self.company.id, "origen": "recibidos"})
        llamadas, parche = self._con_trafquin({
            "xml/listar": [self._listado(1, completo=False, cursor="C1"), self._listado(2)]})
        with parche, patch.object(type(self.Wizard), "_firma_trafquin", return_value={}):
            self.assertFalse(wizard.listar()["completo"])
            self.assertTrue(wizard.listar(continuar=True)["completo"])
        self.assertEqual(llamadas[1][1]["cursor"], "C1")
        self.assertEqual(sorted(wizard.linea_ids.mapped("folio")), ["1", "2"])

    def test_folio_exacto(self):
        wizard = self.Wizard.create({
            "company_id": self.company.id, "origen": "emitidos", "folio_desde": 7, "folio_hasta": 7})
        self.assertEqual(wizard._filtros_listar()["folio"], 7)
        self.assertNotIn("folio_desde", wizard._filtros_listar())

    def test_folio_desde_exige_folio_hasta(self):
        wizard = self.Wizard.create({"company_id": self.company.id, "origen": "recibidos", "folio_desde": 5})
        with self.assertRaises(UserError):
            wizard._validar_filtros()
        wizard.write({"folio_desde": False, "folio_hasta": 20})
        wizard._validar_filtros()
        self.assertEqual(wizard._filtros_listar()["folio_hasta"], 20)

    def test_restaurar_agrupa_folios_cercanos(self):
        wizard = self.Wizard.create({"company_id": self.company.id, "origen": "emitidos"})
        lineas = self.env["sii.dte.trafquin.linea"].create([
            {"wizard_id": wizard.id, "tipo_dte": 33, "folio": str(folio), "rut": "1-9"} for folio in (3, 1, 2, 50)
        ] + [{"wizard_id": wizard.id, "tipo_dte": 61, "folio": "2", "rut": "1-9"}])
        grupos = wizard._grupos_descarga(lineas)
        self.assertEqual(sorted((g[0].tipo_dte, g.mapped("folio")) for g in grupos),
                         [(33, ["1", "2", "3"]), (33, ["50"]), (61, ["2"])])
        llamadas, parche = self._con_trafquin({"xml/descargar": [
            Respuesta(200, {"documentos": [], "completo": True, "cursor": None}) for _g in grupos]})
        with parche, patch.object(type(self.Wizard), "_firma_trafquin", return_value={}):
            res = wizard.restaurar()
        cuerpos = sorted(((c.get("tipo_doc"), c.get("folio"), c.get("folio_desde"), c.get("folio_hasta"), c["origen"])
                          for _r, c in llamadas), key=str)
        self.assertEqual(cuerpos, sorted([(33, None, 1, 3, "ventas"), (33, 50, None, None, "ventas"), (61, 2, None, None, "ventas")], key=str))
        self.assertEqual(set(lineas.mapped("estado")), {"error"})
        self.assertEqual(res["nuevos"], 0)


class RevisionBase(TrafquinBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not cls.env["account.tax"].search([("sii_code", "=", 14), ("type_tax_use", "=", "sale"),
                                              ("company_id", "=", cls.company.id)], limit=1):
            cls.env["account.tax"].create({
                "name": "IVA 19% venta prueba", "amount": 19, "type_tax_use": "sale", "sii_code": 14,
                "company_id": cls.company.id,
            })

    def _revision(self, respuestas, periodo="202610"):
        revision = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": periodo})
        llamadas, parche = self._con_trafquin(respuestas)
        return revision, llamadas, parche

    def _firma(self, **kw):
        return patch.object(type(self.Wizard), "_firma_trafquin", return_value={"priv_key": "k"}, **kw)

    def _propuesta(self, codigos):
        return Respuesta(200, {"data": {
            "periodo": "202610", "tiene_propuesta": True, "estado": 1, "descripcion_estado": "Propuesta",
            "codigos": codigos, "codigos_administrativos": [], "declaraciones": [], "grupos": {},
        }})

    def _rcv(self, *documentos, completo=True):
        return Respuesta(200, {"data": {"completo": completo, "documentos": list(documentos),
                                        "errores": {}, "mensajes": {}, "advertencias": []}})

    def _doc_rcv(self, folio, estado="REGISTRO", rut="12345678-5", total=1190):
        return {"estado_sii": estado, "tipo_dte": 33, "folio": folio, "rut": rut,
                "razon_social": "Proveedor Prueba SpA", "fecha_emision": "2026-10-01",
                "monto_neto": 1000, "monto_iva": 190, "monto_total": total}

    def _sin_detalle(self, tipo):
        return Respuesta(200, {"data": {"tipo_dte": tipo, "sin_datos": True, "totales": None, "dias": []}})

    def _boletas_diarias(self, tipo, *dias):
        return Respuesta(200, {"data": {"tipo_dte": tipo, "sin_datos": False, "tiene_canal": tipo == 48, "dias": [
            dict({"canal": "internet" if tipo == 48 else None, "canal_sii": 1 if tipo == 48 else None,
                  "neto": round(total / 1.19), "iva": total - round(total / 1.19), "exento": 0},
                 fecha=fecha, cantidad=cantidad, total=total) for fecha, cantidad, total in dias]}})

    def _consumo_boletas(self):
        iva = self.env["account.tax"].search([("sii_code", "=", 14), ("type_tax_use", "=", "sale"),
                                              ("company_id", "=", self.company.id)], limit=1)
        boleta = self.env["sii.document_class"].search([("sii_code", "=", 39)], limit=1)
        cf = self.env["account.move.consumo_folios"].create({
            "name": "CF prueba", "company_id": self.company.id,
            "fecha_inicio": date(2026, 10, 1), "fecha_final": date(2026, 10, 1),
            "detalles": [(0, 0, {"tpo_doc": boleta.id, "tipo_operacion": "utilizados", "folio_inicio": 101,
                                 "folio_final": 102, "cantidad": 2})],
            "impuestos": [(0, 0, {"tpo_doc": boleta.id, "impuesto": iva.id, "cantidad": 2, "monto_neto": 3000,
                                  "monto_iva": 570, "monto_exento": 0, "monto_total": 3570})],
        })
        cf.sii_result = "Proceso"
        return cf


@tagged("post_install", "-at_install")
class TestRevisionSii(RevisionBase):

    def test_f29_guarda_codigos_crea_libros_y_conserva_ajustes(self):
        propuesta = self._propuesta([
            {"codigo": 538, "valor": 190000, "glosa": "TOTAL DÉBITOS", "signo": "=", "grupo": "debitos"},
            {"codigo": 9999, "valor": None, "glosa": "", "signo": "", "grupo": "otros"},
        ])
        revision, llamadas, parche = self._revision({"f29/propuesta": [propuesta, propuesta]})
        with parche, self._firma():
            revision.action_consultar_f29()
            debitos = revision.f29_linea_ids.filtered(lambda l: l.codigo == 538)
            self.assertEqual((debitos.valor_sii, debitos.valor_odoo, debitos.diferencia), (190000, 0, 190000))
            self.assertEqual((revision.compra_book_id.periodo_tributario, revision.compra_book_id.tipo_operacion),
                             ("2026-10", "COMPRA"))
            self.assertEqual(revision.venta_book_id.tipo_operacion, "VENTA")
            self.assertEqual(revision.f29_descuadres, 1)
            self.assertFalse(debitos.ajustable)
            debitos.write({"valor_propuesto": 180000, "motivo": "NC no considerada"})
            self.assertFalse(revision.f29_linea_ids._ajustadas())
            revision.action_consultar_f29()
        debitos = revision.f29_linea_ids.filtered(lambda l: l.codigo == 538)
        self.assertFalse(debitos.motivo)
        self.assertIn("firma_electronica", llamadas[0][1])
        self.assertNotIn("clave_tributaria", llamadas[0][1])
        self.assertFalse(revision.f29_error)
        self.assertEqual(len(self.env["account.move.book"].search([
            ("company_id", "=", self.company.id), ("periodo_tributario", "=", "2026-10")])), 2)

    def test_f29_calcula_la_propuesta_desde_los_libros(self):
        self._consumo_boletas()
        propuesta = self._propuesta([
            {"codigo": 110, "valor": 2, "glosa": "CANTIDAD BOLETAS", "signo": "", "grupo": "debitos"},
            {"codigo": 111, "valor": 500, "glosa": "DÉBITO BOLETAS", "signo": "+", "grupo": "debitos"},
            {"codigo": 504, "valor": 100, "glosa": "REMANENTE ANTERIOR", "signo": "+", "grupo": "creditos"},
            {"codigo": 115, "valor": 1, "glosa": "TASA PPM", "signo": "", "grupo": "otros"},
        ])
        revision, _llamadas, parche = self._revision({"f29/propuesta": [propuesta]})
        revision.compras_completo = True
        with parche, self._firma():
            revision.action_consultar_f29()
        valores = {l.codigo: (l.valor_sii, l.valor_odoo, l.calculado_odoo) for l in revision.f29_linea_ids}
        self.assertEqual(valores[110], (2, 2, True))
        self.assertEqual(valores[111], (500, 570, True))
        self.assertEqual(valores[504], (100, 100, True))
        self.assertEqual(valores[538], (500, 570, True))
        self.assertEqual(valores[537], (100, 100, True))
        self.assertEqual(valores[89], (400, 470, True))
        self.assertEqual(valores[563], (0, 3000, True))
        self.assertEqual(valores[62], (0, 30, True))
        totales = revision.f29_linea_ids.filtered(lambda l: l.codigo in (538, 537, 89))
        self.assertTrue(all(totales.mapped("sii_calculado")))
        self.assertEqual(totales.filtered(lambda l: l.codigo == 538).glosa, "Total débitos")

    def test_f29_totales_del_formulario_remanente_y_compras_sin_rcv(self):
        anterior = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": "202609"})
        anterior.write({"f29_linea_ids": [(0, 0, {"codigo": 77, "valor_sii": 0, "valor_odoo": 2000, "calculado_odoo": True})]})
        propuesta = self._propuesta([
            {"codigo": 502, "valor": 91879, "glosa": "", "signo": "", "grupo": "otros"},
            {"codigo": 520, "valor": 32600, "glosa": "", "signo": "", "grupo": "otros"},
            {"codigo": 504, "valor": 148502, "glosa": "", "signo": "", "grupo": "otros"},
            {"codigo": 563, "valor": 483574, "glosa": "", "signo": "", "grupo": "otros"},
            {"codigo": 115, "valor": 0.125, "glosa": "", "signo": "", "grupo": "otros"},
            {"codigo": 537, "valor": None, "glosa": "TOTAL CRÉDITOS", "signo": "=", "grupo": "totales"},
            {"codigo": 538, "valor": None, "glosa": "TOTAL DÉBITOS", "signo": "=", "grupo": "totales"},
            {"codigo": 77, "valor": None, "glosa": "", "signo": "", "grupo": "totales"},
            {"codigo": 62, "valor": None, "glosa": "", "signo": "", "grupo": "totales"},
        ])
        revision, _llamadas, parche = self._revision({"f29/propuesta": [propuesta, propuesta]})
        with parche, self._firma():
            revision.action_consultar_f29()
            lineas = {l.codigo: l for l in revision.f29_linea_ids}
            self.assertEqual((lineas[538].valor_sii, lineas[537].valor_sii, lineas[77].valor_sii, lineas[62].valor_sii),
                             (91879, 181102, 89223, 604))
            self.assertTrue(lineas[537].sii_calculado and not lineas[502].sii_calculado)
            self.assertEqual(lineas[520].glosa, "Crédito facturas recibidas del giro")
            self.assertFalse(lineas[520].calculado_odoo or lineas[537].calculado_odoo or lineas[504].calculado_odoo)
            self.assertFalse(lineas[77].calculado_odoo)
            revision.compras_completo = True
            revision.action_consultar_f29()
        lineas = {l.codigo: l for l in revision.f29_linea_ids}
        self.assertEqual((lineas[504].valor_odoo, lineas[537].valor_odoo, lineas[520].valor_odoo), (2000, 2000, 0))
        self.assertTrue(lineas[537].calculado_odoo and lineas[520].calculado_odoo)
        self.assertEqual(lineas[520].diferencia, 32600)

    def test_f29_codigos_de_notas_de_credito_y_debito(self):
        def movimiento(codigo, iva, neto):
            clase = type("Clase", (), {"sii_code": codigo})()
            return type("Mov", (), {"document_class_id": clase, "env": self.env, "_get_move_imps": lambda self: {},
                                    "totales_por_movimiento": lambda self: {"iva": iva, "neto": neto, "exento": 0, "otros_imps": 0}})()

        movimientos = [movimiento(33, 1900, 10000), movimiento(56, 190, 1000), movimiento(61, 380, 2000)]
        propuesta = self._propuesta([{"codigo": 502, "valor": 1900, "glosa": "", "signo": "+", "grupo": "debitos"}])
        revision, _llamadas, parche = self._revision({"f29/propuesta": [propuesta]})
        Libro = type(self.env["account.move.book"])
        with parche, self._firma(), patch.object(Libro, "_get_moves", lambda libro: movimientos
                                                if libro.tipo_operacion == "VENTA" else []):
            revision.action_consultar_f29()
        odoo = {l.codigo: l.valor_odoo for l in revision.f29_linea_ids if l.calculado_odoo}
        self.assertEqual((odoo[503], odoo[502], odoo[512], odoo[513], odoo[509], odoo[510]), (1, 1900, 1, 190, 1, 380))
        self.assertNotIn(501, odoo)
        self.assertEqual(odoo[538], 1900 + 190 - 380)
        self.assertEqual(odoo[563], 10000 + 1000 - 2000)

    def test_f29_usa_totales_calculados_de_trafquin(self):
        respuesta = self._propuesta([
            {"codigo": 502, "valor": 1000, "glosa": "", "signo": "+", "grupo": "debitos"},
            {"codigo": 538, "valor": None, "glosa": "TOTAL DÉBITOS", "signo": "=", "grupo": "debitos"},
            {"codigo": 563, "valor": 100000, "glosa": "", "signo": "", "grupo": "retenciones"},
            {"codigo": 115, "valor": 1, "glosa": "", "signo": "", "grupo": "retenciones"},
            {"codigo": 68, "valor": 200, "glosa": "", "signo": "", "grupo": "retenciones"},
        ])
        datos = json.loads(respuesta.data)
        datos["data"]["totales_calculados"] = {"538": 1234, "537": 500, "89": 734, "77": 0}
        respuesta.data = json.dumps(datos).encode("utf-8")
        revision, _llamadas, parche = self._revision({"f29/propuesta": [respuesta]})
        with parche, self._firma():
            revision.action_consultar_f29()
        lineas = {l.codigo: l for l in revision.f29_linea_ids}
        self.assertEqual((lineas[538].valor_sii, lineas[537].valor_sii, lineas[89].valor_sii), (1234, 500, 734))
        self.assertTrue(lineas[538].sii_calculado)
        self.assertEqual((lineas[62].valor_sii, lineas[62].calculado_odoo), (800, False))
        self.assertFalse(lineas[502].sii_calculado)

    def test_f29_con_clave_y_clave_rechazada(self):
        self.company.write({"sii_clave_tributaria": "secreta-123"})
        rechazo = Respuesta(422, {"codigo": "CLAVE_RECHAZADA", "message": "clave secreta-123 no aceptada"})
        revision, llamadas, parche = self._revision({"f29/propuesta": [self._propuesta([]), rechazo]})
        with parche, self._firma(side_effect=AssertionError("no debe usar el certificado")):
            revision.action_consultar_f29()
            self.assertEqual((llamadas[0][1]["clave_tributaria"], llamadas[0][1]["rut"]), ("secreta-123", "76086428-5"))
            self.assertNotIn("firma_electronica", llamadas[0][1])
            revision.action_consultar_f29()
            self.assertIn("rechazada", revision.f29_error)
            self.assertNotIn("secreta-123", revision.f29_error)
            self.assertTrue(self.company._clave_sii_rechazada())
            revision.action_consultar_f29()
            Revision = type(self.env["sii.revision.periodo"])
            with patch.object(Revision, "action_consultar_rcv"), patch.object(Revision, "action_consultar_boletas"):
                self.env["sii.revision.periodo"]._cron_revision_mensual()
        self.assertEqual(len(llamadas), 2)
        self.assertNotIn("secreta-123", revision.f29_error)
        self.company.sii_clave_tributaria = "nueva-456"
        self.assertFalse(self.company._clave_sii_rechazada())

    def test_clave_nueva_durante_el_bloqueo_de_trafquin(self):
        self.company.sii_clave_tributaria = "mala-1"
        rechazo = Respuesta(422, {"codigo": "CLAVE_RECHAZADA", "message": "Clave rechazada por el SII"})
        bloqueo = Respuesta(422, {"codigo": "CLAVE_RECHAZADA",
                                  "message": "Clave rechazada hace poco para este RUT: faltan 58 minutos"})
        revision, llamadas, parche = self._revision({"f29/propuesta": [rechazo, bloqueo, rechazo]})
        with parche, self._firma():
            revision.action_consultar_f29()
            self.assertTrue(self.company._clave_sii_rechazada())
            self.assertIn("revísela en Ajustes", revision.f29_error)
            self.assertIn("cámbiela", self.company.sii_clave_aviso)
            self.company.sii_clave_tributaria = "nueva-2"
            self.assertIn("no probará la nueva clave", self.company.sii_clave_aviso)
            revision.action_consultar_f29()
            self.assertEqual(len(llamadas), 2)
            self.assertFalse(self.company._clave_sii_rechazada())
            self.assertIn("faltan 58 minutos", revision.f29_error)
            self.company.sudo().sii_clave_rechazo_fecha = fields.Datetime.now() - timedelta(hours=2)
            self.assertFalse(self.company.sii_clave_aviso)
            revision.action_consultar_f29()
        self.assertEqual(len(llamadas), 3)
        self.assertTrue(self.company._clave_sii_rechazada())
        self.assertNotIn("nueva-2", revision.f29_error)

    def test_rcv_no_usa_la_clave(self):
        self.company.sii_clave_tributaria = "secreta-123"
        revision, llamadas, parche = self._revision({"rcv/documentos": [self._rcv(), self._rcv()]})
        with parche, self._firma():
            revision.action_consultar_rcv()
        self.assertEqual([c[1]["libro"] for c in llamadas], ["compras", "ventas"])
        self.assertTrue(all("firma_electronica" in c[1] and "clave_tributaria" not in c[1] for c in llamadas))

    def test_f29_no_autorizado_queda_en_la_pestana(self):
        error = Respuesta(403, {"codigo": "F29_NO_AUTORIZADO", "message": "Consulta RUT no esta autorizado"})
        revision, _llamadas, parche = self._revision({"f29/propuesta": [error]})
        with parche, self._firma():
            revision.action_consultar_f29()
        self.assertIn("F29_NO_AUTORIZADO", revision.f29_error)
        self.assertTrue(revision.f29_fecha)

    def test_llevar_ajustes_deshabilitado(self):
        revision = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": "202610"})
        revision.write({"f29_linea_ids": [
            (0, 0, {"codigo": 111, "valor_sii": 1000, "valor_propuesto": 900, "motivo": "Boleta anulada"}),
            (0, 0, {"codigo": 520, "valor_sii": 1000, "valor_propuesto": 900, "motivo": "Factura anulada"}),
        ]})
        enviar = self.env["sii.revision.f29.enviar"].create({"revision_id": revision.id})
        self.assertIn("111", enviar.resumen)
        self.assertNotIn("520", enviar.resumen)
        llamadas, parche = self._con_trafquin({"f29/guardar": [
            Respuesta(501, {"codigo": "F29_GUARDADO_DESHABILITADO", "message": "deshabilitado"})]})
        with parche, self._firma():
            with self.assertRaisesRegex(UserError, "aún no está disponible"):
                enviar.action_confirmar()
        cuerpo = llamadas[0][1]
        self.assertEqual((cuerpo["confirmar"], cuerpo["codigos"]), (True, [{"codigo": 111, "valor": 900, "motivo": "Boleta anulada"}]))

    def test_rcv_crea_recepcion_sii_la_completa_el_xml_y_no_duplica(self):
        Documento = self.env["mail.message.dte.document"]
        rcv = self._rcv(self._doc_rcv(123, "PENDIENTE"), self._doc_rcv(8, "RECLAMADO", rut="13579246-2", total=500))
        revision, llamadas, parche = self._revision({"rcv/documentos": [rcv, rcv, rcv]})
        with parche, self._firma():
            revision.action_consultar_compras()
            revision.action_consultar_compras()
            doc = Documento.search([("number", "=", "123"), ("document_class_id.sii_code", "=", 33)])
            self.assertEqual(len(doc), 1)
            self.assertEqual((doc.source, doc.amount, str(doc.date), doc.new_partner),
                             ("rcv", 1190, "2026-10-01", "12345678-5 Proveedor Prueba SpA"))
            self.assertFalse(Documento.search([("number", "=", "8")]))
            pendiente, reclamado = revision.compras_linea_ids.sorted(lambda l: l.folio != "123")
            self.assertEqual((pendiente.estado, reclamado.estado), ("sin_xml", "falta_odoo"))
            self.assertEqual(revision.compra_book_id.periodo_tributario, "2026-10")
            with self.assertRaises(UserError):
                doc._process_document()
            recibidos = Respuesta(200, {"total": 1, "documentos": [self.documento], "advertencias": []})
            res, _llamadas = self._obtener(recibidos)
            self.assertEqual((res["nuevos"], res["document_ids"]), (1, doc.ids))
            self.assertEqual(doc.source, "xml")
            self.assertTrue(doc.dte_id)
            self.assertEqual(doc.id_dte, "F123T33")
            revision.action_consultar_compras()
        self.assertEqual(len(Documento.search([("number", "=", "123"), ("document_class_id.sii_code", "=", 33)])), 1)
        self.assertEqual(revision.compras_linea_ids.filtered(lambda l: l.folio == "123").estado, "recibido")
        self.assertEqual(llamadas[0][1]["periodo"], "202610")

    def test_rcv_incompleto_y_error(self):
        rcv = Respuesta(200, {"data": {"completo": False, "documentos": [], "errores": {"PENDIENTE": {"message": "caído"}}}})
        caido = Respuesta(502, {"codigo": "RCV_ERROR", "message": "SII caído"})
        revision, _llamadas, parche = self._revision({"rcv/documentos": [rcv, caido]})
        with parche, self._firma():
            revision.action_consultar_compras()
            self.assertFalse(revision.compras_completo)
            self.assertIn("no queda conciliado", revision.compras_avisos)
            self.assertIn("PENDIENTE: caído", revision.compras_avisos)
            revision.action_consultar_ventas()
        self.assertIn("RCV_ERROR", revision.ventas_error)

    def test_boletas_contra_consumo_de_folios(self):
        self._consumo_boletas()
        espera = Respuesta(202, {"data": {"listo": False, "solicitud": "S1", "reintentar_en_s": 30}})
        lista = Respuesta(200, {"data": {
            "listo": True, "total": 3,
            "dias": [{"fecha": "2026-10-01", "tipo_dte": 39, "cantidad": 2, "folio_desde": 101, "folio_hasta": 102,
                      "monto_exento": 0, "monto_neto": 3000, "monto_iva": 570, "monto_total": 3570},
                     {"fecha": "2026-10-02", "tipo_dte": 39, "cantidad": 1, "monto_iva": 190, "monto_total": 1190}],
            "resumen_tipos": [{"tipo_dte": 48, "cantidad": 1, "sii": {"rsmnMntTotal": 5000, "rsmnMntIVA": 798}}],
            "advertencias": ["archivo de prueba"],
        }})
        revision, llamadas, parche = self._revision({"rcv/documentos": [espera, lista],
                                                     "rcv/boletas_diarias": [self._sin_detalle(48)]})
        with parche, self._firma():
            revision.action_consultar_boletas()
            self.assertIn("30 segundos", revision.boletas_error)
            revision.action_actualizar_boletas()
        self.assertEqual(llamadas[0][1]["libro"], "ventas_boletas")
        self.assertTrue(llamadas[1][1]["actualizar"])
        self.assertFalse(revision.boletas_error)
        dias = revision.boleta_linea_ids.filtered(lambda l: l.tipo_dte == 39).sorted("fecha")
        self.assertEqual([(str(d.fecha), d.cantidad_odoo, d.odoo_total, d.estado) for d in dias],
                         [("2026-10-01", 2, 3570, "coincide"), ("2026-10-02", 0, 0, "falta_odoo")])
        mes = revision.boleta_linea_ids.filtered(lambda l: l.tipo_dte == 48)
        self.assertEqual((mes.fecha, mes.total, mes.iva), (False, 5000, 798))
        self.assertEqual(revision.boletas_total_dif, 6190)
        self.assertEqual(revision.boletas_avisos, "archivo de prueba")

    def test_cron_diario_usa_el_mes_corrido_o_el_forzado(self):
        Revision = self.env["sii.revision.periodo"]
        with patch.object(type(Revision), "action_consultar_rcv") as consultar:
            Revision._cron_rcv_diario()
        mes = date.today().strftime("%Y%m")
        self.assertTrue(Revision.search([("company_id", "=", self.company.id), ("periodo", "=", mes)]))
        self.assertTrue(consultar.called)
        llamadas, parche = self._con_trafquin({"rcv/documentos": [self._rcv(), self._rcv(), self._rcv(), self._rcv()]})
        with parche, self._firma():
            Revision._cron_rcv_diario("202609")
            Revision._cron_rcv_diario("202609")
        self.assertEqual({c[1]["periodo"] for c in llamadas}, {"202609"})
        self.assertEqual(len(Revision.search([("company_id", "=", self.company.id), ("periodo", "=", "202609")])), 1)

    def _nc_boletas_voucher(self, folio, neto, iva, company=None):
        clase = self.env["sii.document_class"].search([("sii_code", "=", 61)], limit=1)
        secuencia = self.env["ir.sequence"].create({"name": "NC voucher prueba", "sii_document_class_id": clase.id})
        nc = self.env["account.move.boleta_voucher"].create({
            "name": "NC voucher %s" % folio, "company_id": (company or self.company).id,
            "currency_id": self.company.currency_id.id, "sequence_id": secuencia.id, "folio_anular": 1,
            "periodo": "2026-10",
        })
        self.env.flush_all()
        nc.write({"sii_document_number": folio, "total_neto": neto, "total_iva": iva, "total_exento": 0,
                  "total": neto + iva})
        return nc

    def _linea_48(self, libro):
        return libro.boletas.filtered(lambda b: b.tipo_boleta.sii_code == 48)

    def test_libro_ventas_linea_48_del_sii_y_nc_de_boletas_con_voucher(self):
        revision = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": "202610"})
        self._consumo_boletas()
        libro = revision._libro("VENTA")
        linea = self._linea_48(libro)
        self.assertEqual(len(linea), 1)
        self.assertEqual((linea.cantidad_boletas, linea.neto, linea.monto_impuesto, linea.monto_total), (0, 0, 0, 0))
        self.assertEqual(len(libro.boletas.filtered(lambda b: b.tipo_boleta.sii_code == 39)), 1)
        nc = self._nc_boletas_voucher(5001, 4202, 798)
        self._nc_boletas_voucher(5002, 500, 95, company=self.env["res.company"].create({"name": "Otra"}))
        libro = revision._libro("VENTA")
        self.assertEqual(libro._nc_boletas_voucher(), nc)
        self.assertEqual(self._linea_48(libro).monto_total, 0)
        enviados = {}

        def armar(datos):
            enviados.update(datos)
            return {"sii_xml_request": "<EnvioLibro/>"}

        from odoo.addons.l10n_cl_fe.models import libro as modulo_libro
        Voucher = type(self.env["account.move.boleta_voucher"])
        Boletas = type(self.env["account.move.book.boletas"])
        with patch.object(type(libro), "_get_datos_empresa", return_value={}), \
                patch.object(Voucher, "_dte", lambda v: {"Folio": v.sii_document_number}), \
                patch.object(Boletas, "_dte", lambda b: {"TpoDoc": b.tipo_boleta.sii_code}, create=True), \
                patch.object(modulo_libro.fe, "libro", side_effect=armar):
            libro._validar()
        self.assertIn({"TipoDTE": 61, "documentos": [{"Folio": 5001}]}, enviados["Libro"]["Documento"])

    def test_boletas_48_y_nc_voucher_en_el_f29(self):
        self._nc_boletas_voucher(5001, 4202, 798)
        lista = Respuesta(200, {"data": {
            "listo": True, "dias": [],
            "resumen_tipos": [{"tipo_dte": 48, "cantidad": 1, "sii": {"rsmnMntTotal": 5000, "rsmnMntIVA": 798}}],
        }})
        diarias = Respuesta(200, {"data": {"tipo_dte": 48, "sin_datos": False, "dias": [
            {"fecha": "2026-10-03", "canal": "internet", "cantidad": 1, "neto": 4202, "iva": 798, "total": 5000}]}})
        propuesta = self._propuesta([{"codigo": 759, "valor": 798, "glosa": "", "signo": "", "grupo": "debitos"},
                                     {"codigo": 510, "valor": 798, "glosa": "", "signo": "", "grupo": "debitos"}])
        revision, _llamadas, parche = self._revision({"rcv/documentos": [lista], "f29/propuesta": [propuesta],
                                                      "rcv/boletas_diarias": [diarias]})
        with parche, self._firma():
            revision.action_consultar_boletas()
            revision.action_consultar_f29()
        mes = revision.boleta_linea_ids.filtered(lambda l: l.tipo_dte == 48)
        self.assertEqual((mes.cantidad_sii, mes.cantidad_odoo, mes.odoo_iva, mes.odoo_total, mes.estado),
                         (1, 1, 798, 5000, "coincide"))
        valores = {l.codigo: (l.valor_sii, l.valor_odoo) for l in revision.f29_linea_ids}
        self.assertEqual(valores[759], (798, 798))
        self.assertEqual(valores[758], (0, 1))
        self.assertEqual(valores[510], (798, 798))

    def test_cron_crea_el_periodo_anterior(self):
        with patch.object(type(self.env["sii.revision.periodo"]), "action_consultar_todo") as consultar:
            self.env["sii.revision.periodo"]._cron_revision_mensual()
        revision = self.env["sii.revision.periodo"].search([("company_id", "=", self.company.id)])
        self.assertEqual(len(revision), 1)
        self.assertTrue(consultar.called)


class Mov:

    def __init__(self, env, codigo, neto, impuestos, exento=0, no_rec_code=False, iva_uso_comun=False,
                 folio=False, rut=""):
        self.env = env
        self.sii_document_number = folio
        self.commercial_partner_id = type("Socio", (), {"document_number": rut})()
        self.document_class_id = env["sii.document_class"].search([("sii_code", "=", codigo)], limit=1)
        self.no_rec_code = no_rec_code
        self.iva_uso_comun = iva_uso_comun
        self._impuestos = impuestos
        self._neto = neto
        self._exento = exento

    def _get_move_imps(self):
        return {tax.id: {"tax_id": tax.id, "credit": 0, "debit": monto, "code": tax.sii_code}
                for tax, monto in self._impuestos}

    def totales_por_movimiento(self):
        iva = sum(m for t, m in self._impuestos if t.sii_code == 14)
        return {"neto": self._neto, "exento": self._exento, "iva": iva,
                "otros_imps": sum(m for t, m in self._impuestos if t.sii_code not in (0, 14))}


@tagged("post_install", "-at_install")
class TestDeclaracionF29Impuestos(RevisionBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Tax = cls.env["account.tax"]

        def impuesto(nombre, codigo, monto, uso, **extra):
            return Tax.create(dict({"name": nombre, "amount": monto, "type_tax_use": uso, "sii_code": codigo,
                                    "company_id": cls.company.id}, **extra))

        cls.iva_compra = impuesto("IVA compra", 14, 19, "purchase")
        cls.iva_no_rec = impuesto("IVA no recuperable", 14, 19, "purchase", no_rec=True)
        cls.iva_activo = impuesto("IVA activo fijo", 14, 19, "purchase", activo_fijo=True)
        cls.ila_sin_credito = impuesto("ILA compra sin crédito", 24, 31.5, "purchase")
        cls.ila_con_credito = impuesto("ILA vinos compra", 25, 20.5, "purchase")
        cuenta = cls.env["account.account"].create({"name": "ILA crédito", "code": "119925", "account_type": "asset_current"})
        cls.ila_con_credito.invoice_repartition_line_ids.filtered(
            lambda r: r.repartition_type == "tax").account_id = cuenta
        cls.ila_sin_credito.invoice_repartition_line_ids.filtered(
            lambda r: r.repartition_type == "tax").account_id = False
        cls.iva_venta = cls.env["account.tax"].search([("sii_code", "=", 14), ("type_tax_use", "=", "sale"),
                                                       ("company_id", "=", cls.company.id)], limit=1)
        cls.ila_venta_vinos = impuesto("ILA vinos venta", 25, 20.5, "sale")

    def _f29(self, compras=(), ventas=(), fact_prop=0, codigos=(), periodo="202610"):
        propuesta = self._propuesta([{"codigo": c, "valor": v, "glosa": "", "signo": "", "grupo": "otros"}
                                     for c, v in codigos])
        revision, _llamadas, parche = self._revision({"f29/propuesta": [propuesta]}, periodo=periodo)
        revision.compras_completo = True
        revision._libro("COMPRA").fact_prop = fact_prop
        revision._libro("VENTA")
        Libro = type(self.env["account.move.book"])
        movimientos = {"COMPRA": [Mov(self.env, *c[0], **c[1]) for c in compras],
                       "VENTA": [Mov(self.env, *v[0], **v[1]) for v in ventas]}
        with parche, self._firma(), patch.object(Libro, "_get_moves", lambda libro: movimientos[libro.tipo_operacion]):
            revision.action_consultar_f29()
        self._verificar_aportes(revision)
        odoo = {l.codigo: l.valor_odoo for l in revision.f29_linea_ids if l.calculado_odoo}
        return revision, {**{c: 0 for c in (519, 520) if 520 in odoo or 537 in odoo}, **odoo}

    def _cuenta_retencion(self, empresa):
        Cuenta = self.env["account.account"].with_company(empresa)
        return Cuenta.search([("code", "=", "210704")], limit=1) or Cuenta.create({
            "code": "210704", "name": "Retención segunda categoría", "account_type": "liability_current"})

    def _grupo_retencion(self, empresa):
        return self.env["account.tax"].search([
            ("company_id", "=", empresa.id), ("type_tax_use", "=", "purchase")], limit=1).tax_group_id

    def test_retencion_de_boletas_de_honorarios_en_el_151(self):
        empresa = self.env.company
        retencion = self.env["account.tax"].create({
            "name": "Retención honorarios prueba", "amount": -14.5, "amount_type": "percent",
            "type_tax_use": "purchase", "company_id": empresa.id,
            "country_id": (getattr(self._grupo_retencion(empresa), "country_id", False) or self.env.ref("base.cl")).id,
            "tax_group_id": self._grupo_retencion(empresa).id,
            "invoice_repartition_line_ids": [(0, 0, {"repartition_type": "base"}),
                                             (0, 0, {"repartition_type": "tax", "account_id": self._cuenta_retencion(empresa).id})],
            "refund_repartition_line_ids": [(0, 0, {"repartition_type": "base"}),
                                            (0, 0, {"repartition_type": "tax", "account_id": self._cuenta_retencion(empresa).id})],
        })
        gasto = self.env["account.account"].with_company(empresa).search([("account_type", "=", "expense")], limit=1)
        diario = self.env["account.journal"].search([("type", "=", "purchase"), ("company_id", "=", empresa.id)], limit=1)
        bhe = self.env["sii.document_class"].search([("sii_code", "=", 71)], limit=1)
        prestador = self.env["res.partner"].create({"name": "Prestador BHE"})
        for fecha, monto in ((date(2026, 10, 15), 210526), (date(2026, 11, 2), 100000)):
            move = self.env["account.move"].create({
                "move_type": "in_invoice", "partner_id": prestador.id, "journal_id": diario.id,
                "invoice_date": fecha, "date": fecha, "document_class_id": bhe.id,
                "sii_document_number": monto, "use_documents": False,
                "invoice_line_ids": [(0, 0, {"name": "Honorarios", "quantity": 1, "price_unit": monto,
                                             "account_id": gasto.id, "tax_ids": [(6, 0, retencion.ids)]})],
            })
            move.action_post()
        revision, odoo = self._f29(codigos=[(151, 30526)])
        linea = revision.f29_linea_ids.filtered(lambda l: l.codigo == 151)
        self.assertEqual((odoo[151], linea.diferencia), (30526, 0))
        self.assertEqual(linea.aporte_odoo_ids.filtered("move_id").mapped("folio"), ["210526"])

    def _verificar_aportes(self, revision):

        for linea in revision.f29_formulario_ids.filtered("calculado_odoo"):
            self.assertAlmostEqual(sum(linea.aporte_odoo_ids.mapped("monto")), linea.valor_odoo, places=2,
                                   msg="aportes Odoo del código %s" % linea.codigo)
            self.assertAlmostEqual(sum(linea.aporte_diferencia_ids.mapped("monto")), linea.valor_sii - linea.valor_odoo,
                                   places=2, msg="diferencia del código %s" % linea.codigo)

    def test_detalle_de_la_linea_explica_la_diferencia_por_documento(self):
        revision = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": "202610"})
        revision.write({"compras_completo": True, "compras_linea_ids": [(0, 0, {
            "libro": "compras", "estado_sii": "REGISTRO", "tipo_dte": 33, "folio": folio, "rut": "1-9", "iva": iva,
            "total": iva * 6, "estado": estado}) for folio, iva, estado in (("10", 1900, "coincide"), ("12", 14849, "sin_xml"))]})
        propuesta = self._propuesta([{"codigo": 520, "valor": 16749, "glosa": "", "signo": "+", "grupo": "creditos"}])
        llamadas, parche = self._con_trafquin({"f29/propuesta": [propuesta]})
        Libro = type(self.env["account.move.book"])
        compras = [Mov(self.env, 33, 10000, [(self.iva_compra, 1900)], folio=10, rut="1-9"),
                   Mov(self.env, 33, 2632, [(self.iva_compra, 500)], folio=11, rut="1-9")]
        with parche, self._firma(), patch.object(Libro, "_get_moves",
                                                 lambda libro: compras if libro.tipo_operacion == "COMPRA" else []):
            revision.action_consultar_f29()
        self._verificar_aportes(revision)
        linea = revision.f29_linea_ids.filtered(lambda l: l.codigo == 520)
        self.assertEqual((linea.valor_sii, linea.valor_odoo), (16749, 2400))
        self.assertEqual(sorted((a.folio, a.monto) for a in linea.aporte_odoo_ids), [("10", 1900), ("11", 500)])
        self.assertEqual(sorted((a.folio, a.monto) for a in linea.aporte_sii_ids), [("10", 1900), ("12", 14849)])
        diferencias = {a.folio: (a.monto, a.descripcion.split(":")[0]) for a in linea.aporte_diferencia_ids}
        self.assertEqual(diferencias, {"12": (14849, "Está en el SII y no en Odoo"),
                                       "11": (-500, "Está en Odoo y no en el SII")})
        self.assertEqual(linea.aporte_sii_ids.filtered(lambda a: a.folio == "12").estado_cruce, "sin_xml")
        total = revision.f29_linea_ids.filtered(lambda l: l.codigo == 537)
        self.assertIn(linea, total.aporte_odoo_ids.componente_id)
        self.assertIn("520", total.formula)
        accion = linea.action_detalle()
        self.assertEqual((accion["res_model"], accion["res_id"]), ("sii.revision.f29.linea", linea.id))

    def test_detalle_de_codigo_no_calculado(self):
        propuesta = self._propuesta([{"codigo": 520, "valor": 100, "glosa": "", "signo": "+", "grupo": "creditos"},
                                     {"codigo": 151, "valor": 50, "glosa": "", "signo": "+", "grupo": "retenciones"}])
        revision, _llamadas, parche = self._revision({"f29/propuesta": [propuesta]}, periodo="202612")
        with parche, self._firma():
            revision.action_consultar_f29()
        lineas = {l.codigo: l for l in revision.f29_linea_ids}
        self.assertIn("RCV de compras", lineas[520].motivo_no_calculado)
        self.assertTrue(lineas[151].calculado_odoo)
        self.assertFalse(lineas[151].aporte_odoo_ids)

    def test_ejemplo_i1_iva_no_recuperable(self):
        _revision, odoo = self._f29(compras=[((33, 15000, [(self.iva_no_rec, 2850)]), {})])
        self.assertEqual((odoo[564], odoo[521], odoo[519], odoo[520]), (1, 15000, 0, 0))
        _revision, odoo = self._f29(compras=[((33, 15000, [(self.iva_compra, 2850)]), {"no_rec_code": "1"})],
                                    periodo="202611")
        self.assertEqual((odoo[564], odoo[520]), (1, 0))

    def test_ejemplo_i2_iva_uso_comun(self):
        _revision, odoo = self._f29(compras=[((33, 120400, [(self.iva_compra, 22876)]), {"iva_uso_comun": True})],
                                    fact_prop=0.6)
        self.assertEqual((odoo[519], odoo[520]), (1, 13726))
        revision, odoo = self._f29(compras=[((33, 120400, [(self.iva_compra, 22876)]), {"iva_uso_comun": True})],
                                   periodo="202611")
        self.assertNotIn(520, odoo)
        self.assertNotIn(537, odoo)

    def test_ejemplo_i3_adicional_sin_derecho_a_credito(self):
        _revision, odoo = self._f29(compras=[((33, 40000, [(self.iva_compra, 7600), (self.ila_sin_credito, 10500)]), {})])
        self.assertEqual((odoo[519], odoo[520]), (1, 7600))
        self.assertNotIn(575, odoo)

    def test_ejemplo_i4_adicional_con_derecho_a_credito(self):
        _revision, odoo = self._f29(compras=[((33, 110000, [(self.iva_compra, 20900), (self.ila_con_credito, 16500)]), {})])
        self.assertEqual((odoo[519], odoo[520], odoo[574]), (1, 20900, 16500))

    def test_ejemplo_i5_activo_fijo(self):
        _revision, odoo = self._f29(compras=[((33, 291000, [(self.iva_activo, 55290)]), {})])
        self.assertEqual((odoo[524], odoo[525], odoo[519], odoo[520]), (1, 55290, 0, 0))
        self.assertEqual(odoo[537], 55290)

    def test_ventas_con_ila_en_sus_codigos(self):
        _revision, odoo = self._f29(ventas=[
            ((33, 10000, [(self.iva_venta, 1900), (self.ila_venta_vinos, 2050)]), {}),
            ((56, 1000, [(self.iva_venta, 190), (self.ila_venta_vinos, 205)]), {}),
            ((61, 2000, [(self.iva_venta, 380), (self.ila_venta_vinos, 410)]), {}),
        ])
        self.assertEqual((odoo[502], odoo[513], odoo[510]), (1900, 190, 380))
        self.assertEqual((odoo[32], odoo[545], odoo[546]), (2050, 205, 410))
        self.assertEqual(odoo[538], 1900 + 190 - 380)
        self.assertEqual(odoo[563], 9000)

    def test_contrato_nuevo_administrativos_tasa_y_orden(self):
        respuesta = self._propuesta([
            {"codigo": 502, "valor": 91879, "glosa": "", "signo": "+", "grupo": "debitos", "orden": 9, "columna": "Débito"},
            {"codigo": 503, "valor": 2, "glosa": "", "signo": "", "grupo": "debitos", "orden": 9, "columna": "Cantidad de documentos"},
            {"codigo": 115, "valor": 0.125, "glosa": "", "signo": "", "grupo": "retenciones", "orden": 60, "columna": "Tasa"},
            {"codigo": 15, "valor": 202610, "glosa": "", "signo": "", "grupo": "identificacion", "orden": 1, "columna": "Período"},
        ])
        datos = json.loads(respuesta.data)
        datos["data"]["codigos_administrativos"] = [{"codigo": 9064, "valor": 7, "glosa": "", "signo": "", "grupo": "fuera_formulario"}]
        respuesta.data = json.dumps(datos).encode("utf-8")
        revision, _llamadas, parche = self._revision({"f29/propuesta": [respuesta]})
        with parche, self._firma():
            revision.action_consultar_f29()
        codigos = revision.f29_formulario_ids.mapped("codigo")
        self.assertEqual(codigos[:4], [15, 502, 503, 115])
        self.assertNotIn(9064, codigos)
        self.assertEqual(revision.f29_admin_ids.mapped("codigo"), [9064])
        lineas = {l.codigo: l for l in revision.f29_linea_ids}
        self.assertEqual((lineas[115].valor_sii_texto, lineas[502].valor_sii_texto, lineas[502].columna),
                         ("0,125", "91.879", "Débito"))

    def test_iva_de_compras_sin_factura_explica_el_520(self):
        revision, _odoo = self._f29(compras=[((33, 1000, [(self.iva_compra, 190)]), {})],
                                    codigos=[(520, 15039), (511, 15039)])
        revision.write({"compras_linea_ids": [(0, 0, {"libro": "compras", "estado_sii": "REGISTRO", "tipo_dte": 33,
                                                       "folio": "9632", "iva": 14849, "total": 93000, "estado": "sin_xml"})]})
        linea = revision.f29_linea_ids.filtered(lambda l: l.codigo == 520)
        self.assertEqual((revision.compras_pendientes_iva, linea.diferencia), (14849, 14849))
        self.assertTrue(linea.explicacion)
        self.assertTrue(revision.f29_linea_ids.filtered(lambda l: l.codigo == 511).explicacion)
        self.assertNotIn(linea, revision.f29_linea_ids.filtered(lambda l: l.calculado_odoo and not l.explicacion
                                                                 and abs(l.diferencia) > 1))

    def test_observaciones_probables(self):
        anterior = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": "202609"})
        anterior.write({"f29_linea_ids": [(0, 0, {"codigo": 77, "valor_sii": 500})]})
        utm = self.env["res.currency"].with_context(active_test=False).search([("name", "=", "UTM")], limit=1)
        self.env["res.currency.rate"].create([{"currency_id": utm.id, "company_id": self.company.id, "name": fecha,
                                               "rate": 1 / 70000} for fecha in (date(2026, 9, 1), date(2026, 10, 1))])
        revision = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": "202610"})
        revision.write({
            "compras_completo": True,
            "ventas_fecha": fields.Datetime.now(),
            "boletas_fecha": fields.Datetime.now(),
            "f29_linea_ids": [(0, 0, {"codigo": c, "valor_sii": v}) for c, v in
                              ((502, 1000), (111, 570), (759, 0), (520, 200), (504, 500))],
            "ventas_linea_ids": [(0, 0, {"libro": "ventas", "tipo_dte": 33, "folio": "1", "iva": 1500, "estado": "falta_odoo"})],
            "compras_linea_ids": [(0, 0, {"libro": "compras", "tipo_dte": 33, "folio": "7", "rut": "12345678-5",
                                          "estado_sii": "REGISTRO", "iva": 100, "estado": "coincide"})],
            "boleta_linea_ids": [(0, 0, {"tipo_dte": 39, "fecha": date(2026, 10, 1), "iva": 570, "cantidad_sii": 2})],
        })
        estados = {o[0]: o[1] for o in revision._observaciones()}
        self.assertEqual((estados["W01"], estados["W01i"], estados["W01N"], estados["W08"], estados["FM01"], estados["W39"]),
                         ("alerta", "ok", "ok", "alerta", "ok", "sin_info"))
        revision.compras_observados = json.dumps([{"rut": "12.345.678-5"}])
        self.assertEqual({o[0]: o[1] for o in revision._observaciones()}["W39"], "alerta")
        revision.compras_observados = json.dumps([])
        self.assertEqual({o[0]: o[1] for o in revision._observaciones()}["W39"], "ok")
        self.assertIn("W08", revision.observaciones_html)

    def test_511_iva_de_documentos_electronicos_recibidos(self):
        _revision, odoo = self._f29(compras=[
            ((33, 10000, [(self.iva_compra, 1900)]), {}),
            ((61, 1000, [(self.iva_compra, 190)]), {}),
            ((33, 15000, [(self.iva_no_rec, 2850)]), {}),
            ((33, 120400, [(self.iva_compra, 22876)]), {"iva_uso_comun": True}),
        ], fact_prop=0.6, codigos=[(511, 32600)])
        self.assertEqual((odoo[511], odoo[514]), (1900 - 190 + 22876, 2850))
        self.assertEqual(odoo[537], 1900 - 190 + 13726)


@tagged("post_install", "-at_install")
class TestXmlPorDocumento(RevisionBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.factura = cls.env["sii.document_class"].search([("sii_code", "=", 33)], limit=1)
        diario = cls.env["account.journal"].search([("company_id", "=", cls.company.id), ("type", "=", "purchase")], limit=1)
        diario.document_class_ids = [(4, cls.factura.id)]
        cls.env["account.tax"].create({
            "name": "IVA 19% compra prueba", "amount": 19, "type_tax_use": "purchase", "sii_code": 14,
            "company_id": cls.company.id,
        })

    def _pendiente(self, sistema, respuestas=None):
        self.company.sii_sistema_xml = sistema
        rcv = self._rcv(self._doc_rcv(123, "PENDIENTE"))
        revision, llamadas, parche = self._revision(dict(respuestas or {}, **{"rcv/documentos": [rcv]}))
        with parche, self._firma():
            revision.action_consultar_compras()
        linea = revision.compras_linea_ids
        self.assertEqual((linea.estado, linea.document_id.source), ("sin_xml", "rcv"))
        return revision, linea, llamadas, parche

    def test_descarga_un_documento_y_no_duplica(self):
        ok = Respuesta(200, {"total": 1, "documentos": [self.documento], "mipyme": True})
        revision, linea, llamadas, parche = self._pendiente("mipyme", {"xml/recibidos": [ok]})
        documento = linea.document_id
        with parche, self._firma():
            linea.action_descargar_xml()
        ruta, cuerpo = llamadas[-1]
        self.assertEqual(ruta, "xml/recibidos")
        self.assertEqual((cuerpo["folio"], cuerpo["tipo_doc"], cuerpo["rut_contraparte"]), (123, 33, "12345678-5"))
        self.assertNotIn("desde", cuerpo)
        self.assertEqual((linea.xml_estado, linea.estado, linea.document_id), ("descargado", "recibido", documento))
        self.assertEqual((documento.source, documento.id_dte), ("xml", "F123T33"))
        self.assertEqual(self.env["mail.message.dte.document"].search_count([("number", "=", "123")]), 1)
        with self.assertRaises(UserError):
            linea.action_descargar_xml()

    def test_el_sii_no_tiene_el_xml_no_se_reintenta(self):
        no_esta = Respuesta(404, {"codigo": "XML_NO_DISPONIBLE", "message": "El SII no tiene el XML de 33 folio 123",
                                  "mipyme": None})
        revision, linea, llamadas, parche = self._pendiente(False, {"xml/recibidos": [no_esta]})
        self.assertEqual(revision.sistema_xml, False)
        with parche, self._firma():
            revision.action_traer_xml()
            with self.assertRaises(UserError):
                linea.action_descargar_xml()
        self.assertEqual(len([l for l in llamadas if l[0] == "xml/recibidos"]), 1)
        self.assertEqual(linea.xml_estado, "no_disponible")
        self.assertIn("El SII no tiene el XML de 33 folio 123", linea.xml_resultado)
        self.assertFalse(self.company.sii_sistema_xml or self.company.sii_sistema_xml_trafquin)
        self.assertEqual((linea.puede_descargar, linea.sin_descarga), (False, True))
        self.assertIn("1", revision.compras_avisos)

    def test_trafquin_informa_que_no_es_mipyme(self):
        no_esta = Respuesta(404, {"codigo": "XML_NO_DISPONIBLE", "message": "La empresa no usa MIPYME", "mipyme": False})
        _revision, linea, _llamadas, parche = self._pendiente(False, {"xml/recibidos": [no_esta]})
        with parche, self._firma():
            linea.action_descargar_xml()
        self.assertIn("no usa el sistema MIPYME", linea.xml_resultado)
        self.assertEqual((self.company.sii_sistema_xml, self.company.sii_sistema_xml_trafquin), (False, "propio"))
        self.assertEqual(self.company._sistema_xml(), "propio")

    def test_software_propio_registra_sin_xml_y_el_xml_completa_la_factura(self):
        revision, linea, llamadas, parche = self._pendiente("propio")
        self.assertEqual((linea.puede_descargar, linea.sin_descarga), (False, True))
        with self.assertRaises(UserError):
            revision.action_traer_xml()
        self.assertFalse([l for l in llamadas if l[0].startswith("xml/")])
        accion = linea.action_registrar_sin_xml()
        move = self.env["account.move"].browse(accion["res_id"])
        self.assertEqual((move.state, move.move_type, move.document_class_id, move.sii_document_number),
                         ("draft", "in_invoice", self.factura, 123))
        self.assertEqual((move.amount_untaxed, move.amount_tax, move.amount_total), (1000, 190, 1190))
        self.assertEqual(move.partner_id.vat, "CL123456785")
        self.assertEqual((linea.move_id, linea.estado, linea.document_id.move_id), (move, "coincide", move))
        self.assertFalse(linea.sin_descarga)
        recibidos = Respuesta(200, {"total": 1, "documentos": [self.documento], "advertencias": []})
        self._obtener(recibidos)
        documento = self.env["mail.message.dte.document"].search([("number", "=", "123")])
        self.assertEqual((len(documento), documento.source, documento.move_id), (1, "xml", move))
        self.assertIn("<Folio>123</Folio>", move.sii_xml_dte)
        self.assertEqual(self.env["account.move"].search_count([
            ("sii_document_number", "=", 123), ("document_class_id", "=", self.factura.id),
            ("move_type", "=", "in_invoice")]), 1)

    def test_pedir_xml_al_proveedor(self):
        _revision, linea, _llamadas, _parche = self._pendiente("propio")
        proveedor = linea._proveedor()
        with self.assertRaises(UserError):
            linea.action_pedir_xml()
        proveedor.dte_email = "dte@proveedor.test"
        linea.action_pedir_xml()
        correo = self.env["mail.mail"].search([("email_to", "=", "dte@proveedor.test")])
        self.assertEqual(len(correo), 1)
        self.assertIn("folio 123", correo.subject)
        self.assertTrue(linea.xml_pedido_fecha)

    def test_si_timbra_documentos_es_software_propio(self):
        self.company.sii_sistema_xml = False
        self.company._informar_mipyme(True)
        self.assertFalse(self.company._timbra_documentos())
        self.assertEqual(self.company._sistema_xml(), "mipyme")
        caf = self.env["dte.caf"].create({"filename": "caf_33.xml", "company_id": self.company.id})
        self.assertEqual(self.company._sistema_xml(), "propio")
        caf.unlink()
        cliente = self.env["res.partner"].create({"name": "Cliente prueba"})
        factura = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": cliente.id, "company_id": self.company.id,
            "invoice_line_ids": [(0, 0, {"name": "Servicio", "quantity": 1, "price_unit": 1000})],
        })
        self.assertEqual(self.company._sistema_xml(), "mipyme")
        factura.sii_barcode = "<TED version=\"1.0\"><DD><F>1</F></DD></TED>"
        self.assertEqual(self.company._sistema_xml(), "propio")
        self.env["dte.caf"].create({"filename": "caf_33.xml", "company_id": self.company.id})
        self.company.invalidate_recordset(["sii_sistema_xml_aviso"])
        self.assertEqual(self.company._sistema_xml(), "propio")
        self.assertIn("aunque trafquin informa que usa el sistema MIPYME", self.company.sii_sistema_xml_aviso)
        _revision, linea, llamadas, _parche = self._pendiente(False)
        self.assertEqual((linea.puede_descargar, linea.sin_descarga), (False, True))
        self.assertFalse([l for l in llamadas if l[0].startswith("xml/")])
        self.company.sii_sistema_xml = "mipyme"
        self.assertEqual(self.company._sistema_xml(), "mipyme")
        self.assertFalse(self.company.sii_sistema_xml_aviso)


@tagged("post_install", "-at_install")
class TestPropuestaF29EnElSii(HttpCase):

    def setUp(self):
        super().setUp()
        self.company = self.env.company
        self.company.partner_id.document_number = "76.086.428-5"
        self.company.sudo().sii_clave_tributaria = "clave-de-prueba"
        self.admin = new_test_user(self.env, "f29_admin", groups="base.group_system,account.group_account_manager",
                                   password="f29_admin_clave_1")
        self.contador = new_test_user(self.env, "f29_contador", groups="account.group_account_manager")
        self.revision = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": "202610"})

    def test_ingreso_automatico_de_un_solo_uso(self):
        accion = self.revision.with_user(self.admin).action_abrir_propuesta_sii()
        self.assertEqual(accion["target"], "new")
        self.assertTrue(accion["url"].startswith("/l10n_cl_fe/sii/propuesta_f29/%s/" % self.revision.id))
        self.assertNotIn("clave-de-prueba", accion["url"])
        self.authenticate("f29_admin", "f29_admin_clave_1")
        resp = self.url_open(accion["url"], allow_redirects=False)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("no-store", resp.headers["Cache-Control"])
        self.assertIn('action="https://zeusr.sii.cl/cgi_AUT2000/CAutInicio.cgi"', resp.text)
        for campo in ('name="rut" value="76086428"', 'name="dv" value="5"', 'name="rutcntr" value="76.086.428-5"',
                      'name="clave" value="clave-de-prueba"',
                      'name="referencia" value="https://www4.sii.cl/propuestaf29ui/index.html#/infopropuesta"'):
            self.assertIn(campo, resp.text)
        otra = self.url_open(accion["url"], allow_redirects=False)
        self.assertEqual(otra.status_code, 303)
        self.assertEqual(otra.headers["Location"], "https://zeusr.sii.cl/AUT2000/InicioAutenticacion/"
                         "IngresoRutClave.html?https://www4.sii.cl/propuestaf29ui/index.html#/infopropuesta")
        self.assertNotIn("clave-de-prueba", otra.text)

    def test_sin_administrador_ni_clave_valida_solo_abre_el_login(self):
        login = "https://zeusr.sii.cl/AUT2000/InicioAutenticacion/IngresoRutClave.html?" \
                "https://www4.sii.cl/propuestaf29ui/index.html#/infopropuesta"
        self.assertEqual(self.revision.with_user(self.contador).action_abrir_propuesta_sii()["url"], login)
        self.company.sudo()._marcar_clave_sii_rechazada()
        self.assertEqual(self.revision.with_user(self.admin).action_abrir_propuesta_sii()["url"], login)
        self.assertFalse(self.revision.sudo()._formulario_ingreso_sii("cualquiera"))


@tagged("post_install", "-at_install")
class TestAsientoF29(RevisionBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Cuenta = cls.env["account.account"]

        def cuenta(codigo, tipo, **extra):
            return Cuenta.create(dict({"code": codigo, "name": "F29 prueba %s" % codigo, "account_type": tipo,
                                       "company_ids": [(6, 0, cls.company.ids)]}, **extra))

        cls.debito = cuenta("F29T01", "liability_current")
        cls.credito = cuenta("F29T02", "asset_current")
        cls.remanente = cuenta("F29T03", "asset_current")
        cls.ppm = cuenta("F29T04", "asset_current")
        cls.retencion = cuenta("F29T05", "liability_current")
        cls.unico = cuenta("F29T06", "liability_current")
        cls.por_pagar = cuenta("F29T07", "liability_payable", reconcile=True)
        cls.recargo = cuenta("F29T08", "expense")
        cls.diario = cls.env["account.journal"].create({"name": "F29 prueba", "code": "F29T", "type": "general",
                                                        "company_id": cls.company.id})
        cls.banco = cls.env["account.journal"].search([("type", "=", "bank"), ("company_id", "=", cls.company.id)], limit=1) \
            or cls.env["account.journal"].create({"name": "Banco F29", "code": "BF29", "type": "bank", "company_id": cls.company.id})
        cls.company.write({
            "f29_cuenta_debito_id": cls.debito.id, "f29_cuenta_credito_id": cls.credito.id,
            "f29_cuenta_remanente_id": cls.remanente.id, "f29_cuenta_ppm_id": cls.ppm.id,
            "f29_cuenta_retencion_id": cls.retencion.id, "f29_cuenta_impuesto_unico_id": cls.unico.id,
            "f29_cuenta_por_pagar_id": cls.por_pagar.id, "f29_cuenta_recargo_id": cls.recargo.id,
            "f29_diario_id": cls.diario.id,
        })

    def _periodo(self, codigos, periodo="202610"):
        revision = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": periodo})
        revision.f29_linea_ids = [(0, 0, {"codigo": c, "valor_sii": v}) for c, v in codigos.items()]
        return revision

    def _lineas(self, move):
        return sorted((l.account_id.code, l.debit, l.credit) for l in move.line_ids)

    def test_asiento_con_iva_a_pagar_retenciones_y_tesoreria(self):
        revision = self._periodo({538: 100000, 537: 30000, 89: 70000, 151: 5000, 48: 2000})
        revision.action_generar_asiento_f29()
        move = revision.move_f29_id
        self.assertEqual((move.state, move.date, move.journal_id, move.ref), ("draft", date(2026, 10, 31), self.diario, "F29 202610"))
        self.assertEqual(self._lineas(move), sorted([
            ("F29T01", 100000, 0), ("F29T02", 0, 30000), ("F29T07", 0, 70000),
            ("F29T05", 5000, 0), ("F29T07", 0, 5000), ("F29T06", 2000, 0), ("F29T07", 0, 2000)]))
        tesoreria = move.line_ids.filtered(lambda l: l.account_id == self.por_pagar).partner_id
        self.assertEqual(tesoreria.document_number, "60.805.000-0")
        self.assertEqual(self.company.f29_tesoreria_id, tesoreria)

    def test_asiento_con_remanente_202610(self):
        revision = self._periodo({538: 91879, 520: 32600, 504: 148502, 537: 181102, 77: 89223, 62: 604})
        revision.action_generar_asiento_f29()
        self.assertEqual(self._lineas(revision.move_f29_id), sorted([
            ("F29T01", 91879, 0), ("F29T02", 0, 32600), ("F29T03", 0, 148502), ("F29T03", 89223, 0),
            ("F29T04", 604, 0), ("F29T07", 0, 604)]))

    def test_usa_lo_ajustado_regenera_el_borrador_y_no_toca_el_publicado(self):
        revision = self._periodo({502: 81000, 111: 19000, 538: 100000, 537: 30000, 89: 70000})
        revision.action_generar_asiento_f29()
        move = revision.move_f29_id
        revision.f29_linea_ids.filtered(lambda l: l.codigo == 537).write({"valor_propuesto": 40000, "motivo": "Factura"})
        revision.f29_linea_ids.filtered(lambda l: l.codigo == 111).write({"valor_propuesto": 29000, "motivo": "Boletas"})
        revision.action_generar_asiento_f29()
        self.assertEqual(revision.move_f29_id, move)
        self.assertEqual(self._lineas(move), sorted([("F29T01", 110000, 0), ("F29T02", 0, 30000), ("F29T07", 0, 80000)]))
        move.action_post()
        with self.assertRaises(UserError):
            revision.action_generar_asiento_f29()
        self.assertEqual(move.state, "posted")

    def test_falta_configuracion_y_descuadre(self):
        self.company.f29_cuenta_ppm_id = False
        revision = self._periodo({538: 100000, 537: 30000, 89: 70000, 62: 500})
        with self.assertRaisesRegex(UserError, "Cuenta PPM pagado"):
            revision.action_generar_asiento_f29()
        revision = self._periodo({538: 100000, 537: 30000, 89: 60000}, periodo="202609")
        with self.assertRaisesRegex(UserError, "no cuadra"):
            revision.action_generar_asiento_f29()

    def _pagar(self, revision, recargo=0):
        accion = revision.action_registrar_pago_f29()
        with Form(self.env["account.payment.register"].with_context(accion["context"])) as pago:
            pago.journal_id = self.banco
            if recargo:
                pago.f29_recargo = recargo
        return pago.record, pago.record.action_create_payments()

    def test_pago_con_y_sin_multa_queda_conciliado(self):
        for periodo, recargo in (("202610", 0), ("202609", 1500)):
            revision = self._periodo({538: 100000, 537: 30000, 89: 70000, 62: 604}, periodo=periodo)
            revision.f29_declaracion_ids = [
                (0, 0, {"estado": "Pago Rechazado", "folio": "111", "fecha": date(2026, 11, 12)}),
                (0, 0, {"estado": "Vigente", "folio": "222", "fecha": date(2026, 11, 20)}),
            ]
            revision.action_generar_asiento_f29()
            with self.assertRaises(UserError):
                revision.action_registrar_pago_f29()
            revision.move_f29_id.action_post()
            asistente, _accion = self._pagar(revision, recargo)
            self.assertEqual((asistente.amount, asistente.communication, asistente.payment_date),
                             (70604 + recargo, "F29 %s folio 222" % periodo, date(2026, 11, 20)))
            self.assertTrue(revision.f29_pagado)
            self.assertTrue(all(revision.move_f29_id.line_ids.filtered(lambda l: l.account_id == self.por_pagar).mapped("reconciled")))
            gasto = self.env["account.move.line"].search([("account_id", "=", self.recargo.id), ("name", "ilike", periodo)])
            self.assertEqual(sum(gasto.mapped("debit")), recargo)
            with self.assertRaises(UserError):
                revision.action_registrar_pago_f29()

    def test_revision_de_saldos(self):
        revision = self._periodo({538: 100000, 537: 30000, 89: 70000})
        self.assertIn("IVA Débito Fiscal", revision.f29_saldos_aviso)
        self.env["account.move"].create({
            "move_type": "entry", "journal_id": self.diario.id, "date": date(2026, 10, 15),
            "line_ids": [(0, 0, {"account_id": self.debito.id, "credit": 100000}),
                         (0, 0, {"account_id": self.credito.id, "debit": 30000}),
                         (0, 0, {"account_id": self.recargo.id, "debit": 70000})],
        }).action_post()
        revision.invalidate_recordset(["f29_saldos_aviso"])
        self.assertFalse(revision.f29_saldos_aviso)
        revision.action_generar_asiento_f29()
        revision.move_f29_id.action_post()
        revision.invalidate_recordset(["f29_saldos_aviso"])
        self.assertFalse(revision.f29_saldos_aviso)

    def test_estado_del_periodo(self):
        revision = self._periodo({538: 100000, 537: 30000, 89: 70000, 62: 604}, periodo="202608")
        self.assertEqual((revision.estado, revision.vence, revision.anio), ("sin_consultar", date(2026, 9, 21), "2026"))
        self.assertEqual((revision.f29_debito, revision.f29_credito, revision.f29_iva_pagar, revision.f29_ppm),
                         (100000, 30000, 70000, 604))
        revision.write({"f29_fecha": fields.Datetime.now(), "f29_error": "falló"})
        self.assertEqual(revision.estado, "error")
        revision.write({"f29_error": False, "compras_fecha": fields.Datetime.now(), "compras_error": "falló"})
        self.assertEqual((revision.estado, revision.rcv_estado), ("error", "error"))
        revision.compras_error = False
        self.assertEqual(revision.rcv_estado, "incompleto")
        linea = revision.f29_linea_ids.filtered(lambda l: l.codigo == 538)
        linea.write({"calculado_odoo": True, "valor_odoo": 90000})
        self.assertEqual((revision.estado, revision.f29_descuadres), ("descuadrado", 1))
        linea.valor_odoo = 100000
        self.assertEqual(revision.estado, "por_declarar")
        revision.f29_declaracion_ids = [(0, 0, {"estado": "Pago Rechazado", "folio": "1", "fecha": date(2026, 9, 18),
                                                "total": 70604})]
        self.assertEqual((revision.estado, revision.declaracion_estado), ("por_declarar", "Pago Rechazado"))
        revision.f29_declaracion_ids = [(0, 0, {"estado": "Vigente", "folio": "2", "fecha": date(2026, 9, 21),
                                                "total": 70604})]
        self.assertEqual((revision.estado, revision.declaracion_estado, revision.declaracion_fecha,
                          revision.declaracion_total), ("declarado", "Vigente", date(2026, 9, 21), 70604))
        revision.f29_declaracion_ids.filtered(lambda d: d.folio == "2").fecha = date(2026, 9, 22)
        self.assertEqual((revision.estado, revision.contable_estado), ("fuera_plazo", "sin_asiento"))
        revision.action_generar_asiento_f29()
        self.assertEqual(revision.contable_estado, "borrador")
        revision.move_f29_id.action_post()
        self.assertEqual((revision.contable_estado, revision.estado), ("publicado", "fuera_plazo"))
        self._pagar(revision)
        self.assertEqual((revision.contable_estado, revision.estado), ("pagado", "pagado"))
        Periodo = self.env["sii.revision.periodo"]
        self.assertEqual(Periodo.search([("estado", "=", "pagado"), ("id", "=", revision.id)]), revision)

    def test_lista_muestra_lo_que_se_paga_y_lo_declarado_prima(self):
        sin_declarar = self._periodo({538: 300, 504: 40, 537: 100, 89: 200, 62: 100, 151: 50}, periodo="202603")
        self.assertEqual((sin_declarar.impuesto_ventas, sin_declarar.impuesto_compras, sin_declarar.total_a_pagar,
                          sin_declarar.recargo_f29), (300, 60, 350, 0))
        sin_declarar.f29_linea_ids = [(0, 0, {"codigo": 547, "valor_sii": 360})]
        self.assertEqual(sin_declarar.total_a_pagar, 360)
        revision = self._periodo({538: 14, 89: 14}, periodo="202604")
        revision.write({"f29_fecha": fields.Datetime.now()})
        revision.f29_linea_ids.filtered(lambda l: l.codigo == 538).write({"calculado_odoo": True, "valor_odoo": 0})
        self.assertEqual((revision.f29_descuadres, revision.estado), (1, "descuadrado"))
        self._declarado(revision, "7101", {538: 14, 89: 14, 91: 14, 93: 70589, 795: 49412, 94: 21191}, {
            "total_plazo_legal": 14, "ipc": 0, "intereses_multas": 70589, "condonacion": 49412,
            "total_con_recargo": 21191})
        self.assertEqual((revision.total_a_pagar, revision.recargo_f29, revision.f29_iva_pagar), (21191, 21177, 14))
        self.assertEqual((revision.f29_debito_odoo, revision.f29_descuadres), (0, 1))
        self.assertEqual(revision.estado, "fuera_plazo")
        revision.action_generar_asiento_f29()
        revision.move_f29_id.action_post()
        self._pagar(revision)
        self.assertEqual(revision.estado, "pagado")

    def test_vence_al_dia_21(self):
        futuro = self._periodo({538: 1}, periodo="209901")
        futuro.f29_fecha = fields.Datetime.now()
        self.assertEqual((futuro.estado, futuro.vence), ("en_plazo", date(2099, 2, 20)))
        with patch.object(fields.Date, "context_today", return_value=date(2099, 2, 21)):
            self.env["sii.revision.periodo"]._actualizar_vencidos()
        self.assertEqual(futuro.estado, "por_declarar")

    def _declarado(self, revision, folio, codigos, pago=None, estado="Vigente", fecha="2026-05-21"):
        revision.f29_declaracion_ids = [(0, 0, {"estado": "Vigente", "folio": folio, "fecha": _fecha_iso(fecha)})]
        respuesta = Respuesta(200, {"id_peticion": "x", "data": {
            "periodo": revision.periodo, "folio": folio, "fecha": fecha, "estado": estado, "total": 0,
            "codigos": [{"codigo": c, "valor": v, "glosa": ""} for c, v in codigos.items()] if codigos is not None else None,
            "pago": pago, "total_pagado": (pago or {}).get("total_con_recargo") or codigos and codigos.get(91),
            "pdf_b64": base64.b64encode(b"%PDF-1.4 prueba").decode(), "declaraciones": [],
        }})
        llamadas, parche = self._con_trafquin({"f29/declaracion": [respuesta]})
        with parche, self._firma():
            revision.action_traer_declarado()
        return llamadas

    def test_f29_declarado_con_multa_202604(self):
        revision = self._periodo({538: 14, 89: 14}, periodo="202604")
        llamadas = self._declarado(revision, "7001", {538: 14, 89: 14, 91: 14, 92: 0, 93: 70589, 795: 49412, 94: 21191}, {
            "total_plazo_legal": 14, "ipc": 0, "intereses_multas": 70589, "condonacion": 49412,
            "total_con_recargo": 21191, "tipo_declaracion": "Primitiva", "corrige_folios": [],
            "fecha_presentacion": "21/05/2026", "banco": "ESTADO", "medio_pago": "PEL"})
        self.assertEqual((llamadas[0][0], llamadas[0][1]["folio"], llamadas[0][1]["periodo"]),
                         ("f29/declaracion", 7001, "202604"))
        self.assertIsInstance(llamadas[0][1]["folio"], int)
        declaracion = revision._vigente()
        self.assertEqual((declaracion.recargo, declaracion.total_pagado, declaracion.banco, declaracion.medio_pago,
                          declaracion.fecha_presentacion, declaracion.pago_sin_detalle),
                         (21177, 21191, "ESTADO", "PEL", date(2026, 5, 21), False))
        self.assertEqual(declaracion.pdf_id.mimetype, "application/pdf")
        self.assertEqual((declaracion.pdf_id.res_model, declaracion.pdf_id.res_id), ("sii.revision.periodo", revision.id))
        self.assertFalse(revision.declarado_aviso)
        revision.action_generar_asiento_f29()
        revision.move_f29_id.action_post()
        asistente, _accion = self._pagar(revision)
        self.assertEqual((asistente.amount, asistente.f29_recargo, asistente.payment_date), (21191, 21177, date(2026, 5, 21)))
        self.assertTrue(revision.f29_pagado)
        gasto = self.env["account.move.line"].search([("account_id", "=", self.recargo.id), ("name", "ilike", "202604")])
        self.assertEqual(sum(gasto.mapped("debit")), 21177)

    def test_f29_declarado_distinto_de_la_propuesta_202512(self):
        revision = self._periodo({502: 270425, 538: 416970, 537: 9568, 89: 407402, 62: 2649}, periodo="202512")
        self._declarado(revision, "8761732906", {502: 256175, 538: 402720, 537: 9568, 89: 393152, 62: 2649,
                                                  91: 395801, 547: 395801}, fecha="2026-01-20")
        self.assertTrue(revision.declarado_sin_pago)
        self.assertIn("502: propuesta 270.425, declarado 256.175 (-14.250)", revision.declarado_aviso)
        lineas = {l.codigo: (l.valor_declarado, l.declarado_difiere) for l in revision.f29_linea_ids}
        self.assertEqual((lineas[502], lineas[537]), ((256175, True), (9568, False)))
        self.assertEqual((revision.f29_debito, revision.f29_iva_pagar, revision.f29_ppm), (402720, 393152, 2649))
        self.assertEqual(revision._recargo_f29(), 0)
        revision.action_generar_asiento_f29()
        self.assertEqual(self._lineas(revision.move_f29_id), sorted([
            ("F29T01", 402720, 0), ("F29T02", 0, 9568), ("F29T07", 0, 393152), ("F29T04", 2649, 0), ("F29T07", 0, 2649)]))

    def test_f29_declarado_con_postergacion_y_sin_vigente(self):
        postergado = self.env["account.account"].create({"code": "F29T10", "name": "IVA postergado",
                                                          "account_type": "liability_current"})
        self.company.f29_cuenta_postergado_id = postergado
        revision = self._periodo({538: 100000, 537: 30000, 89: 70000}, periodo="202605")
        self._declarado(revision, "7002", {538: 100000, 537: 30000, 89: 70000, 779: 50000, 91: 20000})
        revision.action_generar_asiento_f29()
        self.assertEqual(self._lineas(revision.move_f29_id), sorted([
            ("F29T01", 100000, 0), ("F29T02", 0, 30000), ("F29T10", 0, 50000), ("F29T07", 0, 20000)]))
        otra = self._periodo({538: 1}, periodo="202606")
        with self.assertRaises(UserError):
            self._declarado(otra, "7003", {538: 1}, estado="Pago Rechazado")

    def test_consultar_la_propuesta_conserva_el_detalle_declarado(self):
        revision = self._periodo({538: 100000, 537: 30000, 89: 70000}, periodo="202607")
        self._declarado(revision, "7004", {538: 100000, 537: 30000, 89: 70000, 91: 70000})
        propuesta = Respuesta(200, {"data": {
            "periodo": "202607", "tiene_propuesta": True, "estado": 1, "descripcion_estado": "Declarada",
            "codigos": [{"codigo": 538, "valor": 100000}], "codigos_administrativos": [], "grupos": {},
            "declaraciones": [{"estado": "Vigente", "folio": "7004", "fecha": "2026-08-20", "total": 70000}],
        }})
        llamadas, parche = self._con_trafquin({"f29/propuesta": [propuesta]})
        with parche, self._firma():
            revision.action_consultar_f29()
        self.assertEqual(len(revision.f29_declaracion_ids), 1)
        self.assertEqual(revision._vigente()._valores().get(91), 70000)

    def _utm(self, *valores):
        utm = self.env["res.currency"].with_context(active_test=False).search([("name", "=", "UTM")], limit=1)
        self.env["res.currency.rate"].create([
            {"currency_id": utm.id, "company_id": self.company.id, "name": fecha, "rate": 1 / valor}
            for fecha, valor in valores
        ])

    def test_remanente_reajustado_por_utm(self):
        self._utm((date(2025, 8, 1), 67000), (date(2025, 9, 1), 67199), (date(2025, 10, 1), 67491))
        agosto = self._periodo({77: 12431}, periodo="202508")
        septiembre = self._periodo({504: 12468, 77: 54008}, periodo="202509")
        octubre = self._periodo({504: 54243}, periodo="202510")
        noviembre = self._periodo({504: 55000}, periodo="202511")
        for revision in (septiembre, octubre):
            fm01 = {o[0]: o for o in revision._observaciones()}["FM01"]
            self.assertEqual(fm01[1], "ok", fm01)
        self.assertEqual({o[0]: o for o in octubre._observaciones()}["FM01"][4], 54243)
        self.assertEqual({o[0]: o[1] for o in noviembre._observaciones()}["FM01"], "sin_info")
        agosto.f29_linea_ids.write({"calculado_odoo": True, "valor_odoo": 12431})
        valor, origen = septiembre._remanente_anterior(12468)
        self.assertEqual(valor, 12468)
        self.assertIn("reajustado por UTM 67.199 / 67.000", origen)
        septiembre.f29_linea_ids.filtered(lambda l: l.codigo == 77).write({"calculado_odoo": True, "valor_odoo": 54008})
        self.assertEqual(octubre._remanente_anterior(54243)[0], 54243)
        octubre.f29_linea_ids = [(0, 0, {"codigo": 77, "valor_sii": 1000, "calculado_odoo": True, "valor_odoo": 1000})]
        self.assertEqual(noviembre._remanente_anterior(1010), (1010, noviembre._remanente_anterior(1010)[1]))
        self.assertIn("tolerancia", noviembre._remanente_anterior(1010)[1])
        self.assertEqual(noviembre._remanente_anterior(1500)[0], 1000)

    def test_asiento_con_reajuste_del_remanente(self):
        self.company.f29_cuenta_reajuste_id = False
        self._periodo({77: 54008}, periodo="202509")
        octubre = self._periodo({538: 10000, 520: 5000, 504: 54243, 537: 59243, 77: 49243}, periodo="202510")
        with self.assertRaisesRegex(UserError, "Cuenta reajuste remanente"):
            octubre.action_generar_asiento_f29()
        reajuste = self.env["account.account"].create({"code": "F29T09", "name": "Reajuste remanente", "account_type": "income_other",
                                                       "company_ids": [(6, 0, self.company.ids)]})
        self.company.f29_cuenta_reajuste_id = reajuste
        octubre.action_generar_asiento_f29()
        self.assertEqual(self._lineas(octubre.move_f29_id), sorted([
            ("F29T03", 235, 0), ("F29T09", 0, 235), ("F29T01", 10000, 0), ("F29T02", 0, 5000),
            ("F29T03", 0, 54243), ("F29T03", 49243, 0)]))


@tagged("post_install", "-at_install")
class TestRevisionMultiCompania(TransactionCase):

    def _revision(self, company):
        revision = self.env["sii.revision.periodo"].create({"company_id": company.id, "periodo": "202610"})
        linea = self.env["sii.revision.f29.linea"].create({"revision_id": revision.id, "codigo": 538})
        rcv = self.env["sii.revision.rcv.linea"].create({"revision_id": revision.id, "libro": "compras", "folio": "1"})
        self.env["sii.revision.f29.aporte"].create({"linea_id": linea.id, "origen": "odoo", "rcv_linea_id": rcv.id})
        self.env["sii.revision.f29.declaracion"].create({"revision_id": revision.id})
        self.env["sii.revision.boleta.linea"].create({"revision_id": revision.id})
        return revision

    def test_cada_compania_ve_solo_sus_periodos(self):
        uno = self.env.company
        dos = self.env["res.company"].create({"name": "Compañía 2 F29", "currency_id": uno.currency_id.id})
        usuario = new_test_user(self.env, "f29_multi", groups="account.group_account_invoice",
                                company_id=uno.id, company_ids=[(6, 0, (uno | dos).ids)])
        mia, otra = self._revision(uno), self._revision(dos)
        modelos = ["sii.revision.periodo", "sii.revision.f29.linea", "sii.revision.f29.aporte",
                   "sii.revision.f29.declaracion", "sii.revision.rcv.linea", "sii.revision.boleta.linea"]
        for companias, ve, no_ve in ((uno, mia, otra), (dos, otra, mia), (uno | dos, mia | otra, False)):
            env = self.env(user=usuario, context={"allowed_company_ids": companias.ids})
            for modelo in modelos:
                registros = env[modelo].search([]).sudo()
                visibles = registros if modelo == "sii.revision.periodo" else registros.mapped(
                    "linea_id.revision_id" if modelo == "sii.revision.f29.aporte" else "revision_id")
                self.assertEqual(visibles & (mia | otra), ve, modelo)
                if no_ve:
                    self.assertNotIn(no_ve, visibles, modelo)

    def test_inicio_de_actividades(self):
        company = self.env["res.company"].create({"name": "Inicio F29", "currency_id": self.env.company.currency_id.id})
        Periodo = self.env["sii.revision.periodo"]
        julio = Periodo.create({"company_id": company.id, "periodo": "202507"})
        julio.f29_declaracion_ids = [(0, 0, {"estado": "Vigente", "folio": "9", "fecha": date(2025, 8, 20)})]
        with self.assertRaises(UserError) as error:
            Periodo.create({"company_id": company.id, "periodo": "202506"})
        self.assertIn("anterior al inicio de actividades", str(error.exception))
        self.assertFalse(Periodo._revision_de(company, "202506"))
        self.assertEqual(Periodo._revision_de(company, "202507"), julio)
        self.assertTrue(Periodo._revision_de(company, "202508"))
        company.start_date = date(2025, 8, 1)
        self.assertEqual(julio.estado, "fuera_actividades")
        company.start_date = date(2025, 1, 1)
        self.assertEqual(julio.estado, "sin_consultar")
        self.assertTrue(Periodo.create({"company_id": company.id, "periodo": "202506"}))

    def test_calculo_odoo_solo_con_documentos_de_la_compania(self):
        uno = self.env.company
        dos = self.env["res.company"].create({"name": "Compañía 2 F29", "currency_id": uno.currency_id.id})
        clase = self.env["sii.document_class"].search([("sii_code", "=", 33)], limit=1)
        proveedor = self.env["res.partner"].create({"name": "Proveedor común", "vat": "CL123456785",
                                                     "document_number": "12.345.678-5"})
        env = self.env(context={"allowed_company_ids": [uno.id, dos.id]})
        datos = {}
        for company in (uno, dos):
            diario = self.env["account.journal"].create({"name": "Compras F29 %s" % company.id, "code": "CF%s" % company.id,
                                                         "type": "purchase", "company_id": company.id})
            move = env["account.move"].with_company(company).create({
                "move_type": "in_invoice", "journal_id": diario.id, "partner_id": proveedor.id,
                "invoice_date": date(2026, 10, 5), "date": date(2026, 10, 5),
                "document_class_id": clase.id, "sii_document_number": 77,
            })
            consumo = self.env["account.move.consumo_folios"].create({
                "name": "CF %s" % company.id, "company_id": company.id,
                "fecha_inicio": date(2026, 10, 1), "fecha_final": date(2026, 10, 1),
            })
            consumo.sii_result = "Proceso"
            revision = env["sii.revision.periodo"].create({"company_id": company.id, "periodo": "202610"})
            linea = env["sii.revision.rcv.linea"].create({"revision_id": revision.id, "libro": "compras",
                                                          "tipo_dte": 33, "folio": "77", "rut": "12345678-5"})
            datos[company] = (revision, move, consumo, linea)
        for company, (revision, move, consumo, linea) in datos.items():
            self.assertEqual(revision._libro("COMPRA").move_ids, move)
            self.assertEqual(revision._consumos_folios(), consumo)
            linea._cruzar()
            self.assertEqual(linea.move_id, move)


@tagged("post_install", "-at_install")
class TestReglasMultiCompania(TrafquinBase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dos = cls.env["res.company"].create({"name": "Compañía 2 reglas", "currency_id": cls.company.currency_id.id})
        cls.usuario = new_test_user(cls.env, "multi_dte", groups="base.group_system,account.group_account_manager",
                                    company_id=cls.dos.id, company_ids=[(6, 0, (cls.company | cls.dos).ids)])

    def test_cola_de_envio_con_dos_companias(self):
        Cola = self.env["sii.cola_envio"]
        colas = Cola.create([{"company_id": c.id, "tipo_trabajo": "consulta"} for c in (self.company, self.dos)])
        como_usuario = Cola.with_user(self.usuario)
        self.assertEqual(como_usuario.with_context(allowed_company_ids=[self.dos.id]).search([("id", "in", colas.ids)]),
                         colas[1])
        self.assertEqual(como_usuario.with_context(allowed_company_ids=(self.company | self.dos).ids).search(
            [("id", "in", colas.ids)]), colas)
        procesadas = []
        with patch.object(type(Cola), "_autopack"), patch.object(
                type(Cola), "_procesar_cola_segura", autospec=True,
                side_effect=lambda cola, tipo: procesadas.append(cola.id)):
            self.env(context={"allowed_company_ids": [self.dos.id]})["sii.cola_envio"]._cron_procesar_cola()
        self.assertTrue(set(colas.ids) <= set(procesadas))

    def test_recepcion_de_xml_de_otra_compania_del_usuario(self):
        wiz = self.env["sii.dte.upload_xml.wizard"].with_user(self.usuario).with_context(
            allowed_company_ids=[self.dos.id]).create({
                "xml_file": self.documento["xml_b64"], "filename": "F123T33.xml", "type": "compras",
                "pre_process": True, "option": "upload", "action": "manual",
            })
        doc = self.env["mail.message.dte.document"].browse(wiz.confirm(ret=True) or [])
        self.assertEqual(doc.number, "123")
        self.assertEqual(doc.company_id, self.company)
        self.assertFalse(self.env["mail.message.dte.document"].with_user(self.usuario).with_context(
            allowed_company_ids=[self.dos.id]).search([("id", "in", doc.ids)]))


@tagged("post_install", "-at_install")
class TestLibroDeLaDeclaracion(RevisionBase):

    def test_libro_con_fecha_del_ultimo_dia_del_periodo(self):
        revision = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": "202602"})
        libro = revision._libro("VENTA")
        self.assertEqual(libro.date, date(2026, 2, 28))
        libro.date = date(2026, 11, 20)
        self.assertEqual(revision._libro("VENTA"), libro)
        self.assertEqual(libro.date, date(2026, 2, 28))

    def test_linea_48_no_va_al_xml_del_libro(self):
        from odoo.addons.l10n_cl_fe.models import libro as modulo_libro
        iva = self.env["account.tax"].search([("sii_code", "=", 14), ("type_tax_use", "=", "sale"),
                                              ("company_id", "=", self.company.id)], limit=1)
        Clase = self.env["sii.document_class"]
        libro = self.env["account.move.book"].create({
            "name": "VENTA 2026-10", "company_id": self.company.id, "periodo_tributario": "2026-10",
            "tipo_operacion": "VENTA", "tipo_libro": "MENSUAL",
            "boletas": [(0, 0, {"tipo_boleta": Clase.search([("sii_code", "=", codigo)], limit=1).id,
                                "rango_inicial": 1, "rango_final": 1, "cantidad_boletas": 1, "neto": 100,
                                "impuesto": iva.id, "monto_impuesto": 19, "monto_total": 119})
                        for codigo in (39, 48)],
        })
        self.assertEqual([(b.tipo_boleta.sii_code, b.informativa) for b in libro.boletas], [(39, False), (48, True)])
        enviados = {}

        def armar(datos):
            enviados.update(datos)
            return {"sii_xml_request": "<EnvioLibro/>"}

        Boletas = type(self.env["account.move.book.boletas"])
        with patch.object(type(libro), "_get_datos_empresa", return_value={}), \
                patch.object(Boletas, "_dte", lambda b: {"TpoDoc": b.tipo_boleta.sii_code}, create=True), \
                patch.object(modulo_libro.fe, "libro", side_effect=armar):
            libro._validar()
        self.assertEqual(enviados["Libro"]["boletas"], [{"TpoDoc": 39}])


@tagged("post_install", "-at_install")
class TestBoletasDiariasSii(RevisionBase):

    def test_diario_declara_ventas_sincroniza_los_medios_de_la_compania(self):
        diario = self.env["account.journal"].create({"name": "Pasarela 48", "code": "PS48", "type": "bank",
                                                     "company_id": self.company.id})
        self.assertFalse(diario.sii_declara_ventas)
        diario.write({"sii_declara_ventas": True, "sii_canal_48": "internet"})
        self.assertIn(diario, self.company.medios_de_pago_electronico)
        self.assertIn(diario, self.company.medios_de_pago_internet)
        self.assertEqual(self.env["account.journal"].search([("sii_declara_ventas", "=", True), ("id", "=", diario.id)]),
                         diario)
        diario.sii_canal_48 = "presencial"
        self.assertNotIn(diario, self.company.medios_de_pago_internet)
        diario.sii_declara_ventas = False
        self.assertNotIn(diario, self.company.medios_de_pago_electronico)
        self.assertFalse(diario.sii_canal_48)

    def test_lee_el_contrato_de_boletas_diarias(self):
        revision = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": "202406"})
        self.assertEqual(revision._tipos_con_boletas([
            {"tipo_dte": 48, "cantidad": 30}, {"tipo_dte": 39, "cantidad": 0}, {"tipo_dte": 33, "cantidad": 5}]), [48])
        respuesta = Respuesta(200, {"id_peticion": "x", "data": {
            "periodo": "202406", "tipo_dte": 48, "sin_datos": False, "tiene_canal": True, "resumen_codigo": None,
            "modificados": 1,
            "totales": {"cantidad": 3, "neto": 91517, "exento": 0, "iva": 17388, "total": 108905,
                        "iva_terceros": None, "operaciones_exentas": None},
            "dias": [
                {"dia": 3, "fecha": "2024-06-03", "canal": "internet", "canal_sii": 1, "cantidad": 2, "neto": 50000,
                 "exento": None, "iva": 9500, "total": 59500, "modificado": True, "motivo_codigo": 4,
                 "motivo": "facturas 10 y 11"},
                {"dia": 3, "fecha": "2024-06-03", "canal": None, "canal_sii": None, "cantidad": 1, "neto": 41517,
                 "exento": 0, "iva": 7888, "total": 49405, "modificado": False},
            ]}})
        llamadas, parche = self._con_trafquin({"rcv/boletas_diarias": [respuesta]})
        with parche, self._firma():
            datos = revision._boletas_diarias_sii(48)
        self.assertEqual(llamadas[0][1]["tipo_doc"], 48)
        self.assertEqual(datos["totales"]["total"], 108905)
        internet, sin_canal = datos["dias"]
        self.assertEqual((internet["fecha"], internet["canal"], internet["exento"], internet["motivo_codigo"]),
                         (date(2024, 6, 3), "internet", None, "4"))
        self.assertEqual((sin_canal["canal"], sin_canal["modificado"]), (False, False))
        self.assertFalse(datos["aviso"])

    def _consultar_boletas_diarias(self, revision, d39, d48):
        lista = Respuesta(200, {"data": {"listo": True, "dias": [], "resumen_tipos": [
            {"tipo_dte": 39, "cantidad": 2, "sii": {}}, {"tipo_dte": 48, "cantidad": 3, "sii": {}}]}})
        llamadas, parche = self._con_trafquin({"rcv/documentos": [lista],
                                               "rcv/boletas_diarias": [self._boletas_diarias(39, *d39),
                                                                       self._boletas_diarias(48, *d48)]})
        with parche, self._firma():
            revision.action_consultar_boletas()
        return llamadas

    def test_guarda_boletas_diarias_sin_duplicar_y_arma_el_libro(self):
        self._consumo_boletas()
        revision = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": "202610"})
        Fila = self.env["sii.boleta.diaria"]
        llamadas = self._consultar_boletas_diarias(
            revision, [("2026-10-01", 2, 3570), ("2026-10-02", 1, 1190)],
            [("2026-10-01", 2, 59500), ("2026-10-02", 0, 0)])
        self.assertFalse(revision.boletas_error)
        self.assertEqual([(c[0], c[1]["tipo_doc"], c[1]["periodo"]) for c in llamadas[1:]],
                         [("rcv/boletas_diarias", 39, "202610"), ("rcv/boletas_diarias", 48, "202610")])
        filas = Fila.search([("company_id", "=", self.company.id), ("periodo", "=", "202610")])
        self.assertEqual(len(filas), 4)
        fila48 = filas.filtered(lambda f: f.tipo_dte == 48 and f.fecha == date(2026, 10, 1))
        self.assertEqual((fila48.canal, fila48.total, fila48.primera_total), ("internet", 59500, 59500))
        dias = revision.boleta_linea_ids.filtered(lambda l: l.tipo_dte == 39).sorted("fecha")
        self.assertEqual([(d.cantidad_sii, d.cantidad_odoo, d.estado) for d in dias],
                         [(2, 2, "coincide"), (1, 0, "falta_odoo")])
        linea48 = revision.venta_book_id.boletas.filtered(lambda b: b.tipo_boleta.sii_code == 48)
        self.assertEqual((linea48.cantidad_boletas, linea48.monto_total), (2, 59500))
        linea39 = revision.venta_book_id.boletas.filtered(lambda b: b.tipo_boleta.sii_code == 39)
        self.assertEqual((linea39.cantidad_boletas, linea39.monto_total), (3, 4760))
        self._consultar_boletas_diarias(revision, [("2026-10-01", 2, 3570)],
                                        [("2026-10-01", 2, 40000), ("2026-10-02", 0, 0)])
        self.assertEqual(Fila.search([("company_id", "=", self.company.id), ("periodo", "=", "202610")]), filas)
        self.assertEqual((fila48.total, fila48.primera_total, fila48.rebaja_inferida), (40000, 59500, 19500))
        self.assertFalse(filas.filtered(lambda f: f.tipo_dte == 39 and f.fecha == date(2026, 10, 2)).informado)
        observacion = {o[0]: o[1] for o in revision._observaciones()}
        self.assertEqual(observacion["BOL"], "ok")

    def test_periodo_explicito_en_trafquin(self):
        revision = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": "202610"})
        llamadas, parche = self._con_trafquin({"rcv/boletas_diarias": [self._sin_detalle(48)]})
        with parche, self._firma():
            revision._trafquin("/api/rcv/boletas_diarias", {"periodo": "202501", "tipo_doc": 48})
        self.assertEqual(llamadas[0][1]["periodo"], "202501")

    def test_concilia_pagos_con_el_48_y_verifica_la_rebaja(self):
        self.company.modelo_emision_boleta = "voucher"
        diario = self.env["account.journal"].create({"name": "Flow 48", "code": "FL48", "type": "bank",
                                                     "company_id": self.company.id})
        diario.write({"sii_declara_ventas": True, "sii_canal_48": "internet"})
        cliente = self.env["res.partner"].create({"name": "Cliente 48"})
        Pago = self.env["account.payment"]

        def pagar(dia, monto):
            pago = Pago.create({"payment_type": "inbound", "partner_type": "customer", "partner_id": cliente.id,
                                "journal_id": diario.id, "amount": monto, "date": date(2026, 10, dia)})
            pago.action_post()
            return pago

        con_factura, sin_factura, otro_dia = pagar(1, 1190), pagar(2, 2000), pagar(5, 500)
        revision = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": "202610"})
        factura = self.env["account.move"].create({"move_type": "out_invoice", "partner_id": cliente.id})
        facturas = lambda p: [(factura, 1190, 190)] if p == con_factura else []
        with patch.object(type(Pago), "_facturas_48", facturas):
            self._consultar_boletas_diarias(revision, [], [("2026-10-01", 2, 3190), ("2026-10-05", 0, 0)])
            fila = revision.boleta48_ids.filtered(lambda f: f.fecha == date(2026, 10, 1))
            self.assertEqual(fila.pago_ids, con_factura | sin_factura)
            self.assertEqual(otro_dia.boleta_48_id.fecha, date(2026, 10, 5))
            fila.invalidate_recordset()
            self.assertEqual((fila.pagado_odoo, fila.con_factura, fila.rebaja_pendiente, fila.iva_en_riesgo,
                              fila.estado_48), (3190, 1190, 1190, 190, "rebaja_pendiente"))
            observacion = {o[0]: o for o in revision._observaciones()}
            self.assertEqual(observacion["V48"][1], "alerta")
            revision.action_proponer_rebajas()
            rebaja = fila.rebaja_ids
            self.assertEqual((rebaja.monto, rebaja.iva, rebaja.estado, rebaja.factura_ids), (1190, 190, "por_aplicar", factura))
            rebaja.action_marcar_aplicada()
            self.assertEqual((rebaja.estado, rebaja.total_sii_al_aplicar), ("aplicada", 3190))
            self._consultar_boletas_diarias(revision, [], [("2026-10-01", 2, 2000), ("2026-10-05", 0, 0)])
            fila.invalidate_recordset()
            self.assertEqual((rebaja.estado, fila.rebaja_pendiente, fila.diferencia_sii), ("verificada", 0, 0))
            self.assertEqual(fila.estado_48, "conciliado")
            self.assertEqual({o[0]: o[1] for o in revision._observaciones()}["V48"], "ok")
        sin_factura.action_desvincular_48()
        revision.action_conciliar_48()
        self.assertFalse(sin_factura.boleta_48_id)
        revision.invalidate_recordset()
        self.assertEqual(revision.v48_pagos_sin_boleta, 1)


@tagged("post_install", "-at_install")
class TestPlanDeCuentasCl(TransactionCase):

    def test_compania_nueva_con_el_plan_cl(self):
        from odoo.addons.l10n_cl_fe.models.template_cl import CUENTAS_F29
        Modulo = self.env["ir.module.module"]
        estados = dict(Modulo.search([]).mapped(lambda m: (m.name, m.state)))
        self.env["res.lang"]._activate_lang("es_CL")
        company = self.env["res.company"].create({"name": "Nueva CL", "country_id": self.env.ref("base.cl").id})
        self.env["account.chart.template"].try_loading("cl", company, install_demo=False)
        self.assertEqual(dict(Modulo.search([]).mapped(lambda m: (m.name, m.state))), estados)
        self.assertEqual({campo: company[campo].with_company(company).code for campo in CUENTAS_F29}, CUENTAS_F29)
        self.assertEqual(company.f29_diario_id.type, "general")
        por_pagar = company.f29_cuenta_por_pagar_id
        self.assertEqual((por_pagar.account_type, por_pagar.reconcile), ("liability_payable", True))
        self.assertEqual(company.f29_cuenta_debito_id.with_context(lang="es_CL").name, "IVA Débito Fiscal")
        self.assertEqual(company.f29_cuenta_postergado_id.with_context(lang="es_CL").name, "IVA Postergado por Pagar")
        company.f29_cuenta_recargo_id = False
        self.assertEqual(list(self.env["account.chart.template"]._cl_asignar_cuentas_f29(company)), ["f29_cuenta_recargo_id"])


@tagged("post_install", "-at_install")
class TestBoletasConVoucher(RevisionBase):

    def test_boleta_39_pagada_con_voucher_pide_nc(self):
        self.company.modelo_emision_boleta = "voucher"
        diario = self.env["account.journal"].create({"name": "Flow V39", "code": "FV39", "type": "bank",
                                                     "company_id": self.company.id})
        diario.write({"sii_declara_ventas": True, "sii_canal_48": "internet"})
        cliente = self.env["res.partner"].create({"name": "Cliente V39"})
        Pago = self.env["account.payment"]
        Move = self.env["account.move"]
        clase39 = self.env["sii.document_class"].search([("sii_code", "=", 39)], limit=1)
        boleta = Move.create({"move_type": "out_invoice", "partner_id": cliente.id})
        boleta.write({"document_class_id": clase39.id, "sii_document_number": 9001})
        factura = Move.create({"move_type": "out_invoice", "partner_id": cliente.id})

        def pagar(dia, monto, mes=10):
            pago = Pago.create({"payment_type": "inbound", "partner_type": "customer", "partner_id": cliente.id,
                                "journal_id": diario.id, "amount": monto, "date": date(2026, mes, dia)})
            pago.action_post()
            return pago

        pago_boleta, pago_factura, fuera = pagar(3, 500), pagar(4, 1190), pagar(1, 700, mes=11)
        aplicados = {pago_boleta: [(boleta, 500, 80)], pago_factura: [], fuera: [(boleta, 700, 112)]}

        def documentos(pago, codigos):
            return aplicados[pago] if codigos == (39, 41) else ([(factura, 1190, 190)] if pago == pago_factura else [])

        clase61 = self.env["sii.document_class"].search([("sii_code", "=", 61)], limit=1)
        self.env["ir.sequence"].create({"name": "NC 61 voucher", "sii_document_class_id": clase61.id,
                                        "company_id": self.company.id})
        revision = self.env["sii.revision.periodo"].create({"company_id": self.company.id, "periodo": "202610"})
        with patch.object(type(Pago), "_documentos_pagados_48", documentos):
            pendientes = revision._boletas_con_voucher_pendientes()
            self.assertEqual([(p["documento"], p["monto"], p["fecha"], p["canal"]) for p in pendientes],
                             [(boleta, 500, date(2026, 10, 3), "internet")])
            observacion = {o[0]: o for o in revision._observaciones()}
            self.assertEqual(observacion["V39"][1], "alerta")
            self.assertIn("emitir NC por 500 (1 boletas): 03/10 internet 500", observacion["V39"][2])
            accion = revision.action_nc_boletas_voucher()
            nc = self.env["account.move.boleta_voucher"].browse(accion["res_id"])
            self.assertEqual((nc.move_ids, nc.periodo, nc.medios_de_pago), (boleta, "2026-10", diario))
            self.assertFalse(revision._boletas_con_voucher_pendientes())
            self.assertEqual({o[0]: o[1] for o in revision._observaciones()}["V39"], "ok")
            nc.set_movimientos()
            self.assertEqual(nc.move_ids, boleta)
            self.company.modelo_emision_boleta = "boleta"
            revision.invalidate_recordset()
            self.assertNotIn("V39", {o[0] for o in revision._observaciones()})
