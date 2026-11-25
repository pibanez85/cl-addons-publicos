# Origen de los módulos importados

## `l10n_cl_fe`

| | |
|---|---|
| Repositorio | https://gitlab.com/dansanti/l10n_cl_fe.git |
| Rama | `17.0` |
| Commit importado | `becf79258048d6a563527988b43a184e740ece44` ("[IMP]Trafquin: Integración Propuesta F29 de IVA y conciliación con el SII") |
| Licencia | AGPL-3 (se conserva) |
| Importado con | `git subtree add --prefix l10n_cl_fe https://gitlab.com/dansanti/l10n_cl_fe.git 17.0` (historial completo) |

El módulo se porta a Odoo 20 en este repositorio (rama `20.0`). Los cambios del port
están en commits propios sobre el subárbol; lo pendiente está en `PORT_NOTES.md`.

### Traer cambios nuevos de la rama 17.0

```bash
git subtree pull --prefix l10n_cl_fe https://gitlab.com/dansanti/l10n_cl_fe.git 17.0
```

Después de cada pull:

1. Resolver conflictos con el port (los cambios de upstream siguen escritos para Odoo 17:
   `<tree>`, `ir.model.access.csv`, `ir.rule`, `get_param`, `datas`, etiquetas de impuestos
   con signo, etc.; ver `PORT_NOTES.md`).
2. Mantener la versión del manifiesto en `20.0.x.y.z` y sin `partner_autocomplete`.
3. Actualizar el commit importado en esta tabla (`git log -1 --format=%H FETCH_HEAD`).
4. Correr las pruebas: desde el repositorio privado, `scripts/test.sh l10n_cl_fe`.

## `account_financial_report` y `account_asset_management` (OCA 19.0)

OCA no tiene estos módulos en la rama `20.0` (revisado el 2026-11-25: en
`OCA/account-financial-reporting` 20.0 solo está `account_tax_balance`; en
`OCA/account-financial-tools` 20.0, `account_move_line_tax_editable` y `account_usability`).
Se importaron desde `19.0` con el historial del módulo y se portan aquí.

| Módulo | Repositorio | Rama | Commit importado |
|---|---|---|---|
| `account_financial_report` | https://github.com/OCA/account-financial-reporting.git | `19.0` | `cb29a0138464ee2d0fb5cce410059b72b94a9e7d` ("Translated using Weblate (Spanish)") |
| `account_asset_management` | https://github.com/OCA/account-financial-tools.git | `19.0` | `837651fa356c8499648bb70617ef6914a70830d6` ("Translated using Weblate (Slovenian)") |

Licencia AGPL-3 (se conserva). Versión del manifiesto: `20.0.1.0.0`.

Importados con `git subtree split` en un clon temporal (solo el historial del directorio del
módulo) y `git subtree add` aquí:

```bash
git clone --branch 19.0 --single-branch https://github.com/OCA/account-financial-reporting.git /tmp/afr
git -C /tmp/afr subtree split --prefix=account_financial_report -b split-afr
git subtree add --prefix account_financial_report /tmp/afr split-afr
# ídem con account-financial-tools / account_asset_management
```

Dependencias OCA: `date_range` (OCA/server-ux), `report_xlsx` y `report_xlsx_helper`
(OCA/reporting-engine) **sí** existen en `20.0`; no se importan aquí. El repositorio privado los
trae como submódulos (`vendor/oca/server-ux`, `vendor/oca/reporting-engine`, rama `20.0`).

### Traer cambios nuevos de la rama 19.0

```bash
git clone --branch 19.0 --single-branch https://github.com/OCA/account-financial-reporting.git /tmp/afr
git -C /tmp/afr subtree split --prefix=account_financial_report -b split-afr
git subtree pull --prefix account_financial_report /tmp/afr split-afr
```

(Igual para `account_asset_management` desde `OCA/account-financial-tools`.) `subtree split` es
determinista: los commits ya importados tienen el mismo SHA y el pull solo trae los nuevos.

Después de cada pull:

1. Resolver conflictos con el port (upstream sigue en Odoo 19: `ir.model.access.csv`,
   `ir.rule`, `report_file`, `t-set` dentro de `t-call`, `account.group` de Odoo, JS de
   `ReportAction` con ref del iframe, plantillas Owl sin `this.`; ver `PORT_NOTES.md`).
2. Mantener la versión del manifiesto en `20.0.x.y.z`.
3. Actualizar el commit importado en esta tabla (`git -C /tmp/afr log -1 --format=%H origin/19.0`).
4. Correr las pruebas desde el repositorio privado:
   `scripts/test.sh account_financial_report,account_asset_management` y
   `scripts/test.sh l10n_cl_fe_informes_test`.

Cuando OCA publique estos módulos en `20.0`, conviene cambiar a su versión (comparar con el port
de aquí, en especial `account.group`).
