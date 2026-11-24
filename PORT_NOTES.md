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

## Pendiente — Task 3 (Python)

Línea base de pruebas upstream al terminar la Task 2 (`--test-tags /l10n_cl_fe`): 72 pruebas
ejecutadas (3 de instalación + 69 upstream), **1 falla y 16 errores**; además 2 clases no pasan de
`setUpClass` (`TestRecepcionBhe`, `TestAsientoF29`, 17 pruebas que no se cuentan). Causas vistas:

- `KeyError: 'compute_all_tax'` en `account_move.py::_sync_dynamic_lines` (7 pruebas): el cálculo
  de líneas de impuesto de Odoo 18+ cambió (`_prepare_base_line_for_taxes_computation`,
  `_add_tax_details_in_base_line`); también `_compute_tax_totals` usa
  `_convert_to_tax_base_line_dict`, que ya no existe.
- `ir.attachment.datas` eliminado (solo `raw`, valores `BinaryValue`): `controllers/downloader.py:31`,
  `wizard/upload_xml.py:202` (rompe `_create_attachment`: "_check_contents should handle that"),
  `models/sii_dte_claim.py:87`, `models/mail_message_dte.py:35,48`, `models/mail.py:149`,
  `models/dte_envio_mixin.py:287`, `models/sii_revision_declarado.py:86`, y
  `migrations/17.0.0.51.0`. Revisar además todos los `fields.Binary` (Odoo 20 entrega
  `BinaryValue`, no base64).
- `ir.config_parameter.get_param`/`set_param` eliminados → `get_str/get_int/get_bool/get_float` y
  `set_*` (34 usos en `mail_message_dte*.py`, `caf.py`, `sii_cola_envio.py`, `currency.py`,
  `dte_envio_mixin.py`, `res_partner.py`, `res_config_settings.py`).
- `account.account.company_id` → `company_ids` (también en las pruebas upstream:
  `test_recepcion.py:36`, `test_trafquin.py:1092`; la Task 3 debe adaptar el *setup*, no las aserciones).
- `res.partner.company_type` eliminado (`sii_revision.py:1506,1512`, `sii_revision_asiento.py:85`,
  `wizard/upload_xml.py:312,1133`, y la aserción de `test_recepcion.py:68`).
- `_sql_constraints` ya no se aplica (Odoo solo avisa): `account_move_comision.py:56`,
  `caf.py:329`, `sii_revision.py:307`, `sii.py:39,63` → `models.Constraint`/`models.UniqueIndex`.
  **Hoy esas restricciones únicas no existen en la base.**
- `_name_search` ya no existe en el ORM (ahora `_search_display_name`): los overrides de
  `res_partner.py:83`, `partner_activities.py:15`, `account_move.py:919`,
  `account_journal_sii_document_class.py:73` no se llaman (búsqueda por RUT/folio rota).
- `read_group` sobrescrito en `consumo_folios.py:95` (API de agrupación cambió en 18/19).
- `with_context(lang='es_CL')` falla si `es_CL` no está activo: `libro.py:393`,
  `dte_envio_mixin.py:191,279`, `account_move.py:1424`, `consumo_folios.py:141`. Decidir entre
  activar `es_CL` en el `post_init_hook` o proteger cada uso (en `template_cl.py` ya se protege).
- Acciones Python con `view_mode` `tree`: `wizard/upload_xml.py:81,134,137`, `wizard/notas.py:152`,
  `wizard/trafquin.py:264,272`, `models/mail_message_dte_document.py:342`,
  `models/sii_revision.py:1679,1687`, `models/sii_revision_boletas.py:217`.
- Fórmulas del informe y código del F29/libros que dependan del signo de las etiquetas
  (`tax_negate` ya no existe). En `account.tax-cl.csv` había líneas de nota de crédito con `+`
  (p. ej. `IVAV_17`); al quitar el signo quedan como en Odoo 18+, pero hay que verificar totales.
- `controllers/main.py::ReportController.report_routes`: revisar la firma de la ruta en Odoo 20
  (`readonly=True`, `website=True`).
- `migrations/17.0.*`: no corren en una instalación nueva; una base 17 actualizada a 20 necesita
  scripts propios (`ir_rule`/`ir_model_access` → `ir_access`, `datas`, etiquetas con signo, …).
- `_check_pre_init` borra los registros `ir.module.module` de `l10n_cl`/`l10n_latam*` (y `update_list`
  los recrea). Con `excludes`, si una base con compañía chilena intentara instalar `account` y
  `l10n_cl_fe` en la misma operación, `button_install` ahora falla con un error de módulos
  incompatibles en vez de instalar `l10n_cl` (no probado): instalar `account` antes o sin país.
- La consulta de RUT a `sre.cl` sigue en `res.partner.get_remote_user_data` y en el onchange
  `fill_partner`; el plan pide que quede desactivada por defecto.
- `partner_autocomplete` **se auto-instala igual** (es `auto_install` en Odoo core al tener
  `iap_mail`/`base_setup`): se ve en el log de `scripts/test.sh`. Quitar la dependencia no basta
  para la restricción de privacidad; hay que impedirlo (p. ej. el mismo mecanismo de
  `auto_install=False` o un módulo de marca en el repositorio privado).
- Seguridad (`ir.access`): `dte.caf` sigue con `cru` para `base.group_user` (como en 17.0 para los
  internos), así que cualquier usuario interno puede leer `caf_string`/`caf_file` (llave privada RSA
  del timbre). Restringir esos campos con `groups=` y leerlos con sudo al timbrar (revisar
  `caf.py`, `dte_mixin.py`). Revisar también qué internos necesitan `crud` en
  `account.move.referencias`, `sale.order.referencias`, `partner.activities`,
  `sii.activity.description`. Hay ids con punto (`access_sucursal.manager`) que crean registros en un
  módulo ajeno (heredado de 17.0).
- Vistas con `firma_id` (`sii_cola_envio.xml`) para usuarios sin acceso a `sii.firma`: comprobar
  que el nombre se muestre (o mostrarlo con un related sudo) — Task 4.
- Avisos de etiquetas duplicadas (`account.move.gdr`, `sii.dte.claim`, `dte.caf.apicaf`,
  `account.move.reversal`, `sii.revision.f29.linea`).

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
