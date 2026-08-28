# Flujo de Conversación WhatsApp — Para Revisión con Meta
## TutelApp Business Account

**Fecha:** 28 de agosto de 2026
**Cuenta:** +57 XXXXXXXXXXX (TutelApp)
**Tipo:** Business Account — Servicio de asistencia legal (tutelas en salud)

---

## RESUMEN EJECUTIVO

| Concepto | Detalle |
|----------|---------|
| **Servicio** | Asistente virtual que ayuda a crear y radicar acciones de tutela en Colombia |
| **Modelo de negocio** | Gratuito para crear tutela. $29.000 COP por radicación automática |
| **Proveedor de pagos** | Mercado Pago (link externo) |
| **Datos recolectados** | Nombre, documento, teléfono, email, ciudad, dirección |
| **Datos sensibles** | No se solicitan datos médicos directamente (solo descripción del caso) |
| **Horario de operación** | 24/7 (bot automático). Radicación: lun-vie 8am-5pm |

---

## FLUJO COMPLETO DE CONVERSACIÓN

Cada paso muestra: **[BOT]** = mensaje del bot, **[USUARIO]** = respuesta del usuario.

Los mensajes marcados con ⚠️ son **iniciados por el bot** y pueden necesitar **Message Template** si están fuera de la ventana de 24h.

---

### SECUENCIA 1: NUEVO USUARIO (inicio completo)

```
[USUARIO]  → "Hola" (o cualquier mensaje)

[BOT]      → 👋 ¡Hola! Soy el asistente de TutelApp.
              Soy una herramienta tecnológica diseñada para ayudarte a redactar
              tu propia acción de tutela. Importante: No soy un abogado ni represento
              a la Rama Judicial. Mi función es facilitarte la creación del documento
              que tú mismo presentarás.
              Comencemos con la autorización de datos.

[BOT]      → 📄 Aviso de Tratamiento de Datos
              En TutelApp protegemos tu información. Para ayudarte con tu tutela,
              trataremos tus datos personales y de salud bajo la Ley 1581 de 2012.
              🔹 Finalidad: Crear y radicar técnicamente tu acción de tutela.
              🔹 Datos Sensibles: Al continuar, autorizas el procesamiento de tu caso médico
                 únicamente para este trámite.
              🔹 Tus Derechos: Puedes actualizar o eliminar tus datos en cualquier momento
                 escribiendo "Eliminar mis datos".
              Consulta nuestra política completa aquí:
              https://tutela-online.onrender.com/privacidad
              ¿Autorizas el tratamiento de tus datos para iniciar?
              [✅ Sí, acepto] [❌ No acepto]

[USUARIO]  → "✅ Sí, acepto"

[BOT]      → ✅ Consentimiento registrado.
              Ahora necesito tus datos personales.

[BOT]      → 👤 Escribe tu nombre completo:
[USUARIO]  → "María García López"

[BOT]      → 🪪 Tipo de documento (CC, CE, Pasaporte):
[USUARIO]  → "CC"

[BOT]      → 🆔 Número de documento (sin puntos):
[USUARIO]  → "1234567890"

[BOT]      → 📱 Teléfono celular:
[USUARIO]  → "3101234567"

[BOT]      → 📧 Correo electrónico (para notificaciones del juzgado):
[USUARIO]  → "maria@email.com"

[BOT]      → 🏙️ ¿En qué ciudad vives?
[USUARIO]  → "Bogotá"

[BOT]      → 📍 Dirección de residencia completa (calle, número, barrio, ciudad):
[USUARIO]  → "Calle 123 #45-67, Barrio Norte, Bogotá"

[BOT]      → 🗺️ Departamento (ej: Cundinamarca, Antioquia):
[USUARIO]  → "Cundinamarca"

[BOT]      → ✅ Datos personales registrados.

[BOT]      → ✍️ Antes de continuar:
              Para ayudarte con tu tutela, necesitaremos procesar información sobre tu situación de salud.
              Al compartir tu caso, audios o documentos, autorizas expresamente a TutelApp
              para tratar estos datos sensibles únicamente con el fin de generar tu documento legal.
              [✅ Entiendo y continúo] [❌ No deseo compartir esto]

[USUARIO]  → "✅ Entiendo y continúo"

[BOT]      → ✍️ Cuéntame tu caso de salud en detalle
              Incluye:
              • Qué EPS te negó el servicio
              • Qué tratamiento, cita o medicamento te negaron
              • Fechas de las negaciones
              • Qué le pides al juez que ordene
              🎤 Puedes enviar un audio contando tu caso.
```

