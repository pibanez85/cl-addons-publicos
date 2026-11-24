import base64
import logging
from collections import OrderedDict
from datetime import datetime
from io import BytesIO

from odoo import fields, models
from odoo.exceptions import UserError
from odoo.tools.translate import _

from .bigint import BigInt


_logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    _logger.warning("no se ha cargado PIL")

try:
    from facturacion_electronica import facturacion_electronica as fe
except Exception as e:
    _logger.warning("no se ha cargado FE %s" % str(e))
    fe = None


SII_MAX_LINES_DTE = 60
SII_MAX_LINES_BOLETA = 1000
MAX_NMB_ITEM = 80
MAX_DSC_ITEM = 1000


class DteMixin(models.AbstractModel):
    _name = 'l10n_cl_fe.dte.mixin'
    _inherit = 'l10n_cl_fe.dte.envio.mixin'
    _description = 'Chilean DTE per-document helpers'

    document_class_id = fields.Many2one(
        "sii.document_class", string="Document Type",
        index=True, copy=True,
    )
    sii_code = fields.Integer(
        related="document_class_id.sii_code", string="SII Code",
        store=True, readonly=True, aggregator=False,
    )
    sii_document_number = BigInt(
        string="Document Number", copy=False, readonly=True,
        index=True, aggregator=False,
    )
    use_documents = fields.Boolean(string="Use Documents?",)
    canceled = fields.Boolean(string="Canceled?",)

    def queue(self, vals=None):
        docs = self.filtered('document_class_id')
        if not docs:
            return self.env['sii.cola_envio']
        groups = {}
        for doc in docs:
            key = (doc.company_id, doc.es_boleta())
            groups.setdefault(key, self.env[self._name])
            groups[key] |= doc
        colas = self.env['sii.cola_envio']
        for (company, es_boleta), group in groups.items():
            group_vals = dict(vals or {},
                              company_id=company.id,
                              es_boleta=es_boleta)
            colas |= group._enqueue_uniform(group_vals)
        return colas

    def _dte_ampara_traslado(self):
        return any(
            doc.document_class_id.sii_code == 52
            or (getattr(doc, 'transport_type', False) in ('1', '2', '3') and not doc.es_boleta())
            for doc in self
        )

    def _filter_for_manual_queue(self, cola_model, doc_field):
        skip_ids = set()
        for cola in cola_model.search([(doc_field, 'in', self.ids)]):
            cola_docs = getattr(cola, doc_field) & self
            if not cola_docs:
                continue
            blocked = (
                cola.manual
                or (cola.envio_id and cola.envio_id.sii_send_ident)
                or not cola._try_lock()
            )
            if not blocked:
                cola.invalidate_recordset(['processing'])
                blocked = cola.processing
            if blocked:
                skip_ids.update(cola_docs.ids)
            else:
                cola[doc_field] = [(3, doc_id) for doc_id in cola_docs.ids]
                if not cola.document_count:
                    cola.unlink()
        return self.filtered(lambda d: d.id not in skip_ids)

    def _enqueue_uniform(self, vals):
        if not self:
            return self.env['sii.cola_envio']
        cola_model = self.env['sii.cola_envio']
        doc_field = self._dte_cola_field
        docs_por_pack = self._get_dte_docs_por_pack(vals.get('es_boleta'))
        if not self._fill_envio_defaults(vals):
            return cola_model
        vals.pop('user_id', None)
        manual = vals.get('manual', False)
        if 'firma_id' not in vals and vals.get('company_id'):
            company = self.env['res.company'].browse(vals['company_id'])
            firma = self._resolve_cola_firma(company)
            if not firma:
                raise UserError(_(
                    "No hay firma electrónica disponible para la compañía '%s'. "
                    "Asigne una firma al usuario o al admin."
                ) % company.name)
            vals['firma_id'] = firma.id
        docs = self
        if manual:
            docs = self._filter_for_manual_queue(cola_model, doc_field)
            if not docs:
                return cola_model
        cola_propia = bool(self.env.context.get('dte_cola_propia')) and vals.get('tipo_trabajo') == 'pasivo'
        if manual or cola_propia or len(docs) >= docs_por_pack:
            colas = cola_model
            for i in range(0, len(docs), docs_por_pack):
                chunk = docs[i:i + docs_por_pack]
                chunk_vals = dict(vals, **{doc_field: [(6, 0, chunk.ids)]})
                cola = cola_model.create(chunk_vals)
                cola._log_user_docs(chunk)
                colas |= cola
            return colas
        domain = cola_model._get_queue_domain(vals)
        domain.append(('document_count', '<', docs_por_pack))
        existente = cola_model.search(domain, order='document_count desc', limit=1)
        if existente and existente.document_count + len(docs) <= docs_por_pack:
            if not existente._try_lock():
                existente = cola_model
            else:
                existente.invalidate_recordset(['processing'])
                if existente.processing:
                    existente = cola_model
            if existente:
                existente[doc_field] = [(4, doc_id) for doc_id in docs.ids]
                existente._log_user_docs(docs)
                return existente
        vals[doc_field] = [(6, 0, docs.ids)]
        cola = cola_model.create(vals)
        cola._log_user_docs(docs)
        return cola

    def _attachment_filename(self):
        self.ensure_one()
        prefix = self.document_class_id.doc_code_prefix or ''
        return f"{prefix}{self.sii_document_number or self.id}.xml".replace(' ', '_').replace('/', '_')

    def _dte_status_api_flag(self, record):
        return record.es_boleta()

    def _actecos_emisor(self):
        """Códigos de actividad económica del emisor."""
        return [a.code for a in self.company_id.company_activities_ids[:4]]

    def _acortar_str(self, texto, size=1):
        if not texto:
            return ""
        return texto[:size]

    def sii_header(self):
        """Construye la imagen del encabezado SII (RUT + tipo doc + folio + SII)."""
        W, H = (560, 255)
        img = Image.new("RGB", (W, H), color=(255, 255, 255))
        d = ImageDraw.Draw(img)
        w, h = (0, 0)
        for _i in range(10):
            d.rectangle(((w, h), (550 + w, 220 + h)), outline="black")
            w += 1
            h += 1
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
        d.text((50, 30), "R.U.T.: %s" % self.company_id.document_number, fill=(0, 0, 0), font=font)
        d.text((50, 90), self.document_class_id.name, fill=(0, 0, 0), font=font)
        d.text((220, 150), "N° %s" % self.sii_document_number, fill=(0, 0, 0), font=font)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
        if self.company_id.sii_regional_office_id:
            d.text(
                (200, 235),
                "SII %s" % self.company_id.sii_regional_office_id.name,
                fill=(0, 0, 0),
                font=font,
            )
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode()

    def es_boleta(self):
        return self.document_class_id.es_boleta()

    def es_factura(self):
        return self.document_class_id.es_factura()

    def es_factura_compra(self):
        return self.document_class_id.es_factura_compra()

    def es_guia(self):
        return self.document_class_id.es_guia()

    def es_nc(self):
        refs = self._get_referencias_dte()
        if not refs:
            return False
        move_type = getattr(self, 'move_type', None)
        if move_type and move_type not in ('out_refund', 'in_refund'):
            return False
        return self.document_class_id.es_nc()

    def es_nd(self):
        refs = self._get_referencias_dte()
        if not refs:
            return False
        move_type = getattr(self, 'move_type', None)
        if move_type and move_type not in ('out_invoice', 'in_invoice'):
            return False
        return self.document_class_id.es_nd()

    def es_nc_boleta(self):
        if not (self.es_nc() or self.es_nd()):
            return False
        return any(r.ref_id.es_boleta() for r in self._get_referencias_dte())

    def es_nc_factura_compra(self):
        if not (self.es_nc() or self.es_nd()):
            return False
        for r in self._get_referencias_dte():
            if r.ref_id.es_factura_compra():
                return True
            if r.ref_id.es_nc():
                obj = self.env['account.move'].search([
                    ('sii_document_number', '=', r.folio),
                    ('document_class_id', '=', r.ref_id.id),
                    ('use_documents', '=', True),
                ])
                return obj.es_nc_factura_compra()
        return False

    def _es_exento(self):
        if self.document_class_id.sii_code in [32, 34, 41, 110, 111, 112]:
            return True
        refs = self._get_referencias_dte()
        return bool(refs and refs[0].ref_id.sii_code in [32, 34, 41])

    def _dte_emisor_sucursal(self):
        """Sucursal del emisor: dict {name, sii_code, partner} o None."""
        return None

    def _emisor(self):
        """Emisor del DTE con todos los campos según tipo de documento."""
        self.ensure_one()
        company = self.company_id
        if not company.dte_resolution_date:
            raise UserError(_(
                "La empresa %s no tiene configurada la fecha de resolución SII"
            ) % company.name)
        Emisor = {
            "RUTEmisor": company.partner_id.rut(),
            "Modo": "produccion" if company.dte_service_provider == "SII" else "certificacion",
            "NroResol": company.dte_resolution_number,
            "FchResol": company.dte_resolution_date.strftime("%Y-%m-%d"),
            "ValorIva": 19,
        }
        nombre = self._acortar_str(company.partner_id.name or company.name, 100)
        giro = self._acortar_str(company.activity_description.name, 80) if company.activity_description else ""
        if self.es_boleta():
            Emisor["RznSocEmisor"] = nombre
            Emisor["GiroEmisor"] = giro
        else:
            Emisor["RznSoc"] = nombre
            Emisor["GiroEmis"] = giro
            if company.phone:
                Emisor["Telefono"] = self._acortar_str(company.phone, 20)
            if company.dte_email_id:
                Emisor["CorreoEmisor"] = company.dte_email_id.display_name
            Emisor["Actecos"] = self._actecos_emisor()
        dir_origen = company
        sucursal = self._dte_emisor_sucursal()
        if sucursal:
            Emisor["Sucursal"] = self._acortar_str(sucursal.get("name"), 20)
            Emisor["CdgSIISucur"] = self._acortar_str(sucursal.get("sii_code"), 9)
            dir_origen = sucursal["partner"]
        Emisor["DirOrigen"] = self._acortar_str(
            (dir_origen.street or "") + " " + (dir_origen.street2 or ""), 70
        )
        if not dir_origen.city_id:
            raise UserError(_("Debe ingresar la Comuna de compañía emisora"))
        Emisor["CmnaOrigen"] = dir_origen.city_id.name
        if not dir_origen.city:
            raise UserError(_("Debe ingresar la Ciudad de compañía emisora"))
        Emisor["CiudadOrigen"] = dir_origen.city
        return Emisor

    def _lineas_para_dte(self):
        """Devuelve lista estandarizada de líneas para construir el Detalle.

        Cada entrada es un dict con:
            product: product.product | False
            name: str (NmbItem)
            description: str | False (DscItem)
            qty: float (QtyItem)
            uom_id: uom.uom | False (UnmdItem; unidad de medida de la línea)
            price_unit: float (PrcItem en moneda base del doc)
            discount_pct: float (0..100; signo positivo=descuento, negativo=recargo)
            discount_amount: float (absoluto en moneda base)
            subtotal: float (MontoItem ya redondeado)
            tax_include: bool
            taxes: [{CodImp, TasaImp, price_include, mepco?}]
            cod_imp_adic: int | None
            ind_exe: int | str | None (1 / 2 / 6 ...)
            no_product: bool (NO_PRODUCT u operaciones sin producto)
            tpo_doc_liq: int | None
            needs_desglose: bool (la línea requiere desglose; el mixin reitera con
                                  force_desglose=True si aplica)
        """
        raise NotImplementedError(
            _("El modelo %s debe implementar _lineas_para_dte()") % self._name
        )

    def _dte_fch_emis(self):
        """Fecha de emisión del documento en YYYY-MM-DD."""
        raise NotImplementedError(
            _("El modelo %s debe implementar _dte_fch_emis()") % self._name
        )

    def _dte_tpo_impresion(self):
        """Tipo de impresión: 'T' para tickets/boletas, None para no incluir."""
        return None

    def _dte_forma_pago(self):
        """Forma de pago para IdDoc.FmaPago en documentos no-boleta."""
        return 1

    def _id_doc(self, resumen):
        """Bloque IdDoc del DTE."""
        self.ensure_one()
        iddoc = {
            'TipoDTE': self.document_class_id.sii_code,
            'Folio': self.get_folio(),
            'FchEmis': self._dte_fch_emis(),
        }
        transport_type = getattr(self, 'transport_type', False)
        if transport_type and transport_type != '0' and not self.es_boleta():
            iddoc['TipoDespacho'] = transport_type
        if self.es_boleta():
            iddoc['IndServicio'] = 3
            if not resumen.get('tax_include'):
                iddoc['IndMntNeto'] = 2
        elif self.es_guia():
            iddoc['IndTraslado'] = self.move_reason
            tpo = self._dte_tpo_impresion()
            if tpo:
                iddoc['TpoImpresion'] = tpo
        else:
            if resumen.get('tax_include') and not resumen.get('MntExe'):
                iddoc['MntBruto'] = 1
            iddoc['FmaPago'] = self._dte_forma_pago()
            tpo = self._dte_tpo_impresion()
            if tpo:
                iddoc['TpoImpresion'] = tpo
        return iddoc

    def _dte_partner_recep(self):
        """Partner receptor."""
        return self.partner_id.commercial_partner_id or self.partner_id

    def _dte_recep_name(self, partner):
        return partner.name or ""

    def _dte_recep_giro(self, partner):
        return partner.activity_description.name if partner.activity_description else False

    def _dte_recep_correo(self, partner):
        return partner.dte_email or False

    def _dte_recep_telefono(self, partner):
        return partner.phone or False

    def _dte_recep_validar_obligatorios(self, partner):
        """Indica si se exigen giro/dirección/comuna del receptor."""
        return not self.es_boleta() and not self.es_nc_boleta()

    def _receptor(self):
        self.ensure_one()
        partner = self._dte_partner_recep()
        if partner.document_number in [False, 0, "0"] and not self.es_boleta() and not self.es_nc_boleta():
            raise UserError(_("Debe Ingresar RUT Receptor"))
        Receptor = {
            'RUTRecep': partner.rut(),
            'RznSocRecep': self._acortar_str(self._dte_recep_name(partner), 100),
        }
        validar = self._dte_recep_validar_obligatorios(partner)
        if not self.es_boleta():
            giro = self._dte_recep_giro(partner)
            if giro:
                Receptor['GiroRecep'] = self._acortar_str(giro, 40)
            elif validar:
                raise UserError(_("Seleccione giro del partner"))
            correo = self._dte_recep_correo(partner)
            if correo:
                Receptor['CorreoRecep'] = correo
        telefono = self._dte_recep_telefono(partner)
        if telefono:
            Receptor['Contacto'] = self._acortar_str(telefono, 80)
        if partner.street:
            Receptor['DirRecep'] = self._acortar_str(
                partner.street + (' ' + partner.street2 if partner.street2 else ''), 70
            )
        elif validar:
            raise UserError(_("Debe Ingresar dirección del cliente"))
        if partner.city_id:
            Receptor['CmnaRecep'] = partner.city_id.name
        elif validar:
            raise UserError(_("Debe Ingresar Comuna del cliente"))
        if partner.city:
            Receptor['CiudadRecep'] = partner.city
        return Receptor

    def _totales_intermedio(self, resumen):
        """Totales intermedios derivados del resumen.

        Cada modelo override puede agregar retenciones, OtrosImp, MntBase,
        CredEC, etc. El wrapper _totales() se encarga de pasar este dict por
        _totales_normal() para producir el shape final SII."""
        self.ensure_one()
        totales = {
            'MntNeto': 0,
            'MntExe': resumen.get('MntExe', 0),
            'MntIVA': 0,
            'TasaIVA': resumen.get('TasaIVA', 0),
            'MntTotal': 0,
            'MontoNF': resumen.get('MontoNF', 0),
        }
        Neto = 0
        for l in resumen.get('Detalle', []):
            if l.get('IndExe'):
                continue
            Neto += l.get('MontoItem', 0)
        currency = self.currency_id
        if resumen.get('tax_include') and Neto and totales['TasaIVA']:
            gross = currency.round(Neto)
            totales['MntNeto'] = currency.round(gross / (1 + (totales['TasaIVA'] / 100.0)))
            totales['MntIVA'] = gross - totales['MntNeto']
        else:
            totales['MntNeto'] = currency.round(Neto)
            if self.amount_tax:
                totales['MntIVA'] = abs(currency.round(self.amount_tax))
            elif Neto and totales['TasaIVA']:
                totales['MntIVA'] = round(Neto * (totales['TasaIVA'] / 100.0))
        totales['MntTotal'] = totales['MntNeto'] + totales['MntExe'] + totales['MntIVA']
        return totales

    def _totales(self, resumen):
        """Construye Encabezado.Totales según tipo de documento."""
        currency = self.currency_id
        Totales = {'MntTotal': 0, 'MntNeto': 0, 'MntExe': 0, 'IVA': 0,
                   'TasaIVA': resumen.get('TasaIVA', 0), 'MontoNF': 0}
        if self.document_class_id.sii_code in [34, 41] and self.amount_tax > 0:
            raise UserError(_("NO pueden ir productos afectos en documentos exentos"))
        if self.amount_tax == 0 and self.document_class_id.es_boleta_afecta():
            raise UserError(_("Debe ir almenos un Producto Afecto"))
        MntExe = abs(resumen.get('MntExe', 0))
        Neto = 0
        for l in resumen.get('Detalle', []):
            if l.get('IndExe'):
                continue
            Neto += l.get('MontoItem', 0)
        Totales['TasaIVA'] = resumen.get('TasaIVA', 0)
        if resumen.get('tax_include') and Neto:
            Gross = currency.round(Neto)
            Totales['MntNeto'] = currency.round(Gross / (1 + (Totales['TasaIVA'] / 100.0)))
            Totales['IVA'] = Gross - Totales['MntNeto']
        else:
            Totales['MntNeto'] = currency.round(Neto)
            Totales['IVA'] = abs(currency.round(self.amount_tax))
        if MntExe > 0:
            Totales['MntExe'] = currency.round(MntExe)
        Totales['MntTotal'] = Totales['MntNeto'] + Totales['MntExe'] + Totales['IVA']
        MontoNF = currency.round(resumen.get('MontoNF', 0))
        if MontoNF:
            Totales['MontoNF'] = MontoNF
            Totales['TotalPeriodo'] = Totales['MntTotal'] + MontoNF
        Totales['VlrPagar'] = currency.round(Totales['MntTotal'] + MontoNF)
        return Totales

    def _totales_otra_moneda(self, currency_id, totales):
        """Construye OtraMoneda del Encabezado a partir del dict intermedio."""
        self.ensure_one()
        date = getattr(self, 'invoice_date', False) or fields.Date.context_today(self)
        doc_currency = self.currency_id

        def _conv(amount):
            if currency_id != doc_currency:
                return currency_id._convert(amount, doc_currency, self.company_id, date)
            return amount

        Totales = {
            "TpoMoneda": (currency_id.abreviatura or currency_id.name)[:15],
            "TpoCambio": round(currency_id.rate, 10),
        }
        if totales.get('MntNeto'):
            Totales["MntNetoOtrMnda"] = _conv(totales['MntNeto'])
        if totales.get('MntExe'):
            Totales["MntExeOtrMnda"] = _conv(totales['MntExe'])
        if totales.get('MntBase'):
            Totales["MntFaeCarneOtrMnda"] = _conv(totales['MntBase'])
        if totales.get('TasaIVA'):
            Totales["IVAOtrMnda"] = _conv(totales.get('MntIVA', 0))
        Totales["MntTotOtrMnda"] = _conv(totales.get('MntTotal', 0))
        return Totales

    def _totales_normal(self, currency_id, totales):
        """Convierte el dict de totales intermedio al shape final del SII.

        El dict puede tener: MntNeto, MntExe, MntBase, TasaIVA, MntIVA, CredEC,
        MntRet, MntTotal, MontoNF, VlrPagar, ValComNeto, ValComExe, ValComIVA."""
        self.ensure_one()
        Totales = {}
        date = getattr(self, 'invoice_date', False) or fields.Date.context_today(self)
        doc_currency = self.currency_id

        def _conv(amount):
            if currency_id != doc_currency:
                return currency_id._convert(amount, doc_currency, self.company_id, date)
            return amount

        if totales.get('MntNeto'):
            Totales["MntNeto"] = currency_id.round(_conv(totales['MntNeto']))
        if totales.get('MntExe'):
            Totales["MntExe"] = currency_id.round(_conv(totales['MntExe']))
        if totales.get('MntBase'):
            Totales["MntBase"] = currency_id.round(totales['MntBase'])
        if totales.get('TasaIVA'):
            Totales["TasaIVA"] = totales['TasaIVA']
            Totales["IVA"] = currency_id.round(_conv(totales.get('MntIVA', 0)))
        if totales.get('CredEC'):
            Totales["CredEC"] = currency_id.round(totales['CredEC'])
        if totales.get('MntRet'):
            Totales["MntRet"] = currency_id.round(totales['MntRet'])
        for k in ('ValComNeto', 'ValComExe', 'ValComIVA'):
            if totales.get(k):
                Totales[k] = currency_id.round(_conv(totales[k]))
        Totales["MntTotal"] = currency_id.round(_conv(totales.get('MntTotal', 0)))
        if totales.get('MontoNF'):
            Totales['MontoNF'] = currency_id.round(totales['MontoNF'])
            for k in ('TotalPeriodo', 'MontoPeriodo'):
                if totales.get(k):
                    Totales[k] = currency_id.round(_conv(totales[k]))
        if totales.get('VlrPagar'):
            Totales["VlrPagar"] = currency_id.round(_conv(totales['VlrPagar']))
        return Totales

    def _encabezado_incluye_emisor(self):
        return True

    def _encabezado_transporte(self):
        if getattr(self, 'transport_type', False) in ('2', '3') and not self.es_boleta():
            return self._transporte()
        return None

    def _transporte(self):
        """Construye el bloque Transporte. Modelos con guía/exportación lo
        usan vía _encabezado_transporte()."""
        Transporte = {}
        patente = getattr(self, 'patente', False)
        vehicle = getattr(self, 'vehicle', False)
        if patente:
            Transporte['Patente'] = patente[:8]
        elif vehicle:
            Transporte['Patente'] = vehicle.license_plate or ''
        patente_carro = getattr(self, 'patente_carro', False)
        if patente_carro:
            Transporte['PatenteCarro'] = patente_carro[:8]
        transportista = getattr(self, 'transportista_id', False)
        if getattr(self, 'transportista_por_confirmar', False):
            Transporte['RUTTrans'] = False
        elif transportista:
            if not transportista.document_number:
                raise UserError(_(
                    "Debe especificar el RUT del transportista, en su ficha de partner"
                ))
            Transporte['RUTTrans'] = transportista.rut()
        chofer = getattr(self, 'chofer', False)
        if chofer:
            if not chofer.document_number:
                raise UserError(_("El chofer %s no tiene RUT. Cárguelo en su contacto") % chofer.name)
            Transporte['Chofer'] = {
                'RUTChofer': chofer.rut(),
                'NombreChofer': chofer.name[:30],
            }
        partner = getattr(self, 'partner_shipping_id', False) or self.partner_id or self.company_id.partner_id
        Transporte['DirDest'] = (partner.street or '') + ' ' + (partner.street2 or '')
        Transporte['CmnaDest'] = partner.city_id.name or ''
        Transporte['CiudadDest'] = partner.city or ''
        salida = getattr(self, 'fecha_salida', False)
        if salida:
            salida = fields.Datetime.context_timestamp(self.with_context(tz='America/Santiago'), salida)
            Transporte['FchSalida'] = salida.strftime('%Y-%m-%d')
            Transporte['HraSalida'] = salida.strftime('%H:%M:%S')
        llegada = getattr(self, 'fecha_llegada', False)
        if llegada:
            Transporte['FchLlegada'] = llegada.strftime('%Y-%m-%d')
        return Transporte

    def _encabezado_otra_moneda(self, resumen):
        target = self.currency_target()
        if not target:
            return None
        return self._totales_otra_moneda(target, self._totales_intermedio(resumen))

    def _encabezado_para_dte(self, resumen):
        self.ensure_one()
        enc = {
            'IdDoc': self._id_doc(resumen),
            'Receptor': self._receptor(),
            'Totales': self._totales(resumen),
        }
        if self._encabezado_incluye_emisor():
            enc['Emisor'] = self._emisor()
        transporte = self._encabezado_transporte()
        if transporte:
            enc['Transporte'] = transporte
        otra = self._encabezado_otra_moneda(resumen)
        if otra:
            enc['OtraMoneda'] = otra
        return enc

    def _dte_para_estado(self):
        self.ensure_one()
        resumen = self._detalle_sii()
        return {
            'Encabezado': {
                'IdDoc': self._id_doc(resumen),
                'Receptor': {'RUTRecep': self._dte_partner_recep().rut()},
                'Totales': self._totales(resumen),
            },
        }

    def _get_referencias_dte(self):
        raise NotImplementedError(
            _("El modelo %s debe implementar _get_referencias_dte()") % self._name
        )

    def currency_base(self):
        return self.env.ref("base.CLP")

    def currency_target(self):
        if self.currency_id and self.currency_id != self.currency_base():
            return self.currency_id
        return False

    def _gdr_lines(self):
        """Lista estandarizada de descuentos/recargos globales.

        Cada entrada es un dict con:
            type: 'D' (descuento) | 'R' (recargo)
            valor: float
            tipo_valor: '%' | '$'
            glosa: str | False
            ind_exe: int | None
        """
        return []

    def _gdr(self):
        """Construye DescuentoRecargoGlobal SII a partir de _gdr_lines()."""
        result = []
        currency_base = self.currency_base()
        currency_target = self.currency_target()
        for idx, dr in enumerate(self._gdr_lines(), start=1):
            dr_line = {
                "NroLinDR": idx,
                "TpoMov": dr['type'],
                "TpoValor": dr.get('tipo_valor') or '%',
                "ValorDR": currency_base.round(dr['valor']),
            }
            if dr.get('glosa'):
                dr_line["GlosaDR"] = dr['glosa']
            if currency_target and currency_base != currency_target:
                date = getattr(self, 'invoice_date', False) or fields.Date.context_today(self)
                dr_line["ValorDROtrMnda"] = currency_base._convert(
                    dr['valor'], currency_target, self.company_id, date,
                )
            if dr.get('ind_exe'):
                dr_line["IndExeDR"] = dr['ind_exe']
            result.append(dr_line)
        return result

    def _dte_extras(self, dte):
        return dte

    def _dte_anulado(self):
        return None

    def _dte_set_pruebas_ref(self, lin_ref):
        if not self.env.context.get("set_pruebas", False):
            return None
        razon = "CASO"
        n_atencion = self.env.context.get('n_atencion')
        if n_atencion:
            razon += " " + n_atencion
        if self.sii_batch_number:
            razon += "-" + str(self.sii_batch_number)
        return {
            "NroLinRef": lin_ref,
            "TpoDocRef": "SET",
            "FolioRef": self.get_folio(),
            "FchRef": datetime.strftime(datetime.now(), "%Y-%m-%d"),
            "RazonRef": razon,
        }

    def _referencia_a_dict(self, ref, lin_ref):
        ref_line = {"NroLinRef": lin_ref}
        if not self.es_boleta():
            if getattr(ref, "ref_id", False):
                tpo = ref.ref_id
                ref_line["TpoDocRef"] = (
                    self._acortar_str(tpo.doc_code_prefix, 3) if getattr(tpo, "use_prefix", False) else tpo.sii_code
                )
                ref_line["FolioRef"] = ref.folio
                if getattr(ref, "rut_otro", False):
                    ref_line["RUTOtr"] = ref.rut_otro
                if getattr(ref, "ind_global", False):
                    ref_line["IndGlobal"] = 1
            fch = (
                getattr(ref, "fecha_documento", False)
                or getattr(ref, "date", False)
                or fields.Date.context_today(self)
            )
            if hasattr(fch, "strftime"):
                ref_line["FchRef"] = fch.strftime("%Y-%m-%d")
            else:
                ref_line["FchRef"] = str(fch)
        cod_ref = getattr(ref, "cod_ref", False)
        if cod_ref not in ["", "none", False, None]:
            ref_line["CodRef"] = cod_ref
        razon = getattr(ref, "motivo", False)
        if razon:
            ref_line["RazonRef"] = razon
        return ref_line

    def _referencias_dte(self):
        ref_lines = []
        lin_ref = 1
        set_ref = self._dte_set_pruebas_ref(lin_ref)
        if set_ref:
            ref_lines.append(set_ref)
            lin_ref += 1
        for ref in self._get_referencias_dte():
            ref_lines.append(self._referencia_a_dict(ref, lin_ref))
            lin_ref += 1
        return ref_lines

    def _detalle_sii_uom(self, uom):
        if not uom:
            return None
        es_export = self.document_class_id.es_exportacion()
        uom_name = uom[:1].with_context(exportacion=es_export).display_name
        return uom_name[:4] if uom_name else None

    def _detalle_sii_cdg_item(self, product):
        if not product:
            return []
        cdg = []
        if product.default_code:
            cdg.append({"TpoCodigo": "INT1", "VlrCodigo": product.default_code})
        if product.barcode:
            cdg.append({"TpoCodigo": "EAN13", "VlrCodigo": product.barcode})
        return cdg

    def _detalle_sii_otrmnda(self, line, currency_base, currency_target):
        if not currency_target:
            return None
        date = getattr(self, 'invoice_date', False) or fields.Date.context_today(self)
        otrmnda = {
            "Moneda": currency_target.name[:3],
            "FctConv": round(currency_target.rate, 4),
        }
        price_unit = line.get('price_unit') or 0
        if price_unit:
            otrmnda["PrcOtrMon"] = round(
                currency_base._convert(price_unit, currency_target,
                                       self.company_id, date, round=False),
                6,
            )
        return otrmnda

    def _detalle_sii(self):
        """Construye la sección Detalle SII a partir de _lineas_para_dte().

        Maneja desglose, OtrMnda (si currency_target() está activo) y
        acumula MntExe / MontoNF para totales."""
        self.ensure_one()
        lineas = self._lineas_para_dte()
        detalle = []
        MntExe = 0
        MontoNF = 0
        TasaIVA = 0
        tax_include = False
        tax_excluded = False
        currency_base = self.currency_base()
        currency_target = self.currency_target()
        dte_monto_0 = False
        for r in self._get_referencias_dte():
            if r.cod_ref == '2':
                dte_monto_0 = True
                break
            if r.cod_ref == '1':
                ref_doc = r._buscar_documento_referenciado()
                if ref_doc and any(rr.cod_ref == '2' for rr in ref_doc._get_referencias_dte()):
                    dte_monto_0 = True
                    break
        for idx, line in enumerate(lineas, start=1):
            if line.get('needs_desglose') and not self.env.context.get('force_desglose') and detalle:
                return self.with_context(force_desglose=True)._detalle_sii()
            item = {"NroLinDet": line.get('nro_lin_det', idx)}
            product = line.get('product') or False
            line_no_product = bool(line.get('no_product'))
            is_product = not line_no_product
            is_no_product_code = bool(product) and product.default_code == 'NO_PRODUCT'
            if is_no_product_code and not dte_monto_0:
                raise UserError(_(
                    "Documento %s (id=%s): el producto NO_PRODUCT solo se permite en notas "
                    "de crédito/débito que corrigen texto (referencia código "
                    "'2 - Corrige texto Documento Referencia')."
                ) % (self.display_name, self.id))
            if dte_monto_0 and product and not is_no_product_code:
                raise UserError(_(
                    "Documento %s (id=%s): las notas que corrigen texto (referencia código "
                    "'2 - Corrige texto Documento Referencia') solo pueden llevar el "
                    "producto NO_PRODUCT, no productos reales."
                ) % (self.display_name, self.id))
            cdg = self._detalle_sii_cdg_item(product)
            if cdg:
                item["CdgItem"] = cdg
            taxes = line.get('taxes') or []
            item["Impuesto"] = taxes
            if not taxes and not line.get('ind_exe') and not line_no_product:
                raise UserError(_(
                    "La línea '%s' del documento %s (id=%s) no tiene impuesto "
                    "seleccionado. Debe asignar un impuesto: afecto si corresponde "
                    "o impuesto exento si la línea es exenta."
                ) % (line.get('name') or '', self.display_name, self.id))
            if taxes and not line.get('ind_exe'):
                if line.get('tax_include'):
                    tax_include = True
                else:
                    tax_excluded = True
                if tax_include and tax_excluded:
                    raise UserError(_(
                        "No se puede hacer timbraje mixto en el documento %s (id=%s): "
                        "todos los impuestos deben ser del mismo tipo "
                        "(precio incluído o precio sin incluir)."
                    ) % (self.display_name, self.id))
            for t in taxes:
                if t.get('TasaImp') and not TasaIVA:
                    TasaIVA = t['TasaImp']
            if line.get('cod_imp_adic'):
                item["CodImpAdic"] = line['cod_imp_adic']
            if line.get('tpo_doc_liq'):
                item["TpoDocLiq"] = line['tpo_doc_liq']
            ind_exe = line.get('ind_exe')
            if ind_exe:
                item["IndExe"] = ind_exe
                ind_exe_str = str(ind_exe)
                mnt = line.get('subtotal_exento', line.get('subtotal') or 0)
                if ind_exe_str == "2":
                    MontoNF += mnt
                elif ind_exe_str == "6":
                    MontoNF -= mnt
                else:
                    MntExe += mnt
            item["NmbItem"] = line.get('name') or ''
            if line.get('description'):
                item["DscItem"] = line['description']
            qty = round(line.get('qty') or 0, 4)
            if is_product:
                if qty == 0:
                    qty = 1
                elif qty < 0:
                    raise UserError(_("Cantidad no puede ser menor que 0"))
                price_unit = line.get('price_unit') or 0
                if product and price_unit:
                    item["PrcItem"] = round(price_unit, 6)
                otrmnda = self._detalle_sii_otrmnda(line, currency_base, currency_target)
                if otrmnda:
                    item["OtrMnda"] = otrmnda
            uom = self._detalle_sii_uom(line.get('uom_id'))
            if uom:
                item["UnmdItem"] = uom
            MontoItem = line.get('subtotal') or 0
            disc_pct = line.get('discount_pct') or 0
            disc_amt = line.get('discount_amount') or 0
            if is_product and disc_pct > 0:
                item["DescuentoPct"] = disc_pct
                item["DescuentoMonto"] = disc_amt
                if currency_target:
                    date = getattr(self, 'invoice_date', False) or fields.Date.context_today(self)
                    item["DescuentoMonto"] = currency_base._convert(
                        disc_amt, currency_target, self.company_id, date,
                    )
                    item.setdefault("OtrMnda", {})["DctoOtrMnda"] = disc_amt
            elif is_product and disc_pct < 0:
                item["RecargoPct"] = -disc_pct
                item["RecargoMonto"] = disc_amt
                if currency_target:
                    date = getattr(self, 'invoice_date', False) or fields.Date.context_today(self)
                    item.setdefault("OtrMnda", {})["RecargoOtrMnda"] = round(
                        currency_base._convert(disc_amt, currency_target,
                                               self.company_id, date),
                        4,
                    )
            if is_product and currency_target:
                date = getattr(self, 'invoice_date', False) or fields.Date.context_today(self)
                item.setdefault("OtrMnda", {})["MontoItemOtrMnda"] = currency_base._convert(
                    MontoItem, currency_target, self.company_id, date,
                )
            item["QtyItem"] = qty
            item["MontoItem"] = MontoItem
            if MontoItem < 0 and not self.document_class_id.es_liquidacion():
                raise UserError(_("No pueden ir valores negativos en las líneas de detalle"))
            if item.get("PrcItem", 1) == 0:
                del item["PrcItem"]
            detalle.append(item)
        detalle = self._detalle_sii_post_process(detalle)
        return {
            "Detalle": detalle,
            "MntExe": MntExe,
            "MontoNF": MontoNF,
            "tax_include": tax_include,
            "TasaIVA": TasaIVA,
        }

    def _detalle_sii_post_process(self, detalle):
        """Aplica autopack si la compañía lo configura y el detalle excede el
        límite SII para el tipo de documento."""
        if self.company_id.dte_lines_action == 'autopack':
            sii_limit = SII_MAX_LINES_DTE if not self.es_boleta() else SII_MAX_LINES_BOLETA
            if len(detalle) > sii_limit:
                detalle = self._autopack_detalle(detalle)
        return detalle

    def _autopack_detalle(self, detalle):
        groups = OrderedDict()
        for line in detalle:
            sig = self._autopack_signature(line)
            groups.setdefault(sig, []).append(line)
        packed = []
        for lines in groups.values():
            packed.extend(self._autopack_group(lines, len(packed) + 1))
        for idx, l in enumerate(packed, start=1):
            l['NroLinDet'] = idx
        return packed

    def _autopack_signature(self, line):
        impuestos = line.get('Impuesto') or []
        parts = []
        if isinstance(impuestos, list):
            for t in impuestos:
                parts.append((t.get('CodImp'), t.get('TasaImp')))
        signature_parts = tuple(sorted(
            parts,
            key=lambda x: (str(x[0] or ''), str(x[1] or '')),
        ))
        return (
            signature_parts,
            line.get('IndExe') or '',
            line.get('CodImpAdic') or '',
        )

    def _autopack_tax_name(self, line):
        impuestos = line.get('Impuesto') or []
        names = []
        AccountTax = self.env['account.tax']
        if isinstance(impuestos, list):
            for t in impuestos:
                tax = AccountTax.search([
                    ('sii_code', '=', t.get('CodImp')),
                    ('company_id', '=', self.company_id.id),
                ], limit=1)
                if tax:
                    names.append(tax.description or tax.name)
                else:
                    names.append('CodImp %s' % t.get('CodImp'))
        if line.get('IndExe'):
            names.append('Exento')
        if line.get('CodImpAdic'):
            names.append('Adic %s' % line['CodImpAdic'])
        return ' + '.join(names) or 'Sin Impuesto'

    def _autopack_group(self, lines, start_nro):
        tax_label = self._autopack_tax_name(lines[0])
        nmb = ('Productos Agrupados %s' % tax_label)[:MAX_NMB_ITEM]
        sample = lines[0]
        chunks = []
        current_items = []
        current_monto = 0.0
        for line in lines:
            fragment = '%s %s x %s' % (
                str(line.get('NmbItem') or ''),
                line.get('QtyItem') or 0,
                line.get('PrcItem') or 0,
            )
            candidate = ', '.join(current_items + [fragment])
            if len(candidate) > MAX_DSC_ITEM and current_items:
                chunks.append((current_items, current_monto))
                current_items = []
                current_monto = 0.0
            current_items.append(fragment)
            current_monto += line.get('MontoItem') or 0
        if current_items:
            chunks.append((current_items, current_monto))
        out = []
        for idx, (items, monto) in enumerate(chunks):
            packed = {
                'NroLinDet': start_nro + idx,
                'NmbItem': nmb,
                'DscItem': (', '.join(items))[:MAX_DSC_ITEM],
                'QtyItem': 1,
                'PrcItem': monto,
                'MontoItem': monto,
            }
            if sample.get('Impuesto'):
                packed['Impuesto'] = sample['Impuesto']
            if sample.get('CodImpAdic'):
                packed['CodImpAdic'] = sample['CodImpAdic']
            if sample.get('IndExe'):
                packed['IndExe'] = sample['IndExe']
            out.append(packed)
        return out

    def _exchange_subject(self):
        return "XML de Intercambio DTE: %s" % (
            getattr(self, 'name', False) or self.display_name or self.id
        )

    def _exchange_body(self):
        return self._exchange_subject()

    def send_exchange(self):
        for rec in self:
            commercial = rec.partner_id.commercial_partner_id or rec.partner_id
            if not commercial or commercial.es_mipyme:
                continue
            try:
                att = rec._create_attachment()
            except UserError as e:
                _logger.warning("send_exchange %s: %s", rec.name, e)
                continue
            dte_email_id = rec.company_id.dte_email_id or rec.env.company.dte_email_id
            if not dte_email_id:
                _logger.warning("send_exchange %s: compañía sin dte_email_id", rec.name)
                continue
            dte_receptors = commercial.child_ids + commercial
            email_to = (commercial.dte_email + ',') if commercial.dte_email else ''
            for dte_email in dte_receptors:
                if not dte_email.send_dte or not dte_email.email:
                    continue
                if dte_email.email in ('facturacionmipyme2@sii.cl', 'facturacionmipyme@sii.cl'):
                    resp = rec.env['sii.respuesta.cliente'].sudo().search([('exchange_id', '=', att.id)])
                    resp.recep_envio = '0'
                    continue
                if dte_email.email not in email_to:
                    email_to += dte_email.email + ','
            if not email_to:
                continue
            rec.env['mail.mail'].sudo().create({
                'res_id': rec.id,
                'email_from': dte_email_id.display_name,
                'email_to': email_to.rstrip(','),
                'auto_delete': False,
                'model': rec._name,
                'body': rec._exchange_body(),
                'subject': rec._exchange_subject(),
                'attachment_ids': [(6, 0, att.ids)],
            }).send()

    def get_folio(self):
        return int(self.sii_document_number)

    def _validaciones_uso_dte(self):
        """Validaciones universales antes de timbrar/enviar el DTE."""
        if not self.document_class_id:
            raise UserError(_("NO tiene seleccionado tipo de documento"))
        if self.es_nc() or self.es_nd():
            refs = self._get_referencias_dte()
            if not refs:
                raise UserError(_(
                    "Las Notas deben llevar por obligación una referencia al documento que están afectando"
                ))
            for ref in refs:
                ref_doc = ref._buscar_documento_referenciado()
                if ref_doc and ref_doc.sii_result == 'Rechazado':
                    raise UserError(_(
                        "No se puede emitir una NC/ND sobre el documento %s — "
                        "está Rechazado por el SII."
                    ) % ref_doc.display_name)
        if not self.env.user.get_digital_signature(self.company_id):
            raise UserError(_(
                "Usuario no autorizado a usar firma electrónica para esta compañia. "
                "Por favor solicitar autorización en la ficha de compañia del documento "
                "por alguien con los permisos suficientes de administrador"
            ))
        if not self.env.ref("base.lang_es_CL").active:
            raise UserError(_("Lang es_CL must be enabled"))
        if not self.env.ref("base.CLP").active:
            raise UserError(_("Currency CLP must be enabled"))

    def _dte_clean_envio_if_needed(self):
        if self.sii_result != 'Rechazado' or not self.sii_xml_request:
            return
        if self.sii_xml_request.state in ('Enviado', 'EnProceso', 'Aceptado'):
            self.sii_xml_request = False
        elif self._dte_send_xml_orphan():
            self.sii_xml_request.unlink()
        else:
            self.sii_xml_request = False

    def _dte_repair_for_send(self):
        if self.sii_result == 'Rechazado':
            self.sii_xml_dte = False
            self._timbrar()

    def _get_caf(self, folio):
        """CAF correspondiente al folio."""
        return self.env['dte.caf'].search([
            ('start_nm', '<=', folio),
            ('final_nm', '>=', folio),
            ('document_class_id', '=', self.document_class_id.id),
            '|', ('company_id', '=', False),
            ('company_id', '=', self.company_id.id),
        ], limit=1)

    def _caf_file(self, caf, folio):
        """Contenido del archivo CAF para fe.timbrar."""
        return caf.caf_file

    def _validaciones_caf(self, caf):
        fecha_timbre = fields.Date.context_today(self)
        issued_date = caf.issued_date if hasattr(caf, 'issued_date') else caf['issued_date']
        if fecha_timbre < issued_date:
            raise UserError(_(
                "La fecha del timbraje no puede ser menor a la fecha de emisión del CAF"
            ))

    def _timbrar_apply_result(self, result):
        self.write({
            'sii_xml_dte': result[0]['sii_xml_dte'],
            'sii_barcode': result[0]['sii_barcode'],
        })

    def _timbrar(self):
        """Timbra el documento llamando a fe.timbrar."""
        self.ensure_one()
        return self._timbrar_batch()

    def _timbrar_batch(self):
        """Timbra todos los registros del recordset en una sola llamada a fe.timbrar."""
        if not self:
            return
        datos = self[0]._get_datos_empresa()
        grupos = {}
        for doc in self:
            folio = doc.get_folio()
            caf = doc._get_caf(folio)
            doc._validaciones_caf(caf)
            tipo = doc.document_class_id.sii_code
            bucket = grupos.setdefault(tipo, {'caf_files': [], 'documentos': [], 'records': []})
            caf_content = doc._caf_file(caf, folio)
            if caf_content not in bucket['caf_files']:
                bucket['caf_files'].append(caf_content)
            bucket['documentos'].append(doc._dte())
            bucket['records'].append(doc)
        datos['Documento'] = [
            {
                'TipoDTE': tipo,
                'caf_file': bucket['caf_files'],
                'documentos': bucket['documentos'],
            }
            for tipo, bucket in grupos.items()
        ]
        result = fe.timbrar(datos)
        docs_by_key = {}
        for tipo, bucket in grupos.items():
            for rec in bucket['records']:
                docs_by_key[(int(tipo), rec.get_folio())] = rec
        for resp in result:
            key = (int(resp['TipoDTE']), int(resp['Folio']))
            rec = docs_by_key.get(key)
            if resp.get('error'):
                raise UserError(_(
                    "Error al timbrar %s N° %s: %s"
                ) % (
                    rec.document_class_id.name if rec else resp['TipoDTE'],
                    resp['Folio'],
                    resp['error'],
                ))
            if not rec and len(self) == 1 and len(result) == 1:
                rec = self
            if rec:
                rec._timbrar_apply_result([resp])
            else:
                _logger.warning(
                    "Timbrar: respuesta sin match (key=%s, resp=%s); doc no actualizado",
                    key, resp,
                )

    def _dte(self):
        """Construye el dict DTE."""
        self.ensure_one()
        dte = {}
        anulado = self._dte_anulado()
        if anulado is not None:
            dte["Anulado"] = anulado
        resumen = self._detalle_sii()
        dte["Encabezado"] = self._encabezado_para_dte(resumen)
        dte["Detalle"] = resumen["Detalle"]
        gdr = self._gdr()
        if gdr:
            dte["DscRcgGlobal"] = gdr
        dte["Referencia"] = self._referencias_dte()
        return self._dte_extras(dte)
