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
- `views/portal_my_details.xml` **desactivado** en el manifiesto (ver Task 4).
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
  (`dte.caf._contenido_caf_b64`, `decode_caf`, `load_caf`, `ir.sequence.get_caf_file`) y apicaf
  crea el CAF con sudo tras validar la firma.
- `firma_id` del contexto: `sii.firma._firma_autorizada(company)` (mismas reglas que
  `res.users.get_digital_signature`, o modo sudo) en `dte.envio.mixin`, el asistente de XML,
  `sii.xml.envio` y apicaf. La cola de envío se procesa con sudo (cron y botón) y
  `sii.cola_envio` valida al asignar `firma_id` que sea del usuario o del admin (el respaldo de
  `_resolve_cola_firma`). Pruebas en `tests/test_accesos.py`.
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
    el asiento y el XML en líneas con descuento.
  - El cálculo en el navegador (`account_tax.js`) no conoce `compute_factor` (impuesto fijo por
    unidad): la vista previa puede diferir hasta guardar.
  - `tax_totals` es el estándar de Odoo 20 (sin `amount_retencion` ni grupos de retención): revisar
    `totals_widget.js` en la Task 4.
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
- `views/export.xml`: `'data:image/png;base64,%s' % company.logo` → `image_data_uri(company.logo)`
  (Odoo 20 entrega el logo en bruto) — Task 4.
- Seguridad (ya estaba en 17.0): `/download/boleta` (`auth='public'`) entrega el PDF de cualquier
  registro por `model`/`model_id` con sudo. Revisar aparte.
- `sale.order._amount_all` es código muerto (Odoo usa `_compute_amounts`); `dte_envio_mixin` y
  otros conservan imports de `base64` sin uso.
- Siguen pendientes de la lista anterior: `partner_autocomplete` se auto-instala; la consulta de RUT a
  `sre.cl` no está desactivada por defecto; accesos de `account.move.referencias`,
  `sale.order.referencias`, `partner.activities`, `sii.activity.description`; ids con punto
  (`access_sucursal.manager`); vistas con `firma_id` para usuarios sin acceso a `sii.firma`; avisos
  de etiquetas duplicadas; revisar `_check_pre_init` + `excludes` al instalar `account` y
  `l10n_cl_fe` juntos en una base con compañía chilena.

## Pendiente — Task 4 (vistas y JS)

- `views/portal_my_details.xml` desactivado: en Odoo 20 `portal.portal_my_details_fields` ya no
  existe; el formulario de cuenta usa `portal.address_form_fields`. Rehacer los campos RUT, correo
  DTE y comuna, y revisar `controllers/portal.py` y `static/src/js/portal_dte.js`.
- `views/honorarios.xml` ya venía desactivado en upstream (campos obsoletos de 17).
- Assets JS/Owl (`upgrade_fields.js`, `action_manager_report.js`, `totals_widget.js`,
  `account_file_uploader_dte.js` y su XML) no se cargan en las pruebas de instalación: compilar el
  bundle y probar en el navegador.
- Kanban de `account.move` y formulario de contactos de `res.partner`: adaptados solo para validar;
  revisar en pantalla (el formulario de contactos tipo `dte` oculta el bloque principal y
  `other_info`).
- `views/res_partner.xml`: el campo `vat` queda oculto y se agrega `document_number`; comprobar con
  el formulario nuevo de Odoo 20.
- `uom` `Mensual`: en 17.0 tenía factor 1 respecto de la unidad de referencia (Días); se mantuvo
  (`relative_uom_id` = Días, factor 1). Probablemente debería ser 30 días.

## Repositorio privado (`erp-chile`)

- `scripts/test.sh` usa `--without-demo=all`; Odoo 20 avisa "since 19.0, invalid boolean value:
  'all', assume True" (funciona, solo es un aviso).