**Ventana de 24h:** ✅ TODOS estos mensajes están dentro de la ventana porque el usuario inició la conversación y responde a cada paso.

---

### SECUENCIA 2: NARRACIÓN DEL CASO (texto)

```
[USUARIO]  → "Mi EPS Sanitas me negó el medicamento Trastuzumab
              para tratamiento de cáncer de mama. Me dijeron que no
              está cubierto por el plan de beneficios. Quiero que el
              juez ordene que me lo entreguen."

[BOT]      → ⚠️ ( procesa con IA, extrae datos )

[BOT]      → 📋 Revisa los datos extraídos de tu caso:
              EPS: EPS Sanitas
              Servicio negado: Medicamento Trastuzumab
              Motivo: No cubierto por plan de beneficios
              Solicitud: Entrega del medicamento
              [✅ Sí, confirmar] [✏️ Corregir]

[USUARIO]  → "✅ Sí, confirmar"

[BOT]      → 📎 ¿Tienes soportes o pruebas para adjuntar?
              Ej: fórmulas médicas, resultados, respuestas de la EPS, pantallazos.
              [📎 Adjuntar pruebas] [⏭️ Sin soportes]
```

---

### SECUENCIA 3: NARRACIÓN DEL CASO (audio)

```
[USUARIO]  → 🎤 (audio de 30 segundos contando su caso)

[BOT]      → 🎤 Transcripción de tu audio:
              "Mi EPS Sanitas me negó el medicamento Trastuzumab
              para tratamiento de cáncer de mama..."
              ¿Es correcto?
              [✅ Sí] [✍️ No, escribir]

[USUARIO]  → "✅ Sí"

[BOT]      → 📋 Revisa los datos extraídos de tu caso:
              (mismo formato que secuencia 2)
```

---

### SECUENCIA 4: PRUEBAS

```
[USUARIO]  → "📎 Adjuntar pruebas"

[BOT]      → 📸 Envía tus soportes
              Puedes enviar fotos o documentos.
              Las analizaré y las incluiré como pruebas en tu tutela.
              Envía tus soportes. Cuando termines, presiona el botón.
              [✅ No tengo más]

[USUARIO]  → 📷 (foto de fórmula médica)

[BOT]      → ✅ Soporte 1 recibido.
              ¿Tienes más soportes o generamos la tutela con los que hay?
              [📎 Enviar otro] [✅ No tengo más]

[USUARIO]  → 📷 (foto de respuesta de EPS)

[BOT]      → ✅ Soporte 2 recibido.
              ¿Tienes más soportes o generamos la tutela con los que hay?
              [📎 Enviar otro] [✅ No tengo más]

[USUARIO]  → "✅ No tengo más"
```

**Ventana de 24h:** ✅ El usuario está enviando archivos activamente.

---

### SECUENCIA 5: RESUMEN Y JURAMENTO

```
[BOT]      → 📋 Resumen de tu tutela
              👤 Nombre: María García López
              🆔 Documento: CC 1234567890
              📧 Email: maria@email.com
              🏙️ Ciudad: Bogotá
              🏛️ Accionado: EPS Sanitas
              📝 Hechos: Mi EPS Sanitas me negó el medicamento
              Trastuzumab para tratamiento de cáncer de mama...

[BOT]      → ⚖️ Juramento
              ¿Afirmas bajo la gravedad de juramento que no has interpuesto
              otra acción de tutela por los mismos hechos y derechos
              ante ningún otro juez?
              [✅ Sí, juro] [❌ No]

[USUARIO]  → "✅ Sí, juro"
```

---

### SECUENCIA 6: GENERACIÓN DE PDF Y OPCIONES

