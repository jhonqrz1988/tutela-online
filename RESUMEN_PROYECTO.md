# Proyecto: Tutelas Online AI

## Descripcion General
Sistema automatizado para radicacion de Acciones de Tutela en Colombia via WhatsApp + IA.
El usuario envia su caso por WhatsApp, la IA extrae los datos, genera el documento PDF legal y lo radica en el portal de la Rama Judicial.

---

## Stack Tecnologico
- Backend: FastAPI (Python 3.12)
- Base de datos: SQLite + SQLAlchemy
- PDF: fpdf2
- IA: Groq (modelo llama-3.3-70b-versatile) via OpenAI SDK
- RPA: Playwright (simulado actualmente)
- WhatsApp: Twilio Sandbox (activo)
- Hosting: Railway (tutela-online-production.up.railway.app)
- Repo: github.com/jhonqrz1988/tutela-online

---

## Estado Actual (Julio 2026)

### Funcional
- Chat web de prueba en /admin/chat
- Flujo completo de conversacion WhatsApp:
  - Bienvenida -> seleccion tipo tutela (salud/fotomultas/derecho peticion)
  - Recepcion de relato del caso -> extraccion IA de datos
  - Pregunta campos faltantes uno por uno
  - Resumen y confirmacion
  - Juramento
  - Recepcion de fotos/soportes como pruebas
  - Analisis de imagenes con IA vision
  - Generacion de PDF de la tutela
  - Envio del PDF por WhatsApp
  - Radicacion en Rama Judicial (en simulacion)
- Dashboard admin en /admin con historial, filtros, detalle, reintentar
- 3 tipos de tutela: salud, fotomultas, derecho de peticion
- Adaptacion de genero (masculino/femenino) en el texto legal
- Validacion de campos (cedula solo numeros, email valido, telefono 10+ digitos)
- Twilio Sandbox conectado y funcional

### En produccion (Railway)
- URL: https://tutela-online-production.up.railway.app
- Variables de entorno configuradas:
  - WHATSAPP_PROVIDER=twilio
  - TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER
  - AI_PROVIDER=groq, AI_API_KEY, AI_CHAT_MODEL
  - APP_URL, SECRET_KEY, DATABASE_URL
  - SIMULATE_BOT=true (simulando conexion a Rama Judicial)

---

## Pendientes por Desarrollar

### 1. Mejorar flujo de Juramento
- Cambiar de texto libre a opciones numeradas:
  "1. Si, juro / 2. No"
- Validar solo esas respuestas

### 2. Paso de confirmacion antes de radicar
- Actual: al enviar foto -> genera PDF -> envia PDF -> radica automaticamente
- Deseado: generar PDF -> enviar PDF al usuario -> preguntar "¿Deseas radicar la tutela? 1. Si / 2. No" -> solo si confirma, radicar

### 3. PDF no se envia por Twilio
- El PDF se genera pero no llega al WhatsApp del usuario
- Se necesita: verificar endpoint /admin/tutelas/{id}/pdf, probar con URL pública
- Alternativa: subir PDF a servicio externo (tmpfiles.org, 0x0.st, etc.)

### 4. Notas de voz
- El codigo ya detecta audios y tiene funcion transcribir_audio()
- Falta conectar la transcripcion al flujo de la conversacion
- Cuando el usuario envie audio, transcribirlo y procesar el texto resultante

### 5. Radicacion real (quitar simulacion)
- Actual: SIMULATE_BOT=true genera numero falso
- Pendiente: conectar con portal real de Rama Judicial
- Requiere: analizar selectores del portal, manejar CAPTCHA, tokens de verificacion

### 6. Migrar de Twilio Sandbox a produccion
- Twilio Sandbox: limite 5 numeros, solo para desarrollo
- Produccion: WATI, 360dialog, o Twilio numero real
- Requiere: nuevo numero de telefono no bloqueado por Meta

### 7. Pruebas automaticas
- Tests unitarios para: extraccion IA, generacion PDF, validacion campos
- Tests de integracion para: flujo completo webhook

### 8. Mejoras UI/UX
- Dashboard: boton para ver PDF en linea (ya existe endpoint)
- Dashboard: mostrar estado de envio WhatsApp
- Dashboard: historial de mensajes por tutela

---

## Arquitectura del Proyecto

app/
  api/
    admin.py          - Dashboard admin + chat web + descarga PDF
    health.py         - Health check
    tutelas.py        - CRUD tutelas
    webhook_whatsapp.py - Webhook Twilio + Meta + ZAPI + flujo conversacion
  bot/
    browser.py        - Administrador de navegador Playwright
    navegador.py      - RadicadorBot (llenar formulario Rama Judicial)
  models/
    user.py           - Usuario
    tutela.py         - Tutela
    whatsapp.py       - MensajeWhatsApp
    radicacion.py     - Radicacion
  services/
    whatsapp_service.py - Envio de mensajes (Twilio/Meta/ZAPI)
    ia_service.py     - IA: extraccion, generacion tutela, transcripcion, vision
    documento_service.py - Generacion PDF con fpdf2
    radicacion_service.py - Orquestador de radicacion
  tasks/
    scheduler.py      - APScheduler para radicacion programada
  config.py           - Settings (variables de entorno)
  database.py         - SQLAlchemy engine + session
  main.py             - FastAPI app + routers

---

## Flujo Completo (WhatsApp)

1. Usuario envia "Hola" al numero de Twilio
2. Bot responde con menu: 1. Salud / 2. Fotomultas / 3. Derecho Peticion
3. Usuario selecciona tipo
4. Bot pide: "Cuentame tu caso en detalle"
5. Usuario narra los hechos
6. IA extrae datos automaticamente (nombre, cedula, ciudad, entidad, etc.)
7. Si faltan campos, bot pregunta uno por uno
8. Bot muestra resumen y pide confirmacion "Es correcto? (SI/NO)"
9. Usuario confirma
10. Bot pide juramento "Afirmas bajo juramento que no has interpuesto otra tutela?"
11. Usuario jura
12. Bot pide enviar fotos de soportes (cedula, formulas, etc.)
13. Usuario envia fotos
14. IA analiza las imagenes
15. Bot genera PDF de la tutela
16. Bot envia PDF por WhatsApp
17. [PENDIENTE] Bot pregunta "Deseas radicar? 1. Si / 2. No"
18. Bot radica en portal Rama Judicial
19. Bot envia numero de radicado y constancia

---

## Variables de Entorno

WHATSAPP_PROVIDER=twilio
TWILIO_ACCOUNT_SID=****
TWILIO_AUTH_TOKEN=****
TWILIO_WHATSAPP_NUMBER=+14155238886

AI_PROVIDER=groq
AI_API_KEY=****
AI_CHAT_MODEL=llama-3.3-70b-versatile

APP_URL=https://tutela-online-production.up.railway.app
SECRET_KEY=****
DATABASE_URL=sqlite:///./storage/tutelas.db
SIMULATE_BOT=true