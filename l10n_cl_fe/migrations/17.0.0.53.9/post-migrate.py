import logging

_logger = logging.getLogger(__name__)

NOMBRES = ("Exento Compra", "Exento Venta")

FILTRO = """
    amount = 0
    AND sii_code = 0
    AND ind_exe IS NULL
    AND coalesce(activo_fijo, false) = false
"""

COLUMNAS_REQUERIDAS = {
    "account_tax": ("amount", "sii_code", "ind_exe", "activo_fijo", "type_tax_use", "company_id"),
    "account_tax_repartition_line": ("tax_id", "document_type", "repartition_type", "sequence"),
}


def _columnas(cr, tabla):
    cr.execute(
        "SELECT column_name, udt_name FROM information_schema.columns WHERE table_name = %s",
        (tabla,),
    )
    return dict(cr.fetchall())


def _expr_nombre(cr, columnas):
    """En Odoo 17 account_tax.name es traducible (jsonb); el valor base vive en 'en_US'."""
    if columnas.get("name") == "jsonb":
        return "name->>'en_US'"
    return "name"


def _referencias(cr, destino):
    cr.execute("""
        SELECT c.conrelid::regclass::text, a.attname,
               (SELECT count(*) FROM pg_attribute x
                 WHERE x.attrelid = c.conrelid AND x.attnum > 0 AND NOT x.attisdropped)
          FROM pg_constraint c
          JOIN unnest(c.conkey) AS k(attnum) ON true
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
         WHERE c.contype = 'f' AND c.confrelid = %s::regclass
    """, (destino,))
    return cr.fetchall()


def _unicos(cr, tabla, columna):
    cr.execute("""
        SELECT array_agg(a.attname ORDER BY k.ord)
          FROM pg_constraint c
          JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
          JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
         WHERE c.contype IN ('p', 'u') AND c.conrelid = %s::regclass
      GROUP BY c.oid
    """, (tabla,))
    return [cols for (cols,) in cr.fetchall() if columna in cols]


def _reapuntar(cr, destino, nuevo, viejos, excluir=frozenset()):
    referencias = [r for r in _referencias(cr, destino) if (r[0], r[1]) not in excluir]
    for tabla, columna, _n_columnas in referencias:
        cr.execute(f"SELECT 1 FROM {tabla} WHERE {columna} = ANY(%s) LIMIT 1", (viejos,))
        if not cr.fetchone():
            continue
        for columnas in _unicos(cr, tabla, columna):
            otras = "".join(f", r.{c}" for c in columnas if c != columna)
            cr.execute(f"""
                DELETE FROM {tabla} WHERE ctid IN (
                    SELECT x.ctid FROM (
                        SELECT r.ctid, r.{columna} = ANY(%(viejos)s) AS remapeada,
                               row_number() OVER (
                                   PARTITION BY (CASE WHEN r.{columna} = ANY(%(viejos)s)
                                                      THEN %(nuevo)s ELSE r.{columna} END){otras}
                                   ORDER BY (r.{columna} = ANY(%(viejos)s)), r.ctid) AS rn
                          FROM {tabla} r WHERE r.{columna} IS NOT NULL
                    ) x WHERE x.rn > 1 AND x.remapeada
                )
            """, {"viejos": viejos, "nuevo": nuevo})
        cr.execute(
            f"UPDATE {tabla} SET {columna} = %s WHERE {columna} = ANY(%s)", (nuevo, viejos)
        )
    tablas = [t for t, _c, n in referencias if n == 2]
    for tabla in {t for t in tablas if tablas.count(t) > 1}:
        c1, c2 = [c for t, c, _n in referencias if t == tabla][:2]
        cr.execute(f"DELETE FROM {tabla} WHERE {c1} = {c2}")


def _usos(cr, ids):
    cr.execute("""
        SELECT tax_id, count(*)
          FROM (SELECT account_tax_id AS tax_id
                  FROM account_move_line_account_tax_rel WHERE account_tax_id = ANY(%s)
                UNION ALL
                SELECT tax_line_id FROM account_move_line WHERE tax_line_id = ANY(%s)) x
      GROUP BY tax_id
    """, (ids, ids))
    return dict(cr.fetchall())


def _con_xmlid(cr, modelo, ids):
    cr.execute(
        "SELECT res_id FROM ir_model_data WHERE model = %s AND res_id = ANY(%s)", (modelo, ids)
    )
    return {r[0] for r in cr.fetchall()}


