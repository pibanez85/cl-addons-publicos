# Port de `l10n_cl_fe` 17.0 → Odoo 20

Estado tras la Task 2 (plan 1): el módulo **instala** en Odoo 20 sin errores y pasan las 3 pruebas
de `tests/test_instalacion.py`. El resto del port (Python en la Task 3, vistas y JS en la Task 4)
sigue pendiente. Aquí se anota lo hecho y lo visto.

## Hecho en la Task 2 (solo lo que bloqueaba la instalación)

- Manifiesto: versión `20.0.0.62.0`; sin `partner_autocomplete`; `external_dependencies` sin
  módulos de la biblioteca estándar (`base64`, `ast`, `io`) y `PIL` → `pillow`.
- `__init__.py`: se borró `_auto_install_l10n` (código muerto que importaba `SYSCOHADA_LIST`,
  que ya no existe en `odoo.addons.account`). `_set_default_configs` usa `set_int`/`set_bool`.
- `res.partner`: se quitaron `autocomplete_by_name`, `autocomplete_by_vat`, `enrich_by_duns`,
  `_autocomplete_rut`, `_sugerencia_autocomplete_rut` (dependían de `partner_autocomplete` e IAP).
  `_prepare_display_address(without_company)` → `without_name`.
- Controladores: `content_disposition` desde `odoo.http.stream`; `web.controllers.main` →
  `web.controllers.report`; `report.report_file` → `report.report_name`; `type='json'` → `'jsonrpc'`.
- `@api.returns` (eliminado) en `account.move.copy`; `@depends` `payment_id` → `origin_payment_id`
  en `account.move._compute_amount`; se borró el override no-op de `sale.order.line._compute_amount`
  (dependía de `tax_id`, ahora `tax_ids`).
- `group_operator=` → `aggregator=`; `import fitz` → `import pymupdf as fitz`.
- `mail.message.dte.document.dte_xml`: related a `xml_id.raw` (Odoo 20 eliminó `ir.attachment.datas`).
- Informes SQL (`l10n_cl.sale.document.report`, `l10n_cl.voucher.factura.report`):
  `_table_query` → `_table_sql` (subconsulta entre paréntesis).
- Seguridad: `ir.model.access.csv` → `security/ir.access.csv` (modelo `ir.access` de Odoo 20,
  `operation` en `crud`). Los ACL sin grupo de 17.0 (que en 17 valían también para portal y
  público) pasan a `base.group_user`; solo los catálogos de referencia quedan para
  `base.group_everyone` y en lectura (`sii.document_class`, `sii.document_type`,
  `sii.document_letter`, `sii.responsability`, `sii.regional.offices`,
  `res.country.state.provincia`, `sii.activity.description`). `sii.firma` (`.p12`, clave, llave
  privada) ya no es legible por `base.group_user`: solo `account.group_account_manager` y
  `base.group_system`, también con `groups=` en `file_content`, `password`, `cert` y `priv_key`;
  la firma de documentos usa el registro con sudo de `res.users.get_digital_signature`, y el
  asistente de apicaf lee la llave con sudo solo si el usuario está autorizado en la firma.
  Los `ir.rule` globales pasan a registros `ir.access` sin grupo (restricciones).
  Pruebas: `tests/test_accesos.py`.
- Datos: `uom.uom` sin categorías (`relative_uom_id`/`relative_factor`); `res.bank.csv` desactivado
  (Odoo 20 eliminó `res.bank`); `ir.cron.numbercall` eliminado; `account.report.filter_fiscal_position`
  → `allow_foreign_vat`; `target=inline` eliminado de la acción de ajustes.
- Etiquetas de impuestos: Odoo 18+ usa etiquetas sin signo. Se quitó el `+`/`-` de
  `repartition_line_ids/tag_ids` en `data/template/account.tax-cl.csv` y se cambiaron 4 fórmulas
  del informe de impuestos a `-...` exactamente como lo hizo Odoo en `l10n_cl` 20 (Ventas Netas
  Gravadas con IVA, IVA Debito Fiscal, Base Retenciones ILA (ventas), Retenciones ILA (ventas)).
  `account_account_tag_data.xml` se carga antes de `account_report.xml` (si no, `tag_cl_02` choca
  con la etiqueta que crea el informe).
- `template_cl.py`: `account.account.company_id` → `company_ids`; contexto `lang='es_CL'` solo si
  el idioma está activo.
- Vistas (mínimo para validar): `<tree>` → `<list>` (también en xpaths y `view_mode`);
  `groups_id` → `group_ids`; `editable="top/bottom"` → `bottom`; `active_id`/`active_ids` en
  vistas → `id` / `parent.sale_order_ids`; `<group expand string>` en búsquedas → `<group>`;
  `deprecated` → `active` y `company_id` → `company_ids` en dominios de `account.account`;
  `toggle_active` → cinta "Archivada"; kanban de `account.move` y formulario de contactos de
  `res.partner` adaptados a la estructura nueva; se quitó la herencia de
  `base.res_partner_view_form_private` (ya no existe); `report_file` quitado de `ir.actions.report`.
  `views/mail_dte.xml`: el dominio de `xml_id` comparaba `res_model` con `active_id` (nunca
  coincidía); ahora `res_id = id`.
