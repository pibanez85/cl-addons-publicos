# l10n_cl_fe

Módulo de Facturación Electrónica para Chile con conexión directa al SII.

## Compatibilidad

Compatible con **Odoo Community** y **Odoo Enterprise**.

> ⚠️ Para evitar conflictos de modelos, datos y vistas, antes de instalar
> este módulo deben **eliminarse** los módulos oficiales de localización
> chilena de Odoo:
>
> - `l10n_cl`
> - cualquier módulo `l10n_latam*` (`l10n_latam_base`, `l10n_latam_invoice_document`, etc.)

**Librería `facturacion_electronica` 0.25.0 o superior.** Actualícela antes de actualizar el módulo (`pip install -U facturacion_electronica`). Con una versión anterior, Odoo rechaza la instalación o la actualización por conflicto de versión.

## Descripción

Se crea este repositorio, para dar un enfoque de firma electrónica directa con el SII.

- Se tomaron los inicios de github.com/odoo-chile/l10n_cl_dte y otros módulos de odoo-chile y la continuación de estos en github.com/dansanti/l10n_cl_dte.
- Este repositorio se crea con la finalidad de unificar módulos y facilitar la mantención de la facturación electrónica, que se estaba muy complejo
- Debido a que el módulo "Base report xlsx" no está publicado en la tienda desde odoo 13.0, se integra el código desde NO es necesario instalarlo https://github.com/OCA/reporting-engine
- Se integra la consulta de datos de empresas al repositorio https://sre.cl, para permitir obtener datos digitando el rut
- Al instalar este módulo acepta que por defecto estará activa la sincronización de datos de partners al repositorio https://sre.cl, sin embargo puede desactivarlo en todo momento desde el panel de configuración general.
- Integración con https://apicaf.cl , Api que permite emitir folios vía api, sin pasar por la página del SII. El uso y condiciones de la api es según políticas expuestas en el sitio web mismo de la api.

## Capacidades del Módulo

### Corrección de Redondeo
- **Compatibilidad SII**: Corrige las diferencias de redondeo entre Odoo y los cálculos exigidos por el SII de Chile
- **Redondeo por línea**: Aplica el redondeo correcto en cada línea de detalle según normativa chilena
- **Totales exactos**: Garantiza que los totales del documento coincidan con los cálculos del SII
- **Ejemplo**: Un producto a $1.000 + 19% IVA en Odoo calcula $1.190, pero si son 3 unidades Odoo hace 3x1190=$3.570, mientras Chile exige (3x1000)x1.19=$3.570 o línea por línea con redondeo distinto. Este módulo ajusta estos cálculos para cumplir con la normativa

### Ley de Redondeo (Ley 20.956)
- **Aplicación automática**: Implementa la Ley 20.956 que establece el redondeo del total a pagar en transacciones en efectivo
- **Redondeo a $10**: Los totales se redondean a la decena más cercana (ej: $1.234 → $1.230, $1.236 → $1.240)
- **Solo efectivo**: El redondeo aplica únicamente a pagos en efectivo, no a transferencias, tarjetas u otros medios electrónicos
- **Campo separado**: El monto de redondeo se registra en un campo independiente para mantener la trazabilidad contable
- **Declaración en XML**: El redondeo se declara en el campo `MontoNF` (Monto No Facturable) del DTE:
  - **MontoNF positivo (+)**: El cliente paga menos que el total calculado (ej: total $1.234, paga $1.230, MontoNF=4). Representa un "descuento" por redondeo a favor del cliente
  - **MontoNF negativo (-)**: El cliente paga más que el total calculado (ej: total $1.236, paga $1.240, MontoNF=-4). Representa un "cargo" por redondeo a favor del emisor
  - El MontoNF no afecta la base imponible ni el IVA, solo ajusta el monto final a pagar
- **Sin PPM**: Como el MontoNF no afecta la base imponible ni el IVA, no se paga Pago Provisional Mensual (PPM) por los valores positivos de MontoNF
- **Cuentas de redondeo**: al cargar el plan de cuentas chileno se crean o reutilizan las cuentas *Ganancia por Redondeo Efectivo* (420106) y *Pérdida por Redondeo Efectivo* (410104) y se asignan a los redondeos de efectivo
- **Propinas (módulo Point of Sale)**: Las propinas en el punto de venta se declaran usando el mismo mecanismo de MontoNF con valor negativo, ya que representan un monto adicional que paga el cliente pero que no forma parte del precio de los productos vendidos. La propina no afecta la base imponible ni genera IVA