def _pares_repartition(cr, sobreviviente, viejos):
    """En Odoo 17 la línea de reparto apunta al impuesto con tax_id + document_type."""
    cr.execute(
        "SELECT count(*) FROM account_tax_repartition_line WHERE tax_id = ANY(%s)", (viejos,)
    )
    total = cr.fetchone()[0]
    cr.execute("""
        SELECT DISTINCT ON (rl_old.id) rl_old.id, rl_new.id
          FROM account_tax_repartition_line rl_old
          JOIN account_tax_repartition_line rl_new
            ON rl_new.repartition_type = rl_old.repartition_type
           AND rl_new.document_type = rl_old.document_type
           AND rl_new.tax_id = %(nuevo)s
         WHERE rl_old.tax_id = ANY(%(viejos)s)
      ORDER BY rl_old.id, rl_new.sequence, rl_new.id
    """, {"nuevo": sobreviviente, "viejos": viejos})
    pares = cr.fetchall()
    if len(pares) != total:
        return None
    por_nuevo = {}
    for viejo, nuevo in pares:
        por_nuevo.setdefault(nuevo, []).append(viejo)
    return por_nuevo


def migrate(cr, version):
    _logger.info(
        f"POST-MIGRATION: {version} -> 17.0.0.53.9 - "
        "consolida los impuestos exentos duplicados por la recepción de DTE "
        "(_buscar_impuesto filtraba por ind_exe pero creaba sin él, así que cada "
        "documento con IndExe dejaba un impuesto nuevo)"
    )
    columnas_tax = _columnas(cr, "account_tax")
    columnas_rl = _columnas(cr, "account_tax_repartition_line")
    for tabla, columnas in (("account_tax", columnas_tax),
                            ("account_tax_repartition_line", columnas_rl)):
        faltantes = [c for c in COLUMNAS_REQUERIDAS[tabla] if c not in columnas]
        if faltantes:
            _logger.warning(
                "POST-MIGRATION: %s no tiene las columnas %s; se omite la consolidación "
                "de impuestos exentos", tabla, faltantes,
            )
            return
    nombre = _expr_nombre(cr, columnas_tax)
    cr.execute(f"""
        SELECT company_id, type_tax_use, {nombre} AS nombre, array_agg(id ORDER BY id)
          FROM account_tax
         WHERE {FILTRO} AND {nombre} IN %s
      GROUP BY company_id, type_tax_use, {nombre}
        HAVING count(*) > 1
    """, (NOMBRES,))
    grupos = cr.fetchall()
    if not grupos:
        _logger.info("POST-MIGRATION: no hay impuestos exentos duplicados que consolidar")
        return
    candidatos = [i for grupo in grupos for i in grupo[3]]
    usos = _usos(cr, candidatos)
    con_xmlid = _con_xmlid(cr, "account.tax", candidatos)
    eliminados = 0
    for company_id, _tipo, nombre_impuesto, ids in grupos:
        sobreviviente = min(ids, key=lambda i: (i not in con_xmlid, -usos.get(i, 0), i))
        viejos = [i for i in ids if i != sobreviviente and i not in con_xmlid]
        if not viejos:
            continue
        repartos = _pares_repartition(cr, sobreviviente, viejos)
        if repartos is None:
            _logger.warning(
                "POST-MIGRATION: %s (compañía %s) tiene líneas de reparto sin equivalente "
                "en el sobreviviente; se omite para no romper asientos",
                nombre_impuesto, company_id,
            )
            continue
        for nueva, viejas in repartos.items():
            _reapuntar(cr, "account_tax_repartition_line", nueva, viejas)
            cr.execute(
                "DELETE FROM ir_model_data WHERE model = 'account.tax.repartition.line' "
                "AND res_id = ANY(%s)", (viejas,)
            )
        _reapuntar(cr, "account_tax", sobreviviente, viejos, excluir={
            ("account_tax_repartition_line", "tax_id"),
        })
        cr.execute("DELETE FROM account_tax WHERE id = ANY(%s)", (viejos,))
        eliminados += cr.rowcount
        _logger.info(
            "POST-MIGRATION: %s (compañía %s): %s de %s duplicados -> impuesto %s",
            nombre_impuesto, company_id, cr.rowcount, len(ids) - 1, sobreviviente,
        )
    _logger.info("POST-MIGRATION: eliminados %s impuestos exentos duplicados", eliminados)