- `views/portal_my_details.xml` **desactivado** en el manifiesto (quitado en la Task 4).
- No instalar `l10n_cl` ni `l10n_latam*`: Odoo 20 instala `l10n_cl` al crear una compañía con país
  Chile (`res.company.install_l10n_modules`, fuera del modo de pruebas) porque `l10n_cl` tiene
  `auto_install: ['account']` y `countries: ['cl']`. Defensas:
  1. `res.company._compute_uninstalled_l10n_module_ids` descarta `l10n_cl`/`l10n_latam*` (vale
     aunque `auto_install` vuelva a `True`).
  2. `"excludes"` en el manifiesto: `button_install` se niega a instalarlos junto a `l10n_cl_fe`
     ("Modules ... are incompatible"). Consecuencia: no se puede instalar `l10n_cl_fe` en una base
     que ya tiene `l10n_cl`; migrar desde `l10n_cl` exige desinstalarlo antes.
  3. Ayuda menor: `_cl_fe_desactivar_auto_install()` pone `auto_install=False` en el post_init y tras
     `update_list()`. Un `odoo -u`/`-i` corre `update_list` antes de cargar `l10n_cl_fe` y lo deja
     otra vez en `True` (comprobado); por eso lo que manda son 1 y 2. La plantilla `cl` ya apunta a `l10n_cl_fe` gracias al override de
  `_get_chart_template_mapping` en `template_cl.py`, así que `try_loading('cl')` no instala `l10n_cl`.

## Task 3 — Paso 1: fallas al empezar (`scripts/test.sh l10n_cl_fe`, commit 1f5299d)

Resumen: `1 failed, 16 error(s) of 75 tests` (107 pruebas en las estadísticas del módulo); además
`setUpClass` roto en 2 clases cuyas pruebas no se ejecutan.

| Prueba | Causa |
|---|---|
| `test_recepcion.TestRecepcionBhe` (setUpClass) | `account.account.company_id` no existe (dominio en la prueba) |
| `test_recepcion.TestBoletaSoloIva.test_boleta_consolida_todo_impuesto_en_el_iva` | `KeyError: 'compute_all_tax'` (`_sync_dynamic_lines`) |
| `test_recepcion.TestBoletaSoloIva.test_factura_mantiene_los_dos_impuestos` | `KeyError: 'compute_all_tax'` |
| `test_trafquin.TestTrafquin.test_emitidos_se_publican_sin_reenviar_al_sii` | `XMLSyntaxError`: `b64decode` de un `BinaryValue` (`upload_xml._get_xml`) |
| `test_trafquin.TestTrafquin.test_listar_sin_fechas_por_folios_y_restaurar_seleccion` | ídem |
| `test_trafquin.TestTrafquin.test_registra_el_xml_una_sola_vez` | ídem |
| `test_trafquin.TestRevisionSii.test_rcv_crea_recepcion_sii_la_completa_el_xml_y_no_duplica` | ídem |
| `test_trafquin.TestDeclaracionF29Impuestos.test_retencion_de_boletas_de_honorarios_en_el_151` | `KeyError: 'compute_all_tax'` |
| `test_trafquin.TestXmlPorDocumento.test_descarga_un_documento_y_no_duplica` | `XMLSyntaxError` (binario) |
| `test_trafquin.TestXmlPorDocumento.test_pedir_xml_al_proveedor` | `res.partner.company_type` no existe |
| `test_trafquin.TestXmlPorDocumento.test_si_timbra_documentos_es_software_propio` | `KeyError: 'compute_all_tax'` |
| `test_trafquin.TestXmlPorDocumento.test_software_propio_registra_sin_xml_y_el_xml_completa_la_factura` | `res.partner.company_type` |
| `test_trafquin.TestAsientoF29` (setUpClass) | `account.account.company_id` en la prueba |
| `test_trafquin.TestRevisionMultiCompania.test_calculo_odoo_solo_con_documentos_de_la_compania` | `KeyError: 'compute_all_tax'` |
| `test_trafquin.TestReglasMultiCompania.test_recepcion_de_xml_de_otra_compania_del_usuario` (FAIL) | `ir.attachment.datas` eliminado (`_check_contents should handle that`) |
| `test_trafquin.TestBoletasDiariasSii.test_concilia_pagos_con_el_48_y_verifica_la_rebaja` | `KeyError: 'compute_all_tax'` |
| `test_trafquin.TestBoletasConVoucher.test_boleta_39_pagada_con_voucher_pide_nc` | `KeyError: 'compute_all_tax'` |

## Hecho en la Task 3 (Python)

Resultado: `scripts/test.sh l10n_cl_fe` → `0 failed, 0 error(s) of 102 tests` (todas las clases
pasan `setUpClass`; antes 75 pruebas con 1 falla y 16 errores).

- `self._context/_cr/_uid` (eliminados) → `self.env.context/cr/uid`.
- `ir.config_parameter.get_param/set_param` → `get_str/get_int/get_bool` y `set_*`.
  `account.auto_send_email` se lee como booleano (antes `'False'` era verdadero) y la persistencia
  se guarda en `account.auto_send_persistencia` (se escribía en `...peresistencia`).
- Binarios: los campos entregan `BinaryValue` (contenido en bruto); se lee `.content` o
  `.to_base64()` (lo que espera `facturacion_electronica`), se escribe `BinaryBytes` o base64 `str`,
  los adjuntos se crean con `raw`. `models/binario.py::contenido_binario()` para valores mixtos.
  El código de barras del TED (`sii_barcode_img`) se guarda en bruto (`image_data_uri` de Odoo 20
  recibe bytes en bruto).