### Conversión automática de otros impuestos en boletas
En boletas afectas (DTE 39, comprobante de pago 48 y NC de boleta) **todo impuesto distinto del IVA** (ILA, impuestos específicos, IVA anticipado, etc.) **se consolida automáticamente dentro del IVA** a nivel de asiento contable, aunque el producto esté configurado con dos impuestos (IVA + adicional). **No se requiere duplicar productos** ni mantener variantes específicas para boleta y factura: el mismo producto sirve para ambos documentos.
- **Factura (DTE 33)**: el producto con `neto + IVA + adicional = bruto` genera **dos cuentas contables separadas**, una por el IVA y otra por el adicional, según la configuración de cada impuesto.
- **Boleta (DTE 39, 48 y NC de boleta)**: se mantiene fijo el **bruto** (lo que paga el cliente) y se **recalcula** el desglose como si solo existiera IVA: `neto = bruto / 1.19` e `IVA = bruto − neto`. Se crea **una sola cuenta de impuesto (IVA)** y no se separa el adicional. Funciona igual si el precio del producto está con impuestos incluidos o sin ellos.
- **Por qué**: el formato del DTE de boleta no acepta el campo `OtrosImp`; el SII espera todo el impuesto consolidado en `IVA`. Reflejarlo así en la contabilidad evita la divergencia entre el monto declarado y el asiento, y evita tener que armar productos distintos o flujos paralelos para cada tipo de documento.
- **Configuración del producto**: el producto se mantiene con sus dos impuestos (IVA + adicional). El módulo decide automáticamente, según el tipo de documento del move, si separa o consolida — sin intervención del usuario.

### Búsqueda Inteligente
- **Partners**: Búsqueda por nombre, email, referencia, VAT y **RUT (document_number)** con formato con puntos
- **Tipos de Documento SII**: Búsqueda por nombre, código SII y prefijo del documento

### Configuración de Diarios
- Configuración manual de tipos de documento (Journal Document Class)
- Soporte para documentos DTE y Voucher (código 48)
- Secuencias DTE requeridas solo para documentos electrónicos
- Cuentas por defecto para Descuento Global, Recargo Global y Comisiones

### Emisión de Documentos
- Firma electrónica directa con el SII
- **Modos de Envío al SII**:
  - **Inmediato**: Envío automático al validar el documento
  - **Diferido**: Envío en cola de proceso en segundo plano
  - **Manual**: El usuario decide cuándo enviar
- Consulta de estado en el SII
- Generación de PDF con timbre electrónico

### Recepción de Documentos
- Recepción de XML de intercambio (automática por email o carga manual)
- Parseo automático del XML y creación del documento en borrador
- Validación de firma electrónica del emisor
- **Impuestos adicionales con su tasa**: el ILA de las facturas de proveedor se registra con la tasa de su código (24: 31,5 %, 25 y 26: 20,5 %, 27: 10 %, 271: 18 %), no al 19 %. Si la compañía no tiene el impuesto, se crea desde la plantilla del plan de cuentas; se usan solo impuestos activos y con cuenta
- **Documentos ya recibidos**: se reconocen por el RUT del emisor aunque la razón social del XML no coincida exactamente
- **Origen del documento**: XML o Registro de Compras del SII. Un documento ingresado desde el Registro de Compras se completa cuando llega su XML, sin duplicarse
- **Órdenes de compra**: la factura se asocia a una orden de compra abierta del proveedor con esos productos, si existe, y cada línea se asigna a la línea de la orden por producto o descripción
- **Aprendizaje de productos**: al registrar la factura, el código y el nombre que usa el proveedor quedan guardados en el producto, para reconocerlo en las siguientes facturas
- **Multiempresa**: el XML se busca y registra en cualquiera de las compañías del usuario, sin cambiar la compañía activa
- **Doble verificación con el SII**:
  - **XML recibido → SII**: cada documento recibido por XML se consulta en el registro de reclamos del SII y queda marcado como *Está en el SII* o *No está en el SII* (el SII responde «Documento no ha sido recibido»). Se verifica con el botón *Verificar en el SII* y con un cron diario (documentos de los últimos 90 días aún no confirmados). Filtros *XML que no está en el SII* y *Sin verificar en el SII*
  - **SII → XML**: los documentos que están en el Registro de Compras del SII pero de los que no llegó el XML quedan con origen *Registro de Compras* y se ven con el filtro *Sin XML (Registro de Compras)*; al llegar su XML se completan

