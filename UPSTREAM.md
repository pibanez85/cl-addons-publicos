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