```
[BOT]      → ⏳ Generando tu tutela... Esto puede tardar unos segundos.

[BOT]      → ✅ ¡Tutela generada!
[BOT]      → 📄 (envía archivo PDF: tutela_123.pdf)

[BOT]      → 📄 PDF generado y enviado
              Ahora tienes 2 opciones:
              1️⃣ Procesamiento automático — $29.000 COP
                 Procesamos tu tutela ante la Rama Judicial.
                 Resultado en máximo 4 horas hábiles.
                 Te entregamos el número de seguimiento.
              2️⃣ Hazlo tú mismo — GRATIS
              [💳 Procesamiento $29k] [✍️ Hazlo tú mismo]

[USUARIO]  → "💳 Procesamiento $29k"
```

---

### SECUENCIA 7A: PROCESAMIENTO AUTOMÁTICO (pago)

```
[BOT]      → 💳 Procesamiento automático
              Por $29.000 COP procesamos tu tutela ante la Rama Judicial.
              Incluye:
              ✅ Procesamiento en el portal oficial
              ✅ Número de seguimiento y constancia
              ✅ Resultado en máximo 4 horas hábiles
              ¿Quieres continuar con el pago?
              [✅ Sí, pagar ahora] [❌ No, hazlo yo mismo]

[USUARIO]  → "✅ Sí, pagar ahora"

[BOT]      → 💰 Procesamiento automático
              Para completar el pago de $29.000 COP:
              🔗 https://tutela-online.onrender.com/pago/123
              ⚠️ Importante: Procesamos tu tutela y te entregamos el
              número de seguimiento en máximo 4 horas hábiles (lun-vie 8am-5pm).
```

**Ventana de 24h:** ✅ El usuario está en una conversación activa sobre el pago.

---

### SECUENCIA 7B: HAZLO TÚ MISMO

```
[USUARIO]  → "✍️ Hazlo tú mismo"

[BOT]      → Entendido. Recibiste el PDF de tu tutela por este chat.
              Si al intentarlo lo ves complejo, pulsa el botón
              y lo hacemos por ti por $29.000 COP sin repetir tus datos.
              [💳 Procesen $29k] [✍️ Lo hago yo]

[USUARIO]  → "✍️ Lo hago yo"

[BOT]      → Perfecto. Tu tutela quedó enviada en el PDF de este chat.
              Si cambias de opinión, pulsa el botón y te ayudamos.
```

---

### SECUENCIA 8: PAGO RECIBIDO (Webhook Mercado Pago)

```
              (el usuario paga en Mercado Pago fuera de WhatsApp)

⚠️ [BOT]     → ✅ ¡Pago recibido! Hemos confirmado tu pago por $29.000 COP.
              Nuestro equipo procesará tu solicitud y te notificaremos
              cuando esté lista.
```

⚠️ **ESTE MENSAJE ES INICIADO POR EL BOT** — Puede estar fuera de la ventana de 24h.
**Necesita Message Template aprobado.**

---

### SECUENCIA 9: CÓDIGO DE VERIFICACIÓN

```
⚠️ [BOT]     → 📧 Se envió un código de verificación a tu correo maria@email.com.
              Por favor revisa tu bandeja de entrada o carpeta de spam
              y envíame el código por aquí para continuar con tu trámite.

[USUARIO]  → "456789"

[BOT]      → ⏳ Código recibido. Continuando con la radicación...
              ✅ Código verificado. Procesando tu tutela...
```

⚠️ **EL PRIMER MENSAJE ES INICIADO POR EL BOT** — Puede estar fuera de la ventana de 24h.
**Necesita Message Template aprobado.**

---

### SECUENCIA 10: TRÁMITE FINALIZADO

```
⚠️ [BOT]     → ✅ Tu solicitud ha sido procesada exitosamente.
              Número de seguimiento: 1234567890-1
              Puedes consultar las actualizaciones directamente en este chat.

⚠️ [BOT]     → 📷 (imagen de constancia de radicación)
```

⚠️ **AMBOS MENSAJES SON INICIADOS POR EL BOT** — Pueden estar fuera de la ventana de 24h.
**Necesitan Message Template aprobados.**

---

### SECUENCIA 11: PAGO VERIFICADO (admin manual)

