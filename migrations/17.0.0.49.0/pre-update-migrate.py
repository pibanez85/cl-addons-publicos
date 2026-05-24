import logging
import json
import re

_logger = logging.getLogger(__name__)


def parse_doc_ids(doc_ids_str):
    """Parsea string de doc_ids a lista de enteros de forma segura."""
    if not doc_ids_str:
        return []

    doc_ids_str = doc_ids_str.strip()

    # Si es un número simple
    if doc_ids_str.isdigit():
        return [int(doc_ids_str)]

    # Si tiene formato de lista [1, 2, 3]
    if doc_ids_str.startswith('[') and doc_ids_str.endswith(']'):
        # Extraer números con regex
        numbers = re.findall(r'\d+', doc_ids_str)
        return [int(n) for n in numbers]

    # Intentar como JSON
    try:
        result = json.loads(doc_ids_str)
        if isinstance(result, list):
            return [int(x) for x in result]
        return [int(result)]
    except (json.JSONDecodeError, ValueError):
        pass

    return []


def migrate(cr, installed_version):
    """
    Migra la cola de envío de la estructura antigua (model + doc_ids como Char)
    a la nueva estructura (campos Many2many por modelo).
    """
    _logger.info("Migrando sii_cola_envio de version %s a 17.0.0.49.0" % installed_version)

    # Verificar si existen las columnas antiguas
    cr.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'sii_cola_envio'
        AND column_name IN ('model', 'doc_ids')
    """)
    existing_columns = [row[0] for row in cr.fetchall()]

    if 'model' not in existing_columns or 'doc_ids' not in existing_columns:
        _logger.info("Columnas antiguas no encontradas, migración no necesaria")
        return

    # Mapeo de modelo a tabla de relación y campos
    model_mapping = {
        'account.move': {
            'relation_table': 'sii_cola_envio_account_move_rel',
            'cola_field': 'sii_cola_envio_id',
            'doc_field': 'account_move_id',
        },
        'account.move.book': {
            'relation_table': 'sii_cola_envio_book_rel',
            'cola_field': 'sii_cola_envio_id',
            'doc_field': 'account_move_book_id',
        },
        'account.move.consumo_folios': {
            'relation_table': 'sii_cola_envio_consumo_folios_rel',
            'cola_field': 'sii_cola_envio_id',
            'doc_field': 'account_move_consumo_folios_id',
        },
        'account.move.boleta_voucher': {
            'relation_table': 'sii_cola_envio_boleta_voucher_rel',
            'cola_field': 'sii_cola_envio_id',
            'doc_field': 'account_move_boleta_voucher_id',
        },
    }

    # Obtener registros con datos antiguos
    cr.execute("""
        SELECT id, model, doc_ids
        FROM sii_cola_envio
        WHERE model IS NOT NULL
        AND doc_ids IS NOT NULL
        AND doc_ids != ''
    """)
    records = cr.fetchall()

    migrated_count = 0
    error_count = 0

    for cola_id, model, doc_ids_str in records:
        if model not in model_mapping:
            _logger.warning(f"Modelo {model} no soportado en migración, cola_id={cola_id}")
            error_count += 1
            continue

        # Parsear doc_ids de forma segura
        doc_ids = parse_doc_ids(doc_ids_str)

        if not doc_ids:
            _logger.warning(f"No se pudieron parsear doc_ids '{doc_ids_str}' para cola_id={cola_id}")
            error_count += 1
            continue

        mapping = model_mapping[model]

        # Verificar si la tabla de relación existe
        cr.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = %s
            )
        """, (mapping['relation_table'],))

        if not cr.fetchone()[0]:
            _logger.warning(f"Tabla {mapping['relation_table']} no existe, saltando cola_id={cola_id}")
            error_count += 1
            continue

        # Insertar relaciones
        for doc_id in doc_ids:
            try:
                cr.execute(f"""
                    INSERT INTO {mapping['relation_table']} ({mapping['cola_field']}, {mapping['doc_field']})
                    VALUES (%s, %s)
                    ON CONFLICT DO NOTHING
                """, (cola_id, doc_id))
            except Exception as e:
                _logger.warning(f"Error insertando relación cola_id={cola_id}, doc_id={doc_id}: {e}")
                error_count += 1

        migrated_count += 1

    _logger.info(f"Migración completada: {migrated_count} registros migrados, {error_count} errores")