- `_name_search` → `_rec_names_search` (RUT en `res.partner`, `partner.activities`,
  `account.journal.sii_document_class`); se quitó el override de `account.move` (Odoo ya busca por
  nombre). `read_group` es `final` en Odoo 20: se quitó el override de consumo de folios (los
  totales son campos almacenados).
- `account.account.company_id` → `company_ids` (`parent_of`); `res.partner.company_type` eliminado
  → campo de compatibilidad calculado desde `is_company` (con inverso).
- VAT: Odoo 20 normaliza el VAT al guardar (`_inverse_vat`) y para Chile dejaba `12345678-5`. Este
  módulo guarda y busca `CL` + 9 dígitos: `format_vat_cl` lo mantiene y `check_vat_cl` acepta el
  prefijo `CL`.
- `view_mode` `tree` → `list` en acciones Python; `account.move.payment_id` → `origin_payment_id`
  (`_get_last_sequence_domain`, también en SQL); `purchase.order.line` `product_uom/taxes_id` →
  `uom_id/tax_ids`; `/download/boleta` usa `_render_qweb_pdf(<ref>, ids)`; pago del F29:
  `_get_total_amount_in_wizard_currency_to_full_reconcile` → `_get_total_amounts_to_pay`.
- `check_company` quitado de `account.move.document_class_ids` (`sii.document_class` no tiene
  compañía; Odoo 20 falla al validar).
- `es_CL` se activa en el `post_init_hook` (Odoo 20 rechaza `with_context(lang=...)` con un idioma
  inactivo; el XML de los DTE y los libros se arman con `lang='es_CL'`).
- Plan `cl`: las claves `property_*` del `template_data` se descartan desde Odoo 18; las cuentas por
  defecto pasan a `res.company` (`receivable/payable/income/expense_account_id`,
  `account_stock_valuation_id`). Sin esto, el diario de ventas de una compañía nueva quedaba sin
  cuenta y no se podía crear una factura.
- Impuestos (API de *base lines*): se borró la copia del motor de 17.0 (`compute_all`,
  `_convert_to_tax_base_line_dict`, `_compute_taxes_for_single_line`, `_aggregate_taxes`,
  `_prepare_tax_totals`, `compute_all_tax` de las líneas y el override de `_compute_tax_totals`).
  Ahora: `account.move._get_sync_stack` agrega la sincronización de descuentos/recargos globales y
  comisiones (55/56, entre impuestos y epd); `_get_rounded_base_and_tax_lines` incluye las líneas
  `D`/`R`/`C` como base lines; `account.move.line._compute_totals` les calcula subtotal/total;
  `account.tax._eval_tax_amount_fixed_amount` aplica `compute_factor` (impuesto fijo por unidad);
  `_compute_amount` y `_compute_needed_terms` son los de Odoo 20 con la separación de retenciones
  (`sii_type` `R`/`A`, `amount_tax_retencion`). `compute_all` de Odoo 20 sigue existiendo (sin
  `discount`/`uom_id`) y es el que usan el XML del DTE, comisiones y la recepción.
- `_sql_constraints` → `models.Constraint` (CAF por nombre de archivo, glosa de comisión por
  documento, F29 por compañía y período, nombre/código de letras y responsabilidades del SII).
  Los nombres de las de `sii.py` cambian (`name` → `name_uniq`, `code` → `code_uniq`: `_name` es un
  atributo reservado). Prueba: `tests/test_restricciones.py`.
- Etiquetas sin signo: verificado con `tests/test_etiquetas_impuestos.py` (factura 33 con IVA 19%:
  `IVA Debito Fiscal` con saldo −190 y el informe `-IVA Debito Fiscal` suma +190; compra:
  `IVA Pagado Compras Recuperables` +190; la nota de crédito resta). Los signos ya eran correctos.
  `IVAV_17` tenía en 17.0 `+` en las líneas de nota de crédito (sumaban al débito); sin signo, la
  nota de crédito resta, que es lo correcto.
- CAF: `caf_string`/`caf_file` con `groups=GRUPOS_SECRETOS`; el timbraje los lee con sudo
  (`dte.caf._contenido_caf_b64`, `load_caf`, `_caf_file` de la boleta voucher; `decode_caf` e
  `ir.sequence.get_caf_file` son públicos y no usan sudo) y apicaf
  crea el CAF con sudo tras validar la firma.
- `firma_id` del contexto: `sii.firma._firma_autorizada(company)` (mismas reglas que
  `res.users.get_digital_signature`, o modo sudo) en `dte.envio.mixin`, el asistente de XML,
  `sii.xml.envio` y apicaf. La cola de envío se procesa con sudo (cron y botón) con su
  `firma_id`, así que `sii.cola_envio.create`/`write` sin sudo exigen que el usuario pueda usar esa
  firma (`_firma_autorizada`) al asignarla **y al agregar documentos** (`document_ids`, `book_ids`,
  `consumo_folios_ids`, `boleta_voucher_ids`); `write` lo exige también para la firma que la
  cola ya tiene (no se puede quitar ni cambiar la firma de una cola ajena). El respaldo de la
  firma del admin es explícito (`_resolve_cola_firma(company, respaldo_admin=True)`) y solo lo
  usa el cron: `_validar_firma` lo aplica con el contexto `cola_cron`, que pone únicamente
  `_cron_procesar_cola`; no depende de `env.su`. Un usuario sin firma propia deja la cola sin
  firma; el cron le asigna la del admin; si no hay ninguna firma, falla al encolar. El botón
  Procesar (`action_procesar`) exige `check_access('write')` como el usuario antes del sudo y,
  en una cola sin firma, asigna la firma propia del usuario o lanza `UserError` (nunca la del
  admin; apaga `cola_cron` aunque llegue en el contexto). Quitar documentos de una cola al
  pasarlos a una cola manual (`_filter_for_manual_queue`) se hace con sudo. El chequeo no es un
  `@api.constrains`: Odoo 20 corre las restricciones con sudo (`_validate_fields`), así que una
  restricción no distingue al usuario del proceso interno (la de la Task 3 nunca se aplicaba).
  Pruebas en `tests/test_accesos.py`.