```
⚠️ [BOT]     → ✅ ¡Pago verificado!
              Nuestro equipo procederá con el procesamiento
              de tu solicitud. Te notificaremos cuando esté completa.
```

⚠️ **MENSAJE INICIADO POR EL BOT** — Confirmación de pago verificado manualmente por el equipo.
**Necesita Message Template aprobado.**

---

### SECUENCIA 12: SOLICITUD COMPLETADA (admin manual)

```
⚠️ [BOT]     → ✅ ¡Tu trámite ha sido completado!
              Número de referencia: 1234567890-1
              Gracias por confiar en nosotros.

⚠️ [BOT]     → 📷 Constancia de radicación
              N° radicado: 1234567890-1
              (imagen de constancia)
```

⚠️ **AMBOS MENSAJES SON INICIADOS POR EL BOT** — Pueden estar fuera de la ventana de 24h.
**Necesitan Message Template aprobados.**

---

### SECUENCIA 13: ELIMINAR DATOS

```
[USUARIO]  → "Eliminar mis datos"

[BOT]      → 🗑️ Tus datos han sido eliminados.
              Si necesitas ayuda en el futuro, escribe Hola y empezamos de nuevo.
```

**Ventana de 24h:** ✅ El usuario inició la acción.

---

### SECUENCIA 14: OPT-OUT (NUEVO)

```
[USUARIO]  → "Detener"

[BOT]      → ⏸️ Mensajes pausados.
              Si necesitas ayuda en el futuro, escribe Hola para reanudar.
```

**Ventana de 24h:** ✅ El usuario inició la acción.

---

### SECUENCIA 15: USUARIO EXISTENTE (retorno)

```
[USUARIO]  → "Hola"

[BOT]      → 🤖 TutelApp — Continúa donde lo dejaste.
```

**Ventana de 24h:** ✅ El usuario inició la acción.

---

## MENSAJES QUE NECESITAN MESSAGE TEMPLATE

Estos mensajes son **iniciados por el bot** y pueden enviarse **fuera de la ventana de 24h** del usuario. Meta requiere Message Templates aprobados para cada uno.

### Template 1: Confirmación de Pago Automático

| Campo | Contenido |
|-------|-----------|
| **Nombre** | `confirmacion_pago_tutelapp` |
| **Categoría** | TRANSACTION_UPDATE |
| **Idioma** | es (Español) |
| **Body** | `✅ ¡Pago recibido! Hemos confirmado tu pago por {{1}}. Nuestro equipo técnico ya está trabajando en la generación y radicación de tu documento. Te notificaremos por este medio en cuanto el proceso finalice.` |
| **Variables** | `{{1}}` = monto del pago (ej: "$29.000 COP") |

### Template 2: Solicitud de Código (Verificación de Correo)

| Campo | Contenido |
|-------|-----------|
| **Nombre** | `instruccion_soporte_tutelapp` |
| **Categoría** | TRANSACTION_UPDATE |
| **Idioma** | es (Español) |
| **Body** | `Hola, para que nuestro equipo pueda finalizar tu trámite, necesitamos que nos proporciones el dato que el portal oficial te envió por correo electrónico. Por favor, escríbelo aquí abajo para continuar. ¡Gracias por tu colaboración!` |
| **Variables** | Ninguna |

### Template 3: Entrega de Radicado Automático

| Campo | Contenido |
|-------|-----------|
| **Nombre** | `confirmacion_tramite_finalizado` |
| **Categoría** | TRANSACTION_UPDATE |
| **Idioma** | es (Español) |
| **Body** | `✅ Tu solicitud ha sido procesada exitosamente. El número de seguimiento asignado es {{1}}. Puedes consultar las actualizaciones directamente en este chat.` |
| **Variables** | `{{1}}` = número de radicado |

### Template 4: Confirmación de Pago Manual

| Campo | Contenido |
|-------|-----------|
| **Nombre** | `pago_verificado_manual` |
| **Categoría** | TRANSACTION_UPDATE |
| **Idioma** | es (Español) |
| **Body** | `✅ ¡Pago verificado! Nuestro equipo procederá con el procesamiento de tu solicitud. Te notificaremos cuando esté completa.` |
| **Variables** | Ninguna |