### Recepción de Boletas de Honorarios Electrónicas (BHE)
En la boleta de honorarios **quien recibe el servicio retiene el impuesto** y lo entera en el SII; al prestador se le paga el líquido. El módulo registra la boleta desde el XML que entrega el SII:
- **Cómo se usa**: *Contabilidad → Recepcionar XML Intercambio → Subir XML de Envío* (o desde *Compras*), con el XML de la BHE (raíz `<datos>`). Se reconoce automáticamente; no pasa por el flujo de DTE
- **Documento**: factura de proveedor tipo **Boleta de Honorarios Electrónica (71)** con folio, fecha, emisor y el detalle de servicios; el XML queda adjunto. La compañía se toma del RUT receptor de la boleta y el diario es el de compras que tiene el tipo de documento 71
- **Retención**: se usa **un solo impuesto de retención** para todas las boletas (cuenta **210704 Impuesto Retención Segunda Categoría**), aunque la tasa cambie de un año a otro. El monto retenido es siempre el de la boleta (`impuestoHonorarios`), no el que resultaría de la tasa del impuesto. Si no existe, se crea «Retención Honorarios» una sola vez
- **Por pagar**: la cuenta por pagar queda en el **líquido** de la boleta (ej: bruto $210.526, retención $30.526, por pagar $180.000)
- **Retiene el emisor** (`retieneEmisor` = S): no se aplica retención y se paga el total
- **Sin duplicados**: subir la misma boleta otra vez devuelve el documento ya registrado
- **Prestador nuevo**: se crea con su RUT y dirección; el XML de la BHE no trae el nombre, conviene completarlo
- **Libro de honorarios**: las boletas recibidas (tipos 70 y 71) se incluyen en el libro de honorarios del período

### Respuestas DTE (Acuse de Recibo)
Generación de respuestas según normativa SII:
- **Recibo de Envío**: Confirmación de recepción del XML
- **Recibo de Mercaderías**: Aceptación de bienes o servicios (código ERM)
- **Aceptación Comercial**: Validación comercial del documento
- **Reclamos**:
  - RCD: Reclamo al contenido del documento
  - RFP: Reclamo por falta parcial de mercaderías
  - RFT: Reclamo por falta total de mercaderías

### Cola de Envío
Sistema de cola persistente para envío de DTE al SII:
- **Envío diferido**: Los documentos se encolan para envío en segundo plano
- **Reintentos automáticos**: En caso de fallo de conexión (hasta 10 intentos)
- **Cron automático**: Procesamiento periódico de la cola
- **Estados de envío**: NoEnviado, EnCola, Enviado, EnProceso, Aceptado, Rechazado

#### Autopack
Sistema de agrupación automática de documentos en la cola de envío:
- **Agrupación inteligente**: Agrupa documentos compatibles en packs de hasta 20 documentos
- **Ordenamiento por dependencias**: Respeta referencias entre documentos (ej: NC referencia a Factura)
- **Criterios de agrupación**: Misma compañía, usuario, modo (producción/pruebas) y número de atención
- **Separación por tipo**: Facturas, NC, ND, guías y otros DTE pueden agruparse juntos; boletas van en envíos separados
- **Exclusiones automáticas**: Libros y Consumo de Folios siempre van en envíos separados

#### Extensibilidad
La cola de envío está diseñada para ser extendida por módulos adicionales (guías de despacho, POS, cesiones, etc.):
- Soporte para múltiples tipos de documentos en un mismo envío
- Arquitectura basada en herencia que permite agregar nuevos modelos DTE
- Interfaz común: todos los modelos DTE implementan los métodos de envío y consulta de estado
- Los módulos heredados solo necesitan registrar sus documentos y campos, la lógica de procesamiento es compartida

### Control de Líneas por Documento
Gestión del número de líneas de detalle tanto para la hoja impresa como para el XML al SII. Se configura en Ajustes Generales de Contabilidad → "Acción por límite de líneas".
- **Líneas por hoja impresa** (`dte_lines`, default 60): capacidad del formato impreso. Se usa sólo con las acciones `Auto Crear` y `Detener`.
- **Factor de cálculo** (`dte_lines_factor`, default 50): estima el alto real de una línea larga con `ceil(len(texto) / factor)` — evita que un producto con descripción extensa cuente como una sola línea cuando ocupa varias en papel.
- **Acciones** (`dte_lines_action`):
  - **Auto Crear otro documento**: divide el account.move en varios cuando el detalle no cabe en la hoja impresa.
  - **Detener Creación**: bloquea el documento (UserError) si el detalle excede la hoja.
  - **Agrupar líneas en el XML (auto-pack)** *(default)*: mantiene todas las líneas en el account.move y en la hoja impresa, pero en el XML al SII agrupa por firma tributaria cuando se supera el máximo legal:
    - **60 líneas** para facturas, notas de crédito/débito y demás DTE.
    - **1000 líneas** para boletas y boletas exentas (sii_code 39/41).