- `/download/boleta`: sigue `auth='public'`, pero solo acepta `account.move` y pasa por
  `CustomerPortal._document_check_access` (lectura del usuario o `access_token`).
- Consulta pública `/boleta/<folio>`: exige folio, fecha, monto total exacto y RUT del emisor
  (compañía), solo documentos publicados 39/41/61. El PDF se sirve en `/boleta/<folio>/pdf`, que
  repite la validación en cada request; no se crea ni se guarda `access_token`. Pruebas HTTP en
  `tests/test_controladores.py`.
- Pruebas upstream: solo se adaptó el *setup* (ver el commit `test(l10n_cl_fe): adaptar el setup…`):
  `company_id` → `company_ids` en cuentas, `xml_file` como base64 `str`, y `TestBoletaSoloIva`
  pone la compañía en CLP (la base sin demo queda en USD con redondeo 0,0001).

## Pendiente tras la Task 3

- Impuestos, lo que no se portó del motor de 17.0 (Odoo 20 calcula como el estándar):
  - Líneas `A` (anticipadas): en 17.0 no sumaban al `price_total` de la línea; ahora sí. Los totales
    del documento (`_compute_amount`) siguen restándolas como antes.
  - `include_base_amount_cl`: ya no tiene efecto propio; varios impuestos incluidos en el precio
    sin `include_base_amount` se calculan sobre la misma base neta (lote de Odoo 18+), que era el
    caso de uso.
  - Redondeo del descuento a pesos (SII) dentro del motor: solo queda en
    `_get_price_total_and_subtotal_model` (XML del DTE). Puede haber 1 peso de diferencia entre
    el asiento y el XML en líneas con descuento. **Resuelto en la Task 6** para documentos en pesos
    (el XML sigue al asiento, ver abajo).
  - El cálculo en el navegador (`account_tax.js`) no conoce `compute_factor` (impuesto fijo por
    unidad): la vista previa puede diferir hasta guardar.
  - `tax_totals` es el estándar de Odoo 20 (sin `amount_retencion` ni grupos de retención);
    `totals_widget.js` muestra la diferencia con `amount_total` (ver Task 4).
  - Cambiar solo un descuento/recargo global (sin tocar líneas) no recalcula las líneas de impuesto:
    `_sync_tax_lines` de Odoo 20 solo sigue las líneas producto/epd/redondeo.
  - En borrador, `amount_residual` sale de `tax_totals` (Odoo 20) y no descuenta las líneas `A`.
- `excludes` de `l10n_latam_invoice_document`: Odoo 20 instala `l10n_latam_invoice_document` al
  crear compañías de AR/PE/EC/BR/DO/PA/UY (sus `l10n_*` dependen de él), y con `l10n_cl_fe` instalado
  `button_install` lo rechaza por incompatible: **crear una compañía de esos países falla**.
  Limitación conocida (base chilena).
- `migrations/17.0.*` no se tocaron (no corren en una instalación nueva). Una base 17 que suba a 20
  necesita scripts propios: `ir_rule`/`ir_model_access` → `ir_access`, `datas` → `raw`, etiquetas
  con signo, VAT `12345678-5` → `CL123456785` si se guardó con el formato de Odoo, y las
  restricciones viejas `sii_document_letter_name`/`sii_responsability_name`/`_code` (quedan
  duplicadas, sin daño).
- Descuentos/recargos globales en un documento sin guardar: `_get_rounded_base_and_tax_lines`
  solo agrega las líneas `C`/`D`/`R` si `self.id` (un `NewId` es falso), así que en el formulario,
  antes de guardar, los totales e impuestos no incluyen los descuentos globales.
- `sale.order._amount_all` es código muerto (Odoo usa `_compute_amounts`); `dte_envio_mixin` y
  otros conservan imports de `base64` sin uso.
- Siguen pendientes de la lista anterior (`partner_autocomplete` y `sre.cl`: resueltos en la
  Task 5): accesos de
  `account.move.referencias`, `sale.order.referencias`, `partner.activities`,
  `sii.activity.description`; ids con punto (`access_sucursal.manager`); otras vistas con
  `firma_id` para usuarios sin acceso a `sii.firma` (apicaf resuelto en la Task 4); avisos
  de etiquetas duplicadas; revisar `_check_pre_init` + `excludes` al instalar `account` y
  `l10n_cl_fe` juntos en una base con compañía chilena.

## Hecho en la Task 4 (vistas, informes y JS)

Prueba nueva: `tests/test_web.py` (`HttpCase` con Chrome headless): el cliente web carga la
lista de facturas, el formulario de una factura con el widget de totales y el de un contacto con
RUT; cada acción de ventana del módulo abre su vista por defecto y el formulario de un registro
nuevo (junta las fallas); el PDF real (wkhtmltopdf) de una factura 33 trae el folio y el total.