### Template 5: Entrega de Radicado Manual

| Campo | Contenido |
|-------|-----------|
| **Nombre** | `solicitud_completada_manual` |
| **Categoría** | TRANSACTION_UPDATE |
| **Idioma** | es (Español) |
| **Body** | `✅ ¡Tu trámite ha sido completado! Número de referencia: {{1}}. Gracias por confiar en nosotros.` |
| **Variables** | `{{1}}` = número de radicado |

---

## RESUMEN DE VENTANAS DE 24H

| Secuencia | ¿Dentro de 24h? | ¿Necesita Template? |
|-----------|-----------------|---------------------|
| 1-7: Flujo completo (usuario inicia) | ✅ SÍ | NO |
| 8: Pago recibido (webhook) | ⚠️ PROBABLEMENTE NO | **SÍ** |
| 9: Código verificación (bot inicia) | ⚠️ PROBABLEMENTE NO | **SÍ** |
| 10: Trámite finalizado (bot inicia) | ⚠️ PROBABLEMENTE NO | **SÍ** |
| 11: Pago verificado manual (admin) | ⚠️ NO | **SÍ** |
| 12: Solicitud completada manual (admin) | ⚠️ NO | **SÍ** |
| 13-15: Eliminar/Opt-out/Retorno | ✅ SÍ | NO |

---

## POLÍTICAS DE META QUE APLICA TUTELAPP

### ✅ CUMPLIMIENTO CONFIRMADO

| Política | Cómo cumple |
|----------|-------------|
| Consentimiento explícito | Aviso de privacidad con botones "✅ Sí, acepto" / "❌ No acepto" |
| Consentimiento datos sensibles | Menciona "autorizas el procesamiento de tu caso médico" en el aviso |
| Derecho a eliminación | Comando "Eliminar mis datos" recordado en el aviso |
| Transparencia | Bot se identifica como "herramienta tecnológica", no como abogado |
| Disclaimer | Aclara que no es representante de la Rama Judicial |
| Link de privacidad | https://tutela-online.onrender.com/privacidad visible en el aviso |
| No spam | Solo mensajes transaccionales, no promocionales |
| No contenido prohibido | Sin violencia, odio, contenido sexual ni información falsa |
| Opt-out | Comandos "Detener", "Pausar", "No me molesten" |
| No automatización engañosa | Bot se presenta como bot, no como humano |
| Lenguaje neutro | Usa "procesamiento" en vez de "radicación" para evitar alertas |
| Consentimiento SIC | Cumple Ley 1581 de 2012 y Decreto 1377 de 2013 |

### ⚠️ PENDIENTE DE IMPLEMENTAR

| # | Acción | Estado |
|---|--------|--------|
| 1 | Crear 5 Message Templates en Meta Business Suite | **PENDIENTE** |
| 2 | Aprobar templates (proceso de 24-48h de Meta) | **PENDIENTE** |
| 3 | Implementar envío de templates en el código | **PENDIENTE** |
| 4 | Verificar que la imagen de constancia se envía como adjunto del template | **PENDIENTE** |

---

## INSTRUCCIONES PARA CREAR TEMPLATES EN META

1. Ir a **Meta Business Suite** → **WhatsApp** → **Message Templates**
2. Click **Create Template**
3. Seleccionar categoría: **Transaction Update**
4. Para cada template:
   - Copiar el nombre exacto indicado arriba
   - Pegar el body con las variables `{{1}}`, `{{2}}`, etc.
   - Seleccionar idioma: **Español (Latinoamérica)**
   - No agregar botones (son mensajes informativos)
5. Enviar a revisión (toma 24-48h)
6. Una vez aprobados, implementar en el código usando `enviar_template()`

---

## REFERENCIAS

- [Meta Message Templates](https://developers.facebook.com/docs/whatsapp/business-management-api/message-templates/)
- [WhatsApp Business Policy](https://www.whatsapp.com/legal/business-policy/)
- [WhatsApp Commerce Policy](https://www.whatsapp.com/legal/commerce-policy/)
- [Ley 1581 de 2012 (Colombia)](https://www.funcionpublica.gov.co/eva/gestornormativo/norma.php?i=44908)