- **Detalle del auto-pack** (sólo afecta al XML enviado al SII):
  - Agrupa por firma `(CodImp+TasaImp ordenados, IndExe, CodImpAdic)` → IVA solo ≠ IVA+Adicional ≠ Exento.
  - **NmbItem** (máx 80 chars): fijo `"Productos Agrupados {nombre_impuesto}"`.
  - **DscItem** (máx 1000 chars): concatena `"{NmbItem} {qty} x {precio}"` por producto, separados por coma. Si excede los 1000 chars, parte en varias líneas empaquetadas.
  - **QtyItem**: 1.
  - **PrcItem / MontoItem**: suma del grupo.
  - Conserva `Impuesto`, `CodImpAdic`, `IndExe` del primer miembro del grupo para que el cálculo tributario del XML siga siendo correcto.

### Libros Electrónicos
- Libro de Compras
- Libro de Ventas
- Libro de Guías de Despacho
- Libro de Honorarios (boletas de honorarios recibidas, con su retención)
- Consumo de Folios (Boletas)

### Informes de ventas
- **Ventas por tipo de documento**: total de ventas por tipo de DTE y período (Contabilidad → Informes)
- **Pagos electrónicos de facturas**: facturas 33/34 pagadas con un medio electrónico cuando la compañía está en modelo de emisión **voucher**, para rebajarlas en el Registro de Compras y Ventas
- **Modelo de emisión de boleta** (Ajustes): *voucher* (cada pago electrónico es un comprobante 48 que vale como boleta) o *boleta* (siempre se emite boleta). En cada diario de banco se indica si **declara ventas en el SII** y su canal (presencial o internet)

### Multiempresa
Reglas de registro por compañía para libros, honorarios, consumo de folios, CAF, correos y documentos recibidos, envíos y cola SII, respuestas de clientes, sucursales SII, MEPCO y tipos de documento de cada diario: cada usuario ve solo los registros de sus compañías permitidas.

### Folios (CAF)
- **Aviso de vencimiento**: al tomar un folio, si su CAF vence en 30 días o menos se muestra un aviso con los folios que quedan sin usar (una vez al día por usuario; desde 3 días antes, siempre). Los días se configuran con los parámetros `l10n_cl_fe.caf_dias_aviso` y `l10n_cl_fe.caf_dias_aviso_intensivo`
- **Cron de folios**: refresca los CAF sin bloquear a las cajas del punto de venta

### Referencias DTE
Las referencias permiten vincular documentos electrónicos entre sí, requerido por normativa SII:
- **Notas de Crédito/Débito**: Obligatorio referenciar el documento afectado
- **Códigos de Referencia** (solo para NC y ND):
  - `1`: Anula documento de referencia
  - `2`: Corrige texto del documento
  - `3`: Corrige montos
- **Campos de Referencia**: Tipo de documento, folio, fecha y motivo
- **Otros Documentos**: Facturas pueden referenciar órdenes de compra, guías, contratos, etc.
- **Uso en Pedidos de Venta**: Las referencias se pueden definir desde el pedido y se trasladan a la factura

### Integraciones
- **SRE.cl**: Consulta y sincronización de datos de empresas por RUT
- **APICAF.cl**: Gestión completa de folios CAF vía API
- **MEPCO**: Auto-sincronización de impuestos específicos (combustibles) vía SII, Diario Oficial o actualización manual

