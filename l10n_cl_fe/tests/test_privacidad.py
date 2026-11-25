import http.client
import socket
import urllib.request
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import requests
import urllib3

from odoo.exceptions import UserError
from odoo.tests import Form
from odoo.tests.common import TransactionCase, tagged

# Puntos de salida a la red: las bibliotecas HTTP que usa el módulo (urllib3, requests), las de la
# biblioteca estándar y, debajo de todas, el socket. Si el código llega a la red por cualquiera de
# ellos, la llamada queda registrada y falla.
SALIDAS_RED = [
    (requests.Session, "request"),
    (requests.api, "request"),
    (requests, "request"),
    (requests.adapters.HTTPAdapter, "send"),
    (urllib3.PoolManager, "request"),
    (urllib3.PoolManager, "urlopen"),
    (urllib3.connectionpool.HTTPConnectionPool, "urlopen"),
    (urllib.request, "urlopen"),
    (http.client.HTTPConnection, "request"),
    (socket.socket, "connect"),
    (socket.socket, "connect_ex"),
    (socket, "create_connection"),
]


@contextmanager
def sin_red():
    """Hace fallar cualquier salida a la red y devuelve la lista de intentos.

    La lista importa porque parte del código heredado atrapa todas las excepciones
    (``except:``): la falla sola no basta, la prueba revisa además que la lista quede vacía."""
    intentos = []

    def bloquear(nombre):
        def _bloquear(*args, **kwargs):
            intentos.append((nombre, args[:2]))
            raise AssertionError("Llamada a la red no permitida: %s %r" % (nombre, args[:2]))
        return _bloquear

    with ExitStack() as pila:
        for objeto, atributo in SALIDAS_RED:
            nombre = "%s.%s" % (getattr(objeto, "__name__", objeto), atributo)
            pila.enter_context(patch.object(objeto, atributo, side_effect=bloquear(nombre)))
        yield intentos


@tagged("post_install", "-at_install")
class TestPrivacidad(TransactionCase):
    """Ningún dato de clientes sale a servicios externos (sre.cl, apicaf.cl, IAP de Odoo) sin que
    la compañía lo habilite explícitamente."""

    def test_sre_desactivado_por_defecto(self):
        self.assertFalse(self.env.company.sync_remote_partners)
        self.assertFalse(self.env["res.config.settings"].create({}).sync_remote_partners)
        nueva = self.env["res.company"].create({"name": "Compañía nueva privacidad"})
        self.assertFalse(nueva.sync_remote_partners)
        self.assertFalse(nueva.use_apicaf)

    def _crear_contacto_con_rut(self):
        with Form(self.env["res.partner"]) as contacto:
            contacto.name = "Cliente Privado SpA"
            contacto.document_number = "76.086.428-5"
            contacto.dte_email = "dte@cliente-privado.cl"
        socio = contacto.record
        socio.write({"street": "Calle 1", "document_number": "76086428-5"})
        self.env["res.partner"].create({"name": "76.086.428-5", "document_number": "76.086.428-5"})
        return socio

    def test_sin_llamadas_externas_al_crear_contacto(self):
        with sin_red() as intentos:
            socio = self._crear_contacto_con_rut()
            socio.fill_partner()
            # El cron diario de sincronización tampoco consulta ni envía nada.
            self.env["res.partner"]._check_need_update(100)
        self.assertEqual(intentos, [])
        self.assertEqual(socio.document_number, "76.086.428-5")

    def test_sre_habilitado_si_consulta(self):
        # Control positivo: con sre.cl habilitado, el mismo flujo sí llega a la red por un camino
        # que sin_red() intercepta (la prueba anterior no pasa por un camino sin parchear).
        self.env.company.sync_remote_partners = True
        with sin_red() as intentos:
            self._crear_contacto_con_rut()
        self.assertTrue(intentos)

    def test_apicaf_desactivado(self):
        company = self.env.company
        self.assertFalse(company.use_apicaf)
        Wizard = self.env["dte.caf.apicaf"]
        with sin_red() as intentos:
            with self.assertRaises(UserError):
                Form(Wizard, view="l10n_cl_fe.view_dte_caf_apicaf")
            with self.assertRaises(UserError):
                Wizard.create({"company_id": company.id})
            asistente = Wizard.new({"company_id": company.id, "operacion": "obtener", "id_peticion": 1})
            for metodo in ("conectar_api", "listar", "get_disp", "confirmar", "delist"):
                with self.subTest(metodo=metodo), self.assertRaises(UserError):
                    getattr(asistente, metodo)()
            secuencia = self.env["ir.sequence"].create({
                "name": "Secuencia apicaf", "company_id": company.id, "is_dte": True,
                "sii_document_class_id": self.env["sii.document_class"].search(
                    [("sii_code", "=", 33)], limit=1).id,
                "autoreponer_caf": True,
            })
            with self.assertRaises(UserError):
                secuencia.solicitar_caf()
            # El cron de reposición automática no intenta pedir folios a una compañía sin apicaf
            # (el cron hace commit/rollback: se neutralizan para la prueba).
            Secuencia = type(self.env["ir.sequence"])
            with patch.object(self.env.cr, "commit"), patch.object(self.env.cr, "rollback"), \
                    patch.object(Secuencia, "solicitar_caf", autospec=True) as solicitar:
                self.env["ir.sequence"].check_cafs()
            self.assertFalse(solicitar.called)
        self.assertEqual(intentos, [])

    def test_apicaf_habilitado_abre_el_asistente(self):
        self.env.company.write({"use_apicaf": True, "url_apicaf": False})
        with sin_red() as intentos:
            with Form(self.env["dte.caf.apicaf"], view="l10n_cl_fe.view_dte_caf_apicaf") as asistente:
                asistente.operacion = "reobtener"
                # Pasa el control de apicaf y se detiene en la firma (el admin no tiene una).
                self.assertIn("firma electrónica autorizada", asistente.message)
        self.assertEqual(intentos, [])

    def test_modulos_que_envian_datos_no_instalados(self):
        # partner_autocomplete (IAP de Odoo) se auto-instala con iap_mail y envía el nombre y el
        # RUT/VAT de los contactos a los servidores de Odoo al escribirlos. l10n_cl_fe no puede
        # impedirlo (ver ir_module_module.py): lo bloquea el despliegue (PORT_NOTES).
        Modulo = self.env["ir.module.module"]
        bloqueados = ["partner_autocomplete", "snailmail", "snailmail_account",
                      "crm_iap_enrich", "crm_iap_mine"]
        activos = ("installed", "to install", "to upgrade")
        instalados = Modulo.search([("name", "in", bloqueados), ("state", "in", activos)])
        self.assertEqual(instalados.mapped("name"), [],
                         "Módulos que envían datos de clientes a servicios externos sin opt-in")
        # Tras "Actualizar lista de aplicaciones" (lo que corre `odoo -u`) y una actualización de
        # l10n_cl_fe, sigue sin quedar marcado para instalar.
        Modulo.update_list()
        Modulo.search([("name", "=", "l10n_cl_fe")]).button_upgrade()
        self.assertFalse(Modulo.search([("name", "=", "partner_autocomplete"), ("state", "in", activos)]))
