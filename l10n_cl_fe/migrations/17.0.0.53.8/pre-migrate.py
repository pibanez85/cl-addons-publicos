import logging

_logger = logging.getLogger(__name__)

ORPHAN_MENU_XMLIDS = [
    "menu_sii_config",
    "menu_action_act_partner_activities",
    "menu_action_act_sii_activity_description",
    "menu_action_sii_cola_envio",
    "menu_action_sii_concept_type",
    "menu_action_sii_document_class",
    "menu_action_sii_document_type",
    "menu_action_sii_regional_offices",
    "menu_action_sii_responsability",
    "menu_action_sii_xml_envio",
]


def migrate(cr, version):
    _logger.info(
        "PRE-MIGRATION %s -> 17.0.0.53.8: eliminando menús refactorizados "
        "(menu_sii_config y descendientes)",
        version,
    )
    cr.execute(
        """
        DELETE FROM ir_ui_menu
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 'l10n_cl_fe' AND model = 'ir.ui.menu' AND name = ANY(%s)
        )
        """,
        (ORPHAN_MENU_XMLIDS,),
    )
    cr.execute(
        """
        DELETE FROM ir_model_data
        WHERE module = 'l10n_cl_fe' AND model = 'ir.ui.menu' AND name = ANY(%s)
        """,
        (ORPHAN_MENU_XMLIDS,),
    )