### APICAF - Gestión de Folios
Integración con [apicaf.cl](https://apicaf.cl) para gestión automatizada de folios:
- **Obtener Folios**: Solicitar nuevos folios directamente desde Odoo
- **Bajo Pedido**: Solicitud automática de folios cuando se agotan (configurable por secuencia)
- **Reobtener Folios**: Recuperar folios previamente emitidos
- **Anular Folios**: Anular rangos de folios no utilizados
- **Consultar Disponibles**: Ver folios disponibles en el SII
- **Alerta Automática**: Notificación cuando los folios están por agotarse
- **Configuración**: URL y Token en Ajustes Generales de Contabilidad

### Revisión mensual con el SII (trafquin)
Odoo se conecta al SII a través de **trafquin**, servicio de consultas de solo lectura, con la **clave tributaria** de cada compañía (o el certificado de firma). Se configura en *Ajustes → Facturación electrónica*: URL y token de trafquin (el token se obtiene en [trafquin.cl](https://trafquin.cl/#planes)), clave SII de la compañía y sistema de facturación (Sistema MIPYME del SII o software propio). Si el SII rechaza la clave, trafquin no reintenta y Odoo avisa para corregirla.

#### Declaración Mensual F29 (Contabilidad → Declaración Mensual F29)
- **Período mensual**: un cron crea la revisión de cada mes y trae la **propuesta de F29 del SII**. La lista muestra por período el impuesto de ventas, el impuesto de compras, el PPM y el **total a pagar**, con su estado (por declarar, declarado, pagado, fuera de plazo, con diferencias)
- **Conciliación por código**: cada código del formulario (538, 537, 511, 062, 089, etc.) se compara con lo calculado en Odoo; el detalle muestra qué documentos y montos lo forman
- **Ajustes del registro diario de boletas**: solo los códigos de boletas y comprobantes 48 (110, 111, 758 y 759) se pueden ajustar con su motivo o tomar de Odoo (*Usar Odoo*). Los totales los calcula el SII y no se modifican; el asiento los recalcula a partir de lo ajustado. *Abrir propuesta en el SII* ingresa al formulario con la clave de la compañía. Guardar los ajustes en la propuesta del SII aún no está disponible: quedan como borrador en Odoo
- **F29 declarado**: *Traer F29 declarado* obtiene lo efectivamente declarado (folio, fecha, reajuste, intereses y multas, condonación, IVA postergado) y deja el historial de lo pagado
- **Asiento y pago**: *Generar asiento F29* crea el asiento (débito y crédito fiscal, remanente, PPM, retenciones, impuesto único, IVA postergado, recargos) y *Registrar pago* lo paga contra el diario de banco. Las cuentas se configuran en Ajustes; el plan de cuentas chileno incluye 420203 *Reajuste Remanente Crédito Fiscal* y 210711 *IVA Postergado por Pagar* para las compañías nuevas; en una compañía existente se crean a mano
- **Nunca presenta ni acepta el F29 en el SII**: prepara, concilia y registra; la declaración la presenta usted

#### Registro de Compras y Ventas diario
- Un cron concilia cada día el **RCV del mes en curso** contra Odoo, documento por documento: *coincide*, *monto distinto*, *recibido sin factura*, *pendiente sin XML*, *falta en Odoo* o *falta en el SII*
- **Compras sin XML**: el documento del Registro de Compras se crea como recibido de origen *Registro de Compras*; desde la línea se puede **descargar el XML** (Sistema MIPYME del SII), **pedirlo al proveedor** por correo o **registrar la factura sin XML** con los montos del SII. Cuando llega el XML, el documento se completa
- Con el libro de compras completo, el libro de compras de Odoo toma los documentos en estado *Registro* y *Pendiente*

#### Boletas diarias y comprobantes de pago electrónico (48)
- **Boletas diarias del SII**: resumen por día de boletas (39, 41) y comprobantes de pago electrónico (48) informados al SII, comparados con las boletas emitidas en Odoo; los días que no están en Odoo se suman en la línea de boletas del libro de ventas
- **Comprobantes 48 (modelo voucher)**: se concilian con los pagos electrónicos de cada día, se proponen las **rebajas** de los pagos de facturas y se vinculan los pagos sin comprobante. En el libro de ventas la línea 48 es solo informativa (no se envía al SII)
- **NC de boletas pagadas con voucher**: se incluyen en el libro de ventas como notas de crédito

#### XML de DTE desde el SII (respaldo)
- Asistente para **listar y restaurar** los XML de documentos **emitidos o recibidos** desde el respaldo del SII, por período, tipo de documento y contraparte, sin duplicar los que ya están en Odoo
- Un cron obtiene cada día los **XML de proveedores** de los últimos días desde el SII y los registra como documentos recibidos

#### Seguridad
- Reglas multiempresa para las revisiones, boletas diarias y rebajas
- Al neutralizar una base (copias de prueba) se borran la clave SII y la configuración de trafquin

## Casos de Uso

### Emisión de Factura Electrónica
1. Crear una factura de cliente con los datos del receptor
2. Agregar líneas de detalle con productos/servicios
3. Validar la factura (se genera el timbre electrónico)
4. El sistema envía automáticamente al SII (o manualmente según configuración)
5. Consultar el estado del DTE en el SII
6. Enviar el XML y PDF al cliente por email

### Recepción de Boleta de Honorarios
1. Descargar desde el SII el XML de la boleta de honorarios recibida
2. Ir a Contabilidad > Recepcionar XML Intercambio > Subir XML de Envío y cargar el XML
3. El sistema crea la factura de proveedor BHE con la retención y el líquido por pagar
4. Revisar, completar el nombre del prestador si es nuevo, y validar

### Recepción de Documentos de Proveedor
1. Recibir el XML del proveedor por email o carga manual
2. El sistema parsea el XML y crea el documento en borrador
3. Revisar y validar los datos del documento
4. Generar las respuestas requeridas (Recepción, Mercadería, Comercial)
5. Enviar las respuestas al proveedor

### Configuración de Diarios con Tipos de Documento
1. Ir a Contabilidad > Configuración > Diarios
2. Seleccionar un diario de Ventas o Compras
3. Activar "Usar Documentos"
4. En la pestaña "Documents", agregar los tipos de documento requeridos
5. Para DTE: asignar la secuencia de folios correspondiente
6. Para Voucher (código 48): no requiere secuencia

### Obtención de Folios vía APICAF
1. Configurar las credenciales de APICAF en la compañía
2. Ir al diario y hacer clic en "Obtener Folios desde el SII"
3. Seleccionar el tipo de documento y cantidad de folios
4. Los folios se cargan automáticamente en la secuencia

### Búsqueda Rápida de Clientes
- En cualquier campo de selección de partner, escribir el RUT con puntos (ej: 12.345.678-9)
- El sistema encontrará el cliente automáticamente
- También funciona búsqueda por nombre, email o referencia

## Traslado de bienes (Res. Ex. SII N° 144 de 2026)

Vigente desde el 1 de noviembre de 2026. Reemplaza a la Res. Ex. SII N° 154 de 2025.

- **Facturas con despacho (33, 34 y 46):** pestaña *Transporte* con tipo de despacho, transportista, chofer, patente, patente del carro y fechas de salida y llegada. Con despacho 2 o 3 se informa el bloque `Transporte` del DTE y se imprime en la factura y en el ticket. Con despacho 1 solo se informa el tipo de despacho: si el comprador retira en el local, la factura basta (resolutivo 15°).
- **Transportista:** si no se indica, con despacho 2 o 3 se informa el RUT del emisor (flota propia). Si el transporte es de un tercero que aún no se conoce, marque *Transportista por confirmar*: el RUT se omite en el XML y se imprime "DIFERIDO".
- **Datos que no se conocen al emitir (resolutivo 4°):** en la patente se puede ingresar `DIFERIDO` o `SIN DATO`, y el chofer no es obligatorio. El schema del SII no admite esos textos en RUT ni en fechas.
- **Referencias:** campo *RUT Otro Contribuyente* (`RUTOtr`), solo cuando el documento referido fue emitido por otro contribuyente, como en la guía de despacho tercerizada.
- **Referencia global** (`IndGlobal`, formato DTE del SII): casilla para cuando el documento afecta a más de 20 documentos del mismo tipo, por ejemplo la factura de todas las guías del mes. El folio queda en 0 y el motivo es obligatorio, porque ahí se explica qué documentos abarca. No sirve para anular ni para corregir texto (códigos 1 y 2), que exigen un único documento de referencia.
- **Envío previo (resolutivo 9°):** los documentos que amparan un traslado (guías, y facturas con tipo de despacho) se envían al SII de inmediato, aunque el método de envío configurado sea *Diferido*. Con método *Manual* se mantiene el envío manual.
- **Cambios de datos:** si cambia el vehículo, el chofer u otro dato, se emite un documento nuevo que referencie al anterior. La factura se corrige con nota de crédito y la guía se registra como anulada en el Registro de Guías de Despacho del sitio del SII.
- **Modelo operacional:** cada contribuyente debe informar su modelo de transporte en el aplicativo del SII a más tardar el 31 de enero de 2027. Si no lo informa, se entiende que el transporte es autogestionado.

## Estado:

| Tipo Documento                | Códigos        | Envío | Consulta | Muestra Impresa | Certificación |
| ----------------------------- | -------------- | ----- | -------- | --------------- | ------------- |
| Factura                       | FAC 33, FNA 34 | OK    | OK       | OK              | OK            |
| Nota de Crédito               | 61             | OK    | OK       | Ok              | OK            |
| Nota de Débito                | 56             | OK    | OK       | OK              | OK            |
| Recepción XML Intercambio     | Env, Merc, Com | OK    | OK       | OK              | OK            |
| Libro de Compra-Venta         | Compra, Venta  | OK    | OK       | OK              | OK            |
| Boleta (1)                    | BEL 39, BNA 41 | OK    | OK       | OK              | OK            |
| Consumo de Folios Boletas (1) | CF             | OK    | OK       | OK              | OK            |
| Guía de Despacho (2)          | 52             | OK    | OK       | OK              | OK            |
| Libro de Guías (2)            | LG             | OK    | OK       | OK              | OK            |
| Cesión de Créditos (3)        | CES            | OK    | OK       | OK              | OK            |
| Factura Exportación (4)       | 110            | OK    | OK       | OK              | OK            |
| Nota Crédito Exportación (4)  | 112            | OK    | OK       | OK              | OK            |
| Nota Débito Exportación (5)   | 111            | OK    | OK       | OK              | OK            |
| Factura de Compras (5)        | 46             | OK    | OK       | OK              | OK            |
| Liquidación de facturas (6)   | 43             | X     | X        | X               | X             |

(1) Boleta Integrada, pero se puede extender al PDV https://gitlab.com/dansanti/l10n_cl_dte_point_of_sale  
 (2) Disponible solo desde este módulo en inventario https://gitlab.com/dansanti/l10n_cl_stock_picking  
 (3) Mediante este módulo se agregan las opciones de timbraje de cesiones solo en facturando https://gitlab.com/dansanti/l10n_cl_dte_factoring  
 (4) Se agregan las opciones Exportación solo en Facturando https://gitlab.com/dansanti/l10n_cl_dte_exportacion  
 (5) NO confundir con el concepto de ingresar facturas de proveedor, que la mayoría le dice de compras, este es un documento de retención de impuestos, la recepción de documentos proveedor, está soportada, con los 4 tipos de respuesta que se deben generar según normativa del SII  
 (6) Se agregará módulo adicional, aún no se desarrolla

- Impuestos Soportados Para Ventas(Probados en emisión):

A = Anticipado
N = Normal
R = Retención
D = Adicional
E = Específico

| Código |              Nombre               |  %   | Tipo | Envío | Observación                                                                                        |
| :----: | :-------------------------------: | :--: | :--: | :---: | -------------------------------------------------------------------------------------------------- |
|   14   |                IVA                |  19  |  N   |  OK   |                                                                                                    |
|   15   |        IVA Retención total        |  19  |  R   |  OK   |                                                                                                    |
|   17   |   IVA al faenamiento de carnes    |  5   |  A   |  OK   |                                                                                                    |
|   18   |         IVA a las carnes          |  5   |  A   |  OK   |                                                                                                    |
|   19   |          IVA a la Harina          |  12  |  A   |   X   |                                                                                                    |
|   23   |        Impuesto adicional         |  15  |  A   |   X   | a) artículos oro, platino, marfil b) Joyas, piedras preciosas c) Pieles finas                      |
|   24   |   DL 825/74, ART. 42, letra b)    | 31.5 |  D   |  OK   | Licores, Piscos, whisky, aguardiente, y vinos licorosos o aromatizados.                            |
|   25   |               Vinos               | 20.5 |  D   |  OK   |                                                                                                    |
|   26   |  Cervezas y bebidas alcohólicas   | 20.5 |  D   |  OK   |                                                                                                    |
|   27   | Bebidas analcohólicas y minerales |  10  |  D   |  OK   |                                                                                                    |
|  271   |        Bebidas azucaradas         |  18  |  D   |  OK   | Bebidas analcohólicas y Minerales con elevado contenido de azúcares. (según indica la ley)         |
|   28   |    Impuesto especifico diesel     |      |  E   |  OK   | Compuesto,Autosincronización MEPCO con diariooficial.cl                                            |
|   30   |           IVA Legumbres           |      |  R   |   X   |                                                                                                    |
|   31   |           IVA Silvestre           |      |  R   |   X   |                                                                                                    |
|   32   |           IVA al Ganado           |  8   |  R   |   X   |                                                                                                    |
|   33   |          IVA a la Madera          |  8   |  R   |   X   |                                                                                                    |
|   34   |           IVA al Trigo            |  11  |  R   |   X   |                                                                                                    |
|   35   |   Impuesto Especifico Gasolinas   |      |  E   |  OK   | Compuesto. Para 95 y 97 octanos, Autosincronización MEPCO con diariooficial.cl                     |
|   36   |             IVA Arroz             |  10  |  R   |   X   |                                                                                                    |
|   37   |        IVA Hidrobiológicas        |  10  |  R   |   X   |                                                                                                    |
|   38   |           IVA Chatarras           |  19  |  R   |   X   |                                                                                                    |
|   39   |              IVA PPA              |  19  |  R   |   X   |                                                                                                    |
|   41   |         IVA Construcción          |  19  |  R   |   X   | Solo factura compras                                                                               |
|   44   | IMPUESTO art 37 Letras e, h, I, l |  15  |  A   |   X   | Tasa del 15% en 1era venta a) Alfombras, tapices b) Casa rodantes c) Caviar d) Armas de aire o gas |
|   45   |        Impuesto Pirotecnia        |  50  |  A   |   X   |                                                                                                    |
|   46   |              IVA ORO              |  19  |  R   |   X   |                                                                                                    |
|   47   |           IVA Cartones            |  19  |  R   |   X   |                                                                                                    |
|   48   |          IVA Frambuesas           |  14  |  R   |   X   |                                                                                                    |
|   49   | IVA factura Compra sin Retención  |  0   |  R   |   X   | hoy utilizada sólo por Bolsa de Productos de Chile, lo cual es validado por el sistema             |
|   50   |    IVA instrumentos de prepago    |  19  |  N   |   X   |                                                                                                    |
|   51   |          IVA gas natural          |      |  E   |   X   | Compuesto,Autosincronización MEPCO con diariooficial.cl                                            |
|   53   |       Impuesto Suplementos        | 0.5  |  R   |   X   |                                                                                                    |

