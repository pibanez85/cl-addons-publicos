-- SII: toda empresa queda en el ambiente de certificación (maullin), nunca en producción
UPDATE res_company
   SET dte_service_provider = 'SIICERT'
 WHERE dte_service_provider IS DISTINCT FROM 'SIICERT';

-- apicaf.cl: sin token no se solicitan CAF reales al SII
UPDATE res_company
   SET token_apicaf = NULL
 WHERE token_apicaf IS NOT NULL;

-- trafquin: sin URL no se consulta el SII
UPDATE res_company
   SET url_trafquin = NULL, token_trafquin = NULL
 WHERE url_trafquin IS NOT NULL OR token_trafquin IS NOT NULL;

-- clave tributaria del SII de cada compañía
UPDATE res_company
   SET sii_clave_tributaria = NULL, sii_clave_rechazada = NULL, sii_clave_rechazo_fecha = NULL
 WHERE sii_clave_tributaria IS NOT NULL OR sii_clave_rechazada IS NOT NULL;

-- sincronización de partners con el servicio remoto
UPDATE res_company
   SET sync_remote_partners = false
 WHERE sync_remote_partners;

-- envío automático de consumo de folios
UPDATE res_company
   SET cf_autosend = false
 WHERE cf_autosend;

-- los documentos no se encolan solos para el SII
INSERT INTO ir_config_parameter (key, value)
VALUES ('account.send_dte_method', 'manual')
    ON CONFLICT (key) DO
       UPDATE SET value = 'manual';
