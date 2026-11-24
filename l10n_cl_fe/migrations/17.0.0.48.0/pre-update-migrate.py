import logging

_logger = logging.getLogger(__name__)


def migrate(cr, installed_version):
    """Pre-migration: preparar estructura para autopack."""
    _logger.info("Pre-migración autopack: Migrando de version %s a 17.0.0.48.0" % installed_version)

    # Agregar nuevos campos si no existen
    cr.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'sii_cola_envio' AND column_name = 'manual') THEN
                ALTER TABLE sii_cola_envio ADD COLUMN manual BOOLEAN DEFAULT FALSE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'sii_cola_envio' AND column_name = 'es_boleta') THEN
                ALTER TABLE sii_cola_envio ADD COLUMN es_boleta BOOLEAN DEFAULT FALSE;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'sii_cola_envio' AND column_name = 'sequence') THEN
                ALTER TABLE sii_cola_envio ADD COLUMN sequence INTEGER DEFAULT 10;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'sii_cola_envio' AND column_name = 'document_count') THEN
                ALTER TABLE sii_cola_envio ADD COLUMN document_count INTEGER DEFAULT 0;
            END IF;
        END$$;
    """)

    # Crear tablas de relación Many2many si no existen
    cr.execute("""
        CREATE TABLE IF NOT EXISTS sii_cola_envio_account_move_rel (
            sii_cola_envio_id INTEGER NOT NULL REFERENCES sii_cola_envio(id) ON DELETE CASCADE,
            account_move_id INTEGER NOT NULL REFERENCES account_move(id) ON DELETE CASCADE,
            PRIMARY KEY (sii_cola_envio_id, account_move_id)
        );

        CREATE TABLE IF NOT EXISTS sii_cola_envio_book_rel (
            sii_cola_envio_id INTEGER NOT NULL REFERENCES sii_cola_envio(id) ON DELETE CASCADE,
            account_move_book_id INTEGER NOT NULL REFERENCES account_move_book(id) ON DELETE CASCADE,
            PRIMARY KEY (sii_cola_envio_id, account_move_book_id)
        );

        CREATE TABLE IF NOT EXISTS sii_cola_envio_consumo_folios_rel (
            sii_cola_envio_id INTEGER NOT NULL REFERENCES sii_cola_envio(id) ON DELETE CASCADE,
            account_move_consumo_folios_id INTEGER NOT NULL REFERENCES account_move_consumo_folios(id) ON DELETE CASCADE,
            PRIMARY KEY (sii_cola_envio_id, account_move_consumo_folios_id)
        );

        CREATE TABLE IF NOT EXISTS sii_cola_envio_boleta_voucher_rel (
            sii_cola_envio_id INTEGER NOT NULL REFERENCES sii_cola_envio(id) ON DELETE CASCADE,
            account_move_boleta_voucher_id INTEGER NOT NULL REFERENCES account_move_boleta_voucher(id) ON DELETE CASCADE,
            PRIMARY KEY (sii_cola_envio_id, account_move_boleta_voucher_id)
        );
    """)

    _logger.info("Pre-migración autopack completada")
