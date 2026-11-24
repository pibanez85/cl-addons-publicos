import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info(
        f"POST-MIGRATION: {version} -> 17.0.0.53.7 - "
        "drop sii_xml_envio.firma_id (la firma ahora se resuelve por contexto: "
        "firma de la cola de envío o, por defecto, la del usuario)"
    )
    cr.execute("ALTER TABLE sii_xml_envio DROP COLUMN IF EXISTS firma_id")