- Odoo 20 corre OWL 3 (`useProps`, `computed`, `this.totals()`), sin `t-esc` en QWeb (lo ignora
  con un aviso): todo `t-esc` → `t-out`.
- Informe DTE (`views/layout.xml`, factura y ticket): `tax_totals` de Odoo 18+
  (`subtotals[].tax_groups[]`, montos sin formato con el widget `monetary`) y total desde
  `amount_total`. La fila "Retención" sigue la regla de `totals_widget.js`: solo
  `amount_total - tax_totals['total_amount_currency']` si no es cero. Las reparticiones 'R' ya
  vienen dentro de los grupos de `tax_totals` (antes se imprimían además, y las filas no sumaban
  el total). `test_totales_impresos_con_retencion_cuadran` lo prueba con IVA carnes (factura y
  ticket) y retención total de IVA (compra 46). `dte_resolution_date` vacío ya no rompe el
  informe. Arregla el 500 del PDF de un documento
  publicado y de la página `/boleta/<folio>` encontrada. `account.move.currency_format`: sin
  `monetary=` en `res.lang.format` y solo idiomas activos.
- `views/account_move_view.xml`: el `account_id` de las líneas conserva `parent_field` (widget
  `m2o_cell_with_extra_fields`; sin él la lista de líneas fallaba en el navegador).
  `<div class="oe_chatter">` → `<chatter/>`; sin `widget="one2many_list"` (no existe).
  `views/export.xml`: logo con `image_data_uri`.
- Libro de honorarios: el onchange filtraba por `account.move.sended` (ya no existe).
- JS (`web.assets_backend`):
  - `account_file_uploader_dte.js`: `AccountFileUploader` está en
    `@account/components/account_file_uploader/account_file_uploader`.
  - `totals_widget.js` (OWL 3): `formatData` devuelve los totales; si el documento tiene
    retención (`amount_tax_retencion`, campos invisibles en el formulario), agrega la fila
    "Retención" con `amount_total - total_amount_currency` y el total pasa a `amount_total`.
    No hay prueba de navegador con un impuesto de retención.
  - **Impuestos 'A' (anticipados, p. ej. `credec_venta`): una factura con uno de ellos no se
    puede crear** ("The entry is not balanced"): `_compute_amount` (copia de 17.0) cambia
    `amount_total` por la línea 'A', la cuota (`_compute_needed_terms`) se calcula desde ese
    total y no cuadra con las líneas, que el motor de Odoo 20 calcula como un impuesto normal.
    Viene de la Task 3; por eso la fila "Retención" ('A') del informe y del widget no tiene
    prueba.
  - `report/action_manager_report.js` (informes xlsx): contexto desde `@web/core/user`.
  - `notify_manager.js`: reescrito como servicio que escucha
    `dte.caf.apicaf/display_notification` (avisos de apicaf) en el bus.
  - Quitados: `upgrade_fields.js` y `xml/base.xml` (widget `upgrade_odoocoop_boolean` sin uso en
    ninguna vista, plantilla `l10n_cl_fe.ProUpgrade` inexistente ya en 17.0, aviso comercial).
  - `rut_lookup_field`, `dte_document_list` y `firma_alert_service` (nombrados en el plan) no
    existen en este módulo.
- Portal: se quitó el formulario propio de datos del portal (`views/portal_my_details.xml`,
  `controllers/portal.py`, `static/src/js/portal_dte.js`). Odoo 20 reescribió el formulario
  (`portal.address_form_fields`) y ya trae la comuna (`city_id` con `_enforce_city_choice`, que
  incluye Chile si `res.country.enforce_cities` está activo) y el RUT (campo `vat`).
  `/portal/partner_remote` era `auth="public"` y consultaba `sre.cl` con sudo y el token de la
  compañía para cualquier anónimo. El correo DTE (`dte_email`) queda solo en el backend.
  `res.country.state.get_website_sale_cities` queda (sin uso en el módulo).
- apicaf: el asistente muestra `firma_opcion`, una selección calculada con sudo con el nombre de
  las firmas vigentes en que el usuario está autorizado (sus compañías); `firma` se calcula desde
  ella y solo acepta valores de esa lista (Odoo no valida selecciones dinámicas por RPC).
  Un facturador sin lectura en `sii.firma` puede usarlo (`tests/test_accesos.py`).
- Pruebas de `/boleta` sin reemplazar `Boleta._get_report`: PDF real del controlador (se fuerza
  solo `force_report_rendering`, que Odoo omite en modo de pruebas), página encontrada completa y
  control positivo de la factura 33 como boleta 39.

## Pendiente tras la Task 4

- `views/honorarios.xml` ya venía desactivado en upstream (campos obsoletos de 17).
- Los asistentes (`target="new"`) no se abren en `test_web` (necesitan el contexto de su botón),
  salvo apicaf con `Form` en `test_accesos`.
- Kanban de `account.move`: cargan pero no se revisaron en pantalla.
- `mail.message.dte` (procesar DTE recibidos) devuelve `self.env.ref(<vista>).read()[0]` como
  acción para `create_po`/`create_move` (lee una vista, no una acción): código heredado de 17.0.
- `uom` `Mensual`: en 17.0 tenía factor 1 respecto de la unidad de referencia (Días); se mantuvo
  (`relative_uom_id` = Días, factor 1). Probablemente debería ser 30 días.