- Otras Funcionalidades

|                 Funcionalidad                 |                                Estado en código                                 | Declaración XML | Resultado SII |                                                                                               Observación                                                                                                |
| :-------------------------------------------: | :-----------------------------------------------------------------------------: | :-------------: | :-----------: | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------: |
|               Descuento Global                |                               Implementado en 90%                               |       OK        |      OK       |                                                              Se necesitan pruebas combinaciones afecto-exento y otras combinaciones de uso                                                               |
|                Recargo Global                 |                               Implementado en 90%                               |       OK        |      OK       |                                                              Se necesitan pruebas combinaciones afecto-exento y otras combinaciones de uso                                                               |
|             Ley Redondeo Efectivo             |                        Implementado por defecto por odoo                        |       Ok        |      Ok       |                                                                                         Se necesitan más pruebas                                                                                         |
|             Montos No Facturables             |          Implementado por defecto por odoo, se agregan indicadores DTE          |       Ok        |      Ok       |                                                                                         Se necesitan más pruebas                                                                                         |
|              Líneas Informativas              |          Implementado por defecto por odoo, se agregan indicadores DTE          |        X        |       X       |                                                                                              En desarrollo                                                                                               |
|             Montos Otras Monedas              |          Implementado por defecto por odoo, se agregan indicadores DTE          |       OK        |      OK       |                                                                         Se necesitan más pruebas en casos no factura exportación                                                                         |
|             Boleta Honorarios 71              |             Implementado Retención, falta recepción XML específico              |    No aplica    |       X       |                                                     Se puede Registrar emisiones o recepciones, pero no hay código para la autorecepción de XML aún                                                      |
|     Declaración Formatos Impresión Ticket     | En caso Facturando/Contabilidad, ticket PDF. En casos PdV solo boleta desde PdV |       Ok        |      Ok       | Por defecto solo Ticket PDF, Para formatos térmicos, solo boleta en PdV, de lo contrario con módulos de pago <a href="https://globalresponse.cl/shop/product/imprimir-a-termica-77">print_to_thermal</a> |
| Declaración Montos Brutos (Impuesto Incluido) |                     Hecho, pero puede que falte algún caso                      |       Ok        |      Ok       |                           Aplicable solo a Facturas con impuestos afectos o exentos, en caso compuestos o específicos deben marcar desglose de impuesto en la ficha impuestos                            |

Si tiene dudas sobre el funcionamiento y consecuencias, recordar visitar <a href="https://globalresponse.cl/forum/how-to">la documentación pública</a> o en <a href="www.sii.cl">www.sii.cl</a> o <a href="https://globalresponse.cl/helpdesk/">realizar una consulta a soporte(de pago)</a> o <a href="https://globalresponse.cl/forum/1">postear en foro(gratuito)</a>. También pueden suscribirse en el <a href="https://www.youtube.com/@dansanti">canal Youtube</a> para videos en vivo

Agradecimientos y colaboradores:

- Daniel Blanco
- Nelson Ramirez
- Carlos Toledo
- Carlos Lopez
- Camilo Bustos

<a href='https://www.flow.cl/btn.php?token=ju5ulkb' target='_blank'>
  <img src='https://www.flow.cl/img/botones/btn-donar-negro.png'>
</a>
