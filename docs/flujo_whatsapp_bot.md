# Flujo Completo del Bot de WhatsApp — TutelApp
## Análisis de Cumplimiento con Políticas de Meta WhatsApp Business

**Fecha:** 28 de agosto de 2026
**Total de mensajes:** 86 (68 texto + 14 botones + 1 documento + 2 imagen + 1 servicio)

---

## ÍNDICE
1. [Flujo de Conversación Completo](#flujo)
2. [Todos los Mensajes del Bot](#mensajes)
3. [Análisis de Políticas de Meta](#politicas)
4. [Áreas de Riesgo](#riesgos)
5. [Recomendaciones](#recomendaciones)

---

## FLUJO DE CONVERSACIÓN COMPLETO <a name="flujo"></a>

```
USUARIO ENVÍA "HOLA"
    │
    ├─► Usuario nuevo → Bienvenida + Aviso de Privacidad
    │       │
    │       ├─► Acepta consentimiento → Recolección datos (8 pasos)
    │       │       │
    │       │       ├─► Narración del caso (texto o audio)
    │       │       │       │
    │       │       │       ├─► Audio → Transcripción IA → Confirmar
    │       │       │       │
    │       │       │       └─► Texto → Extracción IA
    │       │       │
    │       │       ├─► Revisión de datos extraídos
    │       │       │       │
    │       │       │       ├─► Confirmar → Pruebas
    │       │       │       └─► Corregir → Volver a narración
    │       │       │
    │       │       ├─► Adjuntar pruebas (fotos/documentos)
    │       │       │
    │       │       ├─► Resumen + Juramento
    │       │       │
    │       │       ├─► Generación de PDF
    │       │       │
    │       │       ├─► Opciones post-PDF:
    │       │       │       ├─► Radicación automática ($29.000 COP)
    │       │       │       │       │
    │       │       │       │       ├─► Link de pago Mercado Pago
    │       │       │       │       ├─► Pago confirmado → Verificación admin
    │       │       │       │       ├─► Radicación en portal judicial
    │       │       │       │       └─► Envío de radicado + constancia
    │       │       │       │
    │       │       │       └─► Hazlo tú mismo (GRATIS)
    │       │       │               └─► Puede cambiar a radicación después
    │       │       │
    │       │       └─► COMPLETADO
    │       │
    │       └─► Rechaza consentimiento → Se cancela
    │
    ├─► Usuario existente → Retoma donde dejó
    │
    ├─► "Eliminar mis datos" → Elimina y confirma
    │
    └─► Mensaje no reconocido → Menú de ayuda
```

---

## TODOS LOS MENSAJES DEL BOT <a name="mensajes"></a>

### FASE 1: BIENVENIDA Y CONSENTIMIENTO

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 1 | Texto | `👋 *Hola! Soy el asistente de TutelApp.*\n\nTe ayudo a crear y radicar acciones de tutela en Colombia de forma rápida y sencilla.\n\nComencemos con la autorización de datos.` | Primera vez que el usuario escribe |
| 2 | Botones | `📄 *Aviso de Tratamiento de Datos Personales*\n\nDe acuerdo con la Ley 1581 de 2012 y el Decreto 1377 de 2013, te informamos que:\n\n🔹 *Responsable:* TutelApp\n🔹 *Finalidad:* Gestionar, crear y radicar tu acción de tutela ante la Rama Judicial\n🔹 *Datos recolectados:* Nombre, documento, teléfono, correo, ciudad, historia clínica y demás información relevante para tu tutela\n🔹 *Derechos del titular:* Acceder, actualizar, rectificar y solicitar la eliminación de tus datos en cualquier momento escribiendo *Eliminar mis datos*\n🔹 *Política completa:* https://tutela-online-production.up.railway.app/privacidad\n\nAl aceptar, autorizas el tratamiento de tus datos personales para los fines descritos.` `[("acepto","✅ Acepto"), ("no","❌ No")]` | Después del #1 |

### FASE 2: RESPUESTAS AL CONSENTIMIENTO

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 3 | Texto | `✅ *Consentimiento registrado.*\n\nAhora necesito tus datos personales.` | Usuario acepta |
| 4 | Texto | `❌ *Has cancelado.*\n\nSin tu autorización no podemos procesar la tutela. Si cambias de opinión, escribe *Hola*.` | Usuario rechaza |
| 5 | Texto | `❌ No puedes usar el servicio sin aceptar el tratamiento de datos.\n\nSi cambias de opinión, escribe *Hola*.` | Mensaje de usuario rechazado |
| 6 | Botones | (Repite aviso de privacidad) `[("acepto","✅ Acepto"), ("no","❌ No")]` | Respuesta no válida en consentimiento |

### FASE 3: RECOLECCIÓN DE DATOS (8 pasos)

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 7 | Texto | `👤 Escribe tu *nombre completo*:` | Paso 0 |
| 8 | Texto | `🪪 Tipo de documento (CC, CE, Pasaporte):` | Paso 1 |
| 9 | Texto | `🆔 Número de documento (sin puntos):` | Paso 2 |
| 10 | Texto | `📱 Teléfono celular:` | Paso 3 |
| 11 | Texto | `📧 Correo electrónico (para notificaciones del juzgado):` | Paso 4 |
| 12 | Texto | `🏙️ ¿En qué ciudad vives?:` | Paso 5 |
| 13 | Texto | `📍 Dirección de residencia completa (calle, número, barrio, ciudad):` | Paso 6 |
| 14 | Texto | `🗺️ Departamento (ej: Cundinamarca, Antioquia):` | Paso 7 |
| 15 | Texto | `Por favor responde con texto: {msg}` | Envía media durante recolección |
| 16 | Texto | `✅ *Datos personales registrados.*` | 8 pasos completados |

### FASE 4: NARRACIÓN DEL CASO

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 17 | Texto | `✍️ *Cuéntame tu caso de salud en detalle*\n\nIncluye:\n• Qué EPS te negó el servicio\n• Qué tratamiento, cita o medicamento te negaron\n• Fechas de las negaciones\n• Qué le pides al juez que ordene\n\n🎤 Puedes enviar un *audio* contando tu caso.` | Después de datos personales |
| 18 | Texto | `🎤 *Transcripción de tu audio:*\n\n"{texto_audio[:500]}"\n\n¿Es correcto?` | Audio recibido y transcrito |
| 19 | Botones | `¿La transcripción es correcta?` `[("1","✅ Sí"), ("2","✍️ No, escribir")]` | Después de #18 |
| 20 | Texto | `No pude procesar el audio. Escribe tu caso.` | Error en transcripción |
| 21 | Texto | `Hubo un error procesando tu caso. Intenta de nuevo.` | Error en extracción IA |

### FASE 5: CONFIRMACIÓN DE AUDIO

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 22 | Texto | `Hubo un error procesando tu caso con la IA. Escribe *reintentar* en un momento.` | Error IA tras confirmar audio |
| 23 | Texto | `✍️ *Escribe tu caso manualmente*\n\nCuéntame qué pasó con todos los detalles.` | Usuario rechaza transcripción |
| 24 | Botones | `¿La transcripción es correcta?` `[("1","✅ Sí"), ("2","✍️ No, escribir")]` | Respuesta no válida |

### FASE 6: REVISIÓN DE DATOS

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 25 | Texto | `📋 *Revisa los datos extraídos de tu caso:*\n\n{preview}` | Datos extraídos por IA |
| 26 | Botones | `¿Los datos son correctos?` `[("1","✅ Sí, confirmar"), ("2","✏️ Corregir")]` | Después de #25 |
| 27 | Texto | `⚠️ *Faltan datos importantes:* {', '.join(faltantes)}` | Confirmado pero faltan campos |
| 28 | Texto | `¿Quieres agregar más detalles? Escribe tu caso de nuevo.` | Después de #27 |
| 29 | Texto | `✍️ *Escribe tu caso de nuevo con más detalles o correcciones:*` | Selecciona corregir |

### FASE 7: PRUEBAS

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 30 | Botones | `📎 ¿Tienes soportes o pruebas para adjuntar?\n\nEj: fórmulas médicas, resultados, respuestas de la EPS, pantallazos.` `[("adjuntar","📎 Adjuntar pruebas"), ("saltar","⏭️ Sin soportes")]` | Después de revisión |
| 31 | Texto | `📸 *Envía tus soportes*\n\nPuedes enviar fotos o documentos. Las analizaré y las incluiré como pruebas en tu tutela.` | Selecciona adjuntar |
| 32 | Botones | `Envía tus soportes. Cuando termines, presiona el botón.` `[("listo","✅ No tengo más")]` | Después de #31 |
| 33 | Texto | `📎 *Envía el siguiente soporte.*` | Selecciona enviar otro |
| 34 | Botones | `Cuando termines de enviar tus soportes, presiona el botón.` `[("listo","✅ No tengo más")]` | Después de #33 |
| 35 | Texto | `✅ *Soporte {num_soportes} recibido.*` | Archivo descargado OK |
| 36 | Botones | `¿Tienes más soportes o generamos la tutela con los que hay?` `[("enviar_otro","📎 Enviar otro"), ("listo","✅ No tengo más")]` | Después de #35 |
| 37 | Texto | `No pude descargar el archivo. Presiona *No tengo más* para continuar.` | Error descarga |
| 38 | Botones | `¿Qué deseas hacer?` `[("enviar_otro","📎 Intentar otro"), ("listo","✅ No tengo más")]` | Después de #37 |
| 39 | Botones | `Presiona *No tengo más* cuando termines de enviar tus soportes.` `[("listo","✅ No tengo más")]` | Texto sin media |

### FASE 8: RESUMEN Y JURAMENTO

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 40 | Texto | `📋 *Resumen de tu tutela*\n\n👤 *Nombre:* {accionante_nombre}\n🆔 *Documento:* {accionante_tipo_doc} {accionante_cedula}\n📧 *Email:* {accionante_email}\n🏙️ *Ciudad:* {ciudad}\n🏛️ *Accionado:* {accionado}\n📝 *Hechos:* {hechos[:300]}...` | Después de pruebas |
| 41 | Botones | `⚖️ *Juramento*\n\n¿Afirmas bajo la gravedad de juramento que *no has interpuesto otra acción de tutela* por los mismos hechos y derechos ante ningún otro juez?` `[("1","✅ Sí, juro"), ("2","❌ No")]` | Después de #40 |
| 42 | Texto | `Sin el juramento no podemos generar la tutela. Si cambias de opinión, responde *Juro*.` | Usuario dice "no" |

### FASE 9: GENERACIÓN DE PDF

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 43 | Texto | `⏳ *Generando tu tutela...* Esto puede tardar unos segundos.` | Juramento aceptado |
| 44 | Texto | `Hubo un error técnico generando tu PDF. Escribe *juro* para reintentarlo.` | Error generación |
| 45 | Texto | `✅ *¡Tutela generada!*` | PDF OK |
| 46 | Documento | Envía `tutela_{id}.pdf` con caption `📄 Tutela generada` | Después de #45 |
| 47 | Texto | `⚠️ No pude enviar el PDF. Intenta de nuevo.` | Error envío documento |
| 48 | Botones | `📄 *PDF generado y enviado*\n\nAhora tienes 2 opciones:\n\n1️⃣ *Radicación automática* — *$29.000 COP*\n   Radicamos por ti ante la Rama Judicial.\n   Entrega en máximo *4 horas hábiles*.\n   Te entregamos el número de radicado.\n\n2️⃣ *Hazlo tú mismo* — GRATIS` `[("1","💳 Radicación $29k"), ("2","✍️ Hazlo tú mismo")]` | Después de #46 |

### FASE 10: DECISIÓN DE RADICACIÓN

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 49 | Texto | `💳 *Radicación automática*\n\nPor *$29.000 COP* radicamos tu tutela ante la Rama Judicial.\nIncluye:\n✅ Radicación en el portal oficial\n✅ Número de radicado y constancia\n✅ Entrega en máximo 4 horas hábiles\n\n¿Quieres continuar con el pago?` | Elige opción 1 |
| 50 | Botones | `¿Confirmas que deseas radicar tu tutela por *$29.000 COP*?` `[("confirmar_pago","✅ Sí, pagar ahora"), ("2","❌ No, hazlo yo mismo")]` | Después de #49 |
| 51 | Texto | `Entendido. Recibiste el PDF de tu tutela por este chat.\n\nSi al intentar radicarla lo ves complejo, pulsa el botón y lo hacemos por ti por *$29.000 COP* sin repetir tus datos.` | Elige opción 2 (hazlo tú mismo) |
| 52 | Botones | `¿Qué prefieres hacer?` `[("1","💳 Radiquen $29k"), ("2","✍️ Lo hago yo")]` | Después de #51 |

### FASE 11: PAGO

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 53 | Texto | `💰 *Radicación automática*\n\nPara completar el pago de *$29.000 COP*:\n\n🔗 {app_url}/pago/{tutela_id}\n\n⚠️ *Importante:* Radicamos tu tutela y te entregamos el *número de radicado* en máximo *4 horas hábiles* (lun-vie 8am-5pm).` | Confirma pago |
| 54 | Texto | `✅ *¡Recibimos tu confirmación de pago!*\n\nNuestro equipo está verificando el pago. En máximo *4 horas hábiles* (lun-vie 8am-5pm) confirmaremos y te enviaremos el *número de radicado* por este chat.\n\nGracias por confiar en nosotros.` | Usuario dice "pagado" |
| 55 | Texto | `Estamos esperando la confirmación de tu pago. Te avisaremos por este chat.` | Respuesta no válida en espera |

### FASE 12: RADICACIÓN AUTOMÁTICA

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 56 | Texto | `📧 Se envió un código de verificación a tu correo *{email}*.\n\nRevisa tu bandeja de entrada (o spam) y envíame el código por aquí para continuar con la radicación.` | Portal pide código email |
| 57 | Texto | `✅ *¡Tu tutela fue radicada exitosamente!*\n\nNúmero de radicado: *{num_radicado}*\n\nTu tutela ya está en la Rama Judicial. Te enviaremos actualizaciones sobre el caso por este chat.` | Radicación completada |
| 58 | Imagen | Envía screenshot de constancia (sin caption) | Después de #57 |

### FASE 13: COMPLETADO

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 59 | Texto | `✅ *Tutela completada.*\n\nSi necesitas ayuda con otra tutela, escribe *Hola*.` | Cualquier mensaje en estado completado |

### FASE 14: ACCIONES ADMIN (vía panel)

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 60 | Texto | `✅ *¡Pago confirmado!*\n\nNuestro equipo radicará tu tutela y te enviaremos el *número de radicado* por este chat en máximo *4 horas hábiles* (lun-vie 8am-5pm).` | Admin confirma pago |
| 61 | Texto | `✅ *¡Tutela radicada!*\n\nN° radicado: *{num_radicado}*\n\nGracias por usar nuestro servicio.` | Admin registra radicado |
| 62 | Imagen | Envía constancia con caption `📄 *Constancia de radicación*\nN° radicado: {num_radicado}` | Después de #61 |

### FASE 15: WEBHOOK MERCADO PAGO

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 63 | Texto | `✅ *¡Pago confirmado!* Recibimos tu pago de $29.000 COP.\n\nNuestro equipo radicará tu tutela y te enviaremos el *número de radicado* por este chat en máximo *4 horas hábiles* (lun-vie 8am-5pm).` | Webhook MP confirma pago aprobado |
| 64 | Texto | `✅ *¡Pago confirmado!* Nuestro equipo radicará tu tutela y te enviaré el número de radicado por este chat en máximo *4 horas hábiles* (lun-vie 8am-5pm).` | Verificación manual de pago |

### FASE 16: MENSAJES DE USUARIO EXISTENTE

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 65 | Texto | `⏳ *Código recibido.* Continuando con la radicación...` | "Hola" con código válido |
| 66 | Texto | `✅ *Código verificado.* Radicando tu tutela...` | Verificación exitosa |
| 67 | Texto | `❌ *Error:* {resultado.get('error', 'No se pudo completar')}` | Verificación falló |
| 68 | Texto | `🔑 El código debe tener 4 a 6 dígitos. Revísalo en tu correo y envíamelo de nuevo.` | Formato de código inválido |

### FASE 17: ELIMINAR DATOS

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 69 | Texto | `🗑️ *Tus datos han sido eliminados.*\n\nSi necesitas ayuda en el futuro, escribe *Hola* y empezamos de nuevo.` | "eliminar mis datos" |

### FASE 18: MENÚ POR DEFECTO

| # | Tipo | Mensaje | Trigger |
|---|------|---------|---------|
| 70 | Texto | `🤖 *Asistente TutelApp*\n\nComandos:\n• *Hola* — iniciar o continuar\n• *Eliminar mis datos* — borrar tu información` | Mensaje no reconocido |

---

## ANÁLISIS DE POLÍTICAS DE META WHATSAPP BUSINESS <a name="politicas"></a>

### ✅ CUMPLE

| Política | Cómo cumple TutelApp |
|----------|---------------------|
| **Consentimiento explícito** | Aviso de privacidad con botones "Acepto"/"No" antes de recolectar datos |
| **Derecho a eliminación** | Comando "Eliminar mis datos" que borra toda la información |
| **Transparencia** | El bot se identifica como "asistente de TutelApp" en el primer mensaje |
| **Política de privacidad** | Link a página de privacidad en el aviso de datos |
| **Ley 1581 de 2012** | Referencia explícita a la ley colombiana de protección de datos |
| **Finalidad declarada** | Indica claramente que los datos son para "Gestionar, crear y radicar tu acción de tutela" |
| **No contenido spam** | Mensajes transaccionales, no promocionales |
| **No contenido prohibido** | No hay violencia, contenido sexual, odio, ni información falsa |
| **Servicio legítimo** | Servicio de asistencia legal real con valor tangible |
| **Horario de contacto** | Indica "lun-vie 8am-5pm" en mensajes de servicio |
| **Valor claro** | Precio visible ($29.000 COP) antes del pago |
| **Opción de no pago** | "Hazlo tú mismo" GRATIS siempre disponible |
| **No automatización engañosa** | El bot se presenta como asistente, no como humano |

### ⚠️ ÁREAS DE RIESGO

| # | Riesgo | Política de Meta | Detalle | Severidad |
|---|--------|-----------------|---------|-----------|
| 1 | **Solicitud de datos sensibles (historia clínica)** | Messaging Policy §2.1: No solicitar datos médicos sensibles por chat | El paso 17 dice "historia clínica" en el aviso de privacidad. El bot pide "pruebas" que pueden incluir información médica. Meta puede considerar esto como solicitud de datos sensibles. | **ALTA** |
| 2 | **URL de privacidad incorrecta** | Business Policy: Links must be functional | El link en el mensaje #2 apunta a `tutela-online-production.up.railway.app/privacidad` (Railway, dominio viejo). El deploy actual está en Render. El link puede estar roto. | **MEDIA** |
| 3 | **Pago fuera de WhatsApp** | Commerce Policy: Transactions should use WhatsApp Pay when available | El pago se hace vía link externo (Mercado Pago), no dentro de WhatsApp. Meta prefiere transacciones dentro de la plataforma. | **BAJA** |
| 4 | **Mensajes fuera de ventana de 24h** | WhatsApp Business Policy: Templates needed for non-conversation messages | Si el equipo envía notificaciones fuera de la ventana de 24h del usuario (ej: "pago confirmado", "radicado listo"), necesita usar Messages Templates aprobados. Actualmente usa `enviar_texto()` directo. | **ALTA** |
| 5 | **No hay opción de opt-out clara** | Messaging Policy: Users must be able to opt out | Solo existe "Eliminar mis datos". No hay "Detener mensajes" o "No me molesten". Meta requiere una forma clara de opt-out. | **MEDIA** |
| 6 | **Botón "No" no cancela completamente** | Business Policy: Rejection must be respected | Si el usuario dice "No" al consentimiento, el bot dice "escribe Hola" para empezar de nuevo. Esto permite re-intentar, lo cual podría verse como no respetar el rechazo. | **BAJA** |
| 7 | **Mensajes de radicación automática sin template** | WhatsApp Business Policy §2.2: Business-initiated messages require templates | Los mensajes #56 (código email), #57 (radicada), #60-64 (pago/radicación) son iniciados por el negocio fuera de una conversación activa. Necesitan Messages Templates aprobados. | **ALTA** |
| 8 | **Precio puede cambiar sin aviso** | Commerce Policy: Prices must be accurate | $29.000 COP está hardcodeado. Si el precio cambia, todos los mensajes deben actualizarse. | **BAJA** |

---

## RESUMEN DE RIESGOS <a name="riesgos"></a>

| Severidad | Cantidad | Acción requerida |
|-----------|----------|------------------|
| 🔴 ALTA | 3 | Corregir antes de escalar |
| 🟡 MEDIA | 2 | Corregir pronto |
| 🟢 BAJA | 3 | Monitorear |

### 🔴 RIESGOS ALTOS (corregir ya)

**1. Datos sensibles (historia clínica)**
- El bot pide "historia clínica" y "pruebas médicas" por WhatsApp
- Meta prohíbe solicitar datos médicos sensibles por messenger
- **Solución:** No mencionar "historia clínica" explícitamente. Usar "información relevante para tu caso" o "documentos de soporte".

**2. Messages Templates para mensajes fuera de ventana**
- Cuando el pago se confirma o la tutela se radica, el equipo envía mensajes proactivamente
- Si el usuario no ha escrito en las últimas 24h, Meta bloquea el mensaje
- **Solución:** Crear y aprobar Messages Templates para: confirmación de pago, número de radicado, código de verificación

**3. Opt-out insuficiente**
- Solo "Eliminar mis datos" existe
- Meta requiere "Detener" o "No" como opt-out claro
- **Solución:** Agregar comandos "Detener", "No me molesten", "Pausar"

### 🟡 RIESGOS MEDIOS (corregir pronto)

**4. URL de privacidad rota**
- Link apunta a dominio viejo (Railway)
- **Solución:** Cambiar a `https://tutela-online.onrender.com/privacidad`

**5. Rechazo no se respeta permanentemente**
- Si el usuario rechaza, puede reintentar con "Hola"
- **Solución:** Aceptar como válido (el usuario puede cambiar de opinión)

---

## RECOMENDACIONES <a name="recomendaciones"></a>

### Inmediatas (antes de escalar)

1. **Cambiar "historia clínica"** por "información relevante" en el aviso de privacidad
2. **Crear Messages Templates** para mensajes proactivos:
   - Pago confirmado
   - Número de radicado
   - Código de verificación de email
3. **Agregar comandos de opt-out:** "Detener", "No me molesten", "Pausar"
4. **Corregir URL de privacidad** a `https://tutela-online.onrender.com/privacidad`

### A mediano plazo

5. **Usar Messages Templates** para TODOS los mensajes iniciados por el negocio
6. **Implementar ventana de 24h** — trackear cuándo fue el último mensaje del usuario
7. **Agregar mensaje de bienvenida** cuando el usuario reanuda después de 24h
8. **_AUDIT de contenido_** — Revisar cada mensaje contra la lista completa de Messaging Policy

---

## REFERENCIAS

- [WhatsApp Business Policy](https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates/)
- [WhatsApp Messaging Policy](https://www.whatsapp.com/legal/business-policy/)
- [WhatsApp Commerce Policy](https://www.whatsapp.com/legal/commerce-policy/)
- [Ley 1581 de 2012 (Colombia)](https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=44908)
- [Decreto 1377 de 2013 (Colombia)](https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=52997)