## Hecho en la Task 5 (privacidad por defecto y RUT)

Pruebas nuevas: `tests/test_privacidad.py` y `tests/test_rut.py`.

- Ningún dato de clientes sale a un servicio externo sin que la compañía lo habilite:
  - `sre.cl`: `res.company.sync_remote_partners` ("Consultar y sincronizar contactos con sre.cl")
    viene **apagado** y gobierna toda llamada: la consulta del RUT al escribir un contacto
    (`fill_partner`/`get_remote_user_data`, onchange), `_sre_post` (lanza `UserError`) y el cron
    `_check_need_update`. Antes solo controlaba el envío de datos; la consulta salía siempre
    (URL y token públicos por defecto).
  - `apicaf.cl` (recibe la llave privada de la firma y el RUT): nuevo `res.company.use_apicaf`,
    apagado. Sin él, `default_get`/`create` del asistente lanzan `UserError` (no se abre),
    `conectar_api`, `delist` y todo envío (`_apicaf_post`) lo exigen, `ir.sequence.solicitar_caf`
    falla con el aviso, `caf_bajo_pedido` no pide folios y los crons de reposición
    (`check_cafs`) y anulación automática (`dte.caf.auto_anular`) se saltan esa compañía.
  - En Ajustes, la URL y el token de cada servicio se muestran solo con el interruptor activo.
  - Una base 17 que se actualice conserva `sync_remote_partners` como estaba (antes `True`):
    sigue consultando sre.cl hasta que se apague. Los scripts de migración lo deben decidir.
  - Ya eran opt-in: trafquin (crons solo con `token_trafquin`, sin valor por defecto) y CMF (sin
    clave API no consulta). MEPCO (`account_tax.py`) lee páginas públicas del Diario Oficial y del
    SII y no envía datos de clientes.
  - `test_sin_llamadas_externas_al_crear_contacto` parchea `requests` (`Session.request`,
    `api.request`, `HTTPAdapter.send`), `urllib3` (`PoolManager.request/urlopen`,
    `HTTPConnectionPool.urlopen`), `urllib.request.urlopen`, `http.client.HTTPConnection.request`
    y `socket.socket.connect/connect_ex`/`socket.create_connection`; cada intento queda en una
    lista que debe quedar vacía (el código heredado tiene `except:` que tragaría la excepción).
    `test_sre_habilitado_si_consulta` es el control positivo: con sre.cl habilitado el mismo flujo
    sí queda registrado.
- `partner_autocomplete` (IAP de Odoo: envía nombre y VAT de los contactos al escribirlos y los
  datos de la compañía al iniciar sesión) se auto-instala con `iap_mail`. **l10n_cl_fe no puede
  impedirlo**: Odoo lo marca en el mismo `button_install` que a l10n_cl_fe, antes de cargar su
  código; con `excludes` la instalación de l10n_cl_fe falla ("Modules ... are incompatible",
  comprobado); desinstalarlo en el `post_init_hook` funciona, pero recarga el registro y Odoo ya
  no corre las pruebas `post_install` de esa instalación ("0 tests"). Por eso se bloquea en el
  despliegue: la imagen de `erp-chile` lo borra (`docker/Dockerfile`). Si quedó instalado, el
  `post_init_hook` deja un WARNING. `test_modulos_que_envian_datos_no_instalados` falla en un
  despliegue que no lo bloquee.
- RUT: `document_number` (RUT/RUN) se guarda al crear y al escribir en una sola forma,
  `_formatear_rut`: puntos de miles, guion, dígito verificador en mayúscula y sin ceros a la
  izquierda (`76.086.428-5`, `10.000.013-K`, `1.234.567-4`). Es la forma que ya dejaban el
  onchange y que usan las pruebas de upstream; el XML del DTE no la usa tal cual: `rut()` y
  `formatear_rut` de facturacion_electronica la reducen a `76086428-5`. Cambio: un RUT de menos de
  10 millones ya no queda como `01.234.567-4` (onchange y `_rut_formateado`). La validación del
  dígito verificador (`_check_document_number_cl` con `check_vat_cl`) ya existía. El `vat`
  (`CL` + 9 dígitos) no se completa desde `document_number` al crear por ORM (solo el onchange).

## Hecho en la Task 6 (emisión de 33, 34, 56 y 61)

Pruebas nuevas: `tests/common_dte.py` (`DteCase`: compañía 76.086.428-5 lista para emitir,
`.p12` autofirmado generado con `cryptography` y cargado con `sii.firma.action_process`,
`cls.caf(codigo, desde, hasta, vencido=False)` con un CAF de llave RSA propia y
`cls.xsd(nombre)` con los XSD de `facturacion_electronica/xsd/`) y `tests/test_emision.py`
(XML de 33/34/56/61 válido contra `DTE_v10.xsd`, totales, referencias y errores de emisión).
La firma y el FRMA del CAF no son del SII: el XML valida contra el XSD, no ante el SII.

- `sii.firma.action_process` no podía cargar ningún `.p12`: el modelo se llama `SignatureCert`
  y tapaba la clase de la biblioteca (instanciaba el modelo de Odoo). Se importa como
  `FeSignatureCert`. Al cargar la firma queda vigente (`check_signature`); antes quedaba
  `unverified` hasta volver a pulsar el botón y `get_digital_signature` no la encontraba.
- `account.move._get_last_sequence_domain`: Odoo 18+ espera una condición `SQL` (no la tupla
  `(where_string, param)`); con un diario con documentos **ninguna factura se podía crear**
  (`_compute_name`). Ahora es la condición de Odoo (sin período, `relaxed=True`) más
  `use_documents`/`document_class_id`, y el reinicio (anual/mensual/nunca) se deduce, como en
  17.0, del último movimiento del mismo grupo (mismo tipo de documento, o sin documento). Si se
  dedujera de todo el diario, un movimiento sin documento después de un DTE ("FAC 12") deduciría
  "nunca" y la numeración `INV/2026/…` volvería a empezar (fix round 1;
  `test_numeracion_sin_documento_en_diario_con_documentos`).
- Requisitos de emisión, con `UserError` claro antes de armar y firmar el XML:
  `account.move._validar_requisitos_emisor` (RUT, giro, número y fecha de resolución; sin fecha,
  `_emisor` fallaba con `AttributeError`), certificado digital vigente (mensaje con
  "certificado") y CAF para el folio (`_timbrar_batch`; antes `TypeError` comparando con
  `False`). CAF agotado: el error de `ir.sequence._next_do` ("No hay más folios"); CAF vencido:
  el de `_validaciones_caf`.
- `CorreoEmisor` solo si la compañía tiene correo DTE (`False` rompía la biblioteca).
- Descuentos en pesos (revisión del plan, foco 2): el `DescuentoMonto` del XML es
  `round(PrcItem × QtyItem) − MontoItem` del asiento. Así `MontoItem = PrcItem × QtyItem −
  DescuentoMonto`, `MntNeto + IVA + MntExe = MntTotal = amount_total` peso por peso. Otras
  monedas siguen con el cálculo anterior (`discount_amount`).
- Neto e IVA del SII en el asiento (fix round 1): `account.move._base_line_neto_sii` (desde
  `_prepare_product_base_line_for_taxes_computation`) usa como base de cada línea de una factura
  en CLP con impuestos del SII sin precio incluido el neto del SII: bruto redondeado menos el
  descuento redondeado a pesos (mitad hacia arriba, como `discount_amount`). Solo ajusta el
  descuento (o, sin descuento, el precio) de la línea de cálculo, no el registro. Con redondeo
  global el IVA queda en `round(MntNeto × 19%)`, la fórmula con que el SII valida (antes:
  205 − 50% daba neto 103 con IVA 19 sobre 102,5; ahora neto 102 e IVA 19; 102,5 sin descuento
  da neto 103 e IVA 20). Con varias líneas con medio peso el XML tampoco cuadraba con el asiento
  (MntTotal 1.997 vs `amount_total` 1.996). Pruebas: `test_iva_consistente_con_neto*`.

## Pendiente tras la Task 6

- El neto del SII solo se aplica a `account.move`: pedidos de venta/compra (y el cálculo en el
  navegador, `account_tax.js`) siguen con el subtotal de Odoo, así que pueden diferir en un peso
  de la factura que generan cuando hay descuentos o precios con decimales. Las líneas con
  impuesto incluido (boletas) siguen con el cálculo de Odoo.
- Facturas 34 con `MntNeto` 0 en el XML (el XSD lo acepta).
- Impuestos especiales ('A' anticipados, específicos/MEPCO, retenciones) en la emisión: sin
  pruebas (deuda documentada).
- El envío al SII (sobre `EnvioDTE`, cola) no se probó: las pruebas solo timbran y firman.

## Hecho en la Task 7 (recepción de DTE de proveedores)

Pruebas nuevas: `tests/test_recepcion_dte.py` (sobre `DteCase`). Fixture `tests/data/envio_dte_33.xml`:
EnvioDTE de intercambio generado con este módulo (un `DteCase` con el RUT del emisor cambiado a
96.806.980-2, `action_post` y `sii.xml.envio.crear_intercambio(factura, "76086428-5")`): una 33,
folio 4521, neto 55.901, IVA 10.621, total 66.522, firmado con un certificado autofirmado y un CAF de
prueba (válido contra `EnvioDTE_v10.xsd`). `envio_malformado.xml`: el mismo archivo truncado.

- Contrato para la etapa 1: con el método de recepción "Crear Factura Solamente"
  (`account.dte_receipt_method = create_move`, o acción `create_move` + "Solo subir" en el asistente
  de carga) cada DTE recibido queda como `in_invoice` **en borrador** con `document_class_id`,
  `sii_document_number`, el proveedor por RUT (se crea si no existe) y enlazado a su
  `mail.message.dte.document` (`move_id`). Se genera el acuse de recibo del envío (`RespuestaDTE`),
  no la aceptación comercial. Antes esa combinación solo creaba el documento recibido; la factura
  se creaba (publicada) recién al aceptar. Con `manual` (valor por defecto) sigue igual: solo el
  documento recibido. Aceptar después un documento que ya tiene la factura en borrador envía la
  aceptación pero no la publica (el cron de aceptación automática tampoco).
- Montos: la factura en borrador trae el neto, IVA y total del DTE peso por peso (incluida una línea
  con descuento). Si el IVA del proveedor difiere por redondeo, se ajusta la línea de IVA del
  borrador al del DTE (`_ajustar_iva_xml`, mismo patrón que la retención de la BHE); si aun así el
  total no cuadra, no se crea la factura y el error queda en el chatter del documento recibido.
  Reemplaza la "línea de redondeo" heredada (una línea creada con `check_move_validity=False`).
- `do_create_inv` hacía `cr.commit()` a mitad del proceso (en pruebas lanzaba `AssertionError`, que
  el `except` se tragaba). Ahora cada DTE va en un savepoint: el que falla se deshace solo y el error
  queda en el chatter del documento recibido o del envío (`_registrar_error`). En un envío con varios
  DTE cada documento recibido enlaza su propia factura (antes quedaba el `document_id` de la vuelta
  anterior).
- Duplicados: un DTE ya registrado (RUT emisor, tipo y folio, `_inv_exist`/`_dte_exist`) no crea otra
  factura ni otro documento, aunque llegue con otro nombre de archivo o por correo.
- Correo (`mail.message.process_mess`): cada XML adjunto en su propio savepoint; uno malformado deja
  "No se pudo procesar el XML <nombre>: <error>" en el chatter del registro al que llegó el correo
  (el `mail.message.dte`) y no impide procesar los demás (antes la excepción cortaba el lote y solo
  quedaba en el log). Un segundo EnvioDTE en el mismo correo (o en una respuesta al mismo hilo) va a
  su propio `mail.message.dte`: antes reemplazaba el `xml_id` y los documentos del primero perdían su
  XML. `mail.message.dte.process_message` (asistente "Procesar DTE" sobre varios envíos) también
  procesa cada envío por separado y deja el error en su chatter.
- La compañía receptora y el proveedor se buscan por `vat` o por `document_number`
  (`res.company._buscar_por_rut`, `res.partner._buscar_por_rut`): una compañía creada por ORM no tiene
  `vat` (ver Task 5) y la recepción no la encontraba ("No existe compañia para 76086428-5").
- `<CorreoEmisor/>` vacío (válido contra el XSD; la biblioteca lo emite así cuando la compañía no
  tiene correo DTE) rompía la creación del proveedor. Declaración XML con comillas simples o
  `iso-8859-1` en minúsculas: antes `ValueError` de lxml. XML mal formado en el asistente de carga:
  `UserError` con el nombre del archivo.
- Configuración que la recepción necesita (las pruebas la dejan como el asistente de diarios): el
  diario de compras con los tipos de documento recibidos (`document_class_ids`; si no, "No existe
  Diario para el tipo de documento") y con actividades (`journal_activities_ids`): el recibo de
  mercaderías y la respuesta comercial sobre la factura arman los datos del "emisor" desde el diario
  de la factura.
- XSD: el acuse de recibo (`RespuestaDTE`/`RecepcionEnvio`) y la respuesta comercial
  (`RespuestaDTE`/`ResultadoDTE`) validan contra `RespuestaEnvioDTE_v10.xsd`; el recibo de mercaderías
  (`EnvioRecibos`) contra `EnvioRecibos_v10.xsd` (la consulta y el reclamo al SII se simulan).

## Pendiente tras la Task 7

- **Firma de los DTE recibidos: no se verifica.** Ni el módulo ni `facturacion_electronica`
  comprueban la firma del EnvioDTE ni la de cada DTE (en `respuesta.py`, `_check_digest_caratula` y
  `_check_digest_dte` tienen la verificación comentada y responden siempre "Envío Ok"/"DTE Recibido
  OK"). Un XML adulterado se registra como bueno. Tampoco se valida el TED/CAF del emisor ni el XSD
  del envío recibido (`_validar_caratula` pone `EstadoRecepEnv = 0` al final aunque falle). Las
  pruebas modifican el XML del fixture (folio, IVA) sin volver a firmarlo.
- `sii.dte.claim.do_reject` usa `tipo` sin definir (rechazo comercial de un documento recibido falla
  con `NameError`); no se tocó.
- `_inv_exist` no filtra por compañía.
- La biblioteca emite `<CorreoEmisor/>` vacío en los DTE (no boletas) cuando la compañía no tiene
  correo DTE: `dte.py` asigna `CorreoEmisor` siempre; el arreglo de la Task 6 solo evita que el
  módulo le pase `False`. Es válido contra el XSD (el fixture de la Task 7 lo trae).

## Repositorio privado (`erp-chile`)

- `scripts/test.sh` usa `--without-demo=all`; Odoo 20 avisa "since 19.0, invalid boolean value:
  'all', assume True" (funciona, solo es un aviso).
- `docker/Dockerfile` borra `partner_autocomplete` y `pos_partner_autocomplete` de la imagen (en
  `odoo/addons` y en la copia parcial de `dist-packages/addons`): ver la Task 5. Un módulo con el
  mismo nombre en `/mnt/addons` no sirve para taparlo (`odoo/addons` va primero en la búsqueda).

## Task 8: libro de compras y ventas

- `account.move.book.set_resumen` era un compute almacenado sin `@api.depends` (solo `onchange`): los
  totales (`total_afecto/exento/iva/otros_imps/total`) quedaban en 0 al crear el libro y, además,
  las notas de crédito sumaban. Ahora tiene `depends` y las notas (60, 61, 112) restan.
  `totales_por_movimiento` sigue sin signo (lo usan la revisión F29 y el xlsx).
- Efecto: F29 (`sii_revision`) lee `total_iva` del libro; antes siempre 0 con movimientos reales.
- Pruebas `tests/test_libro.py`: libro del mes (2x33, 61, compra 33) con cifras a mano y XML del libro
  contra `LibroCV_v10.xsd`. Una compra recibida es `in_invoice` con `use_documents=False` (no se timbra).
