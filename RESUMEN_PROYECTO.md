# Proyecto: Tutelas Online AI

## Descripcion General
Sistema automatizado para radicacion de Acciones de Tutela en Colombia via WhatsApp + IA.
El usuario envia su caso por WhatsApp, la IA extrae los datos, genera el documento PDF legal y lo radica en el portal de la Rama Judicial.

---

## Stack Tecnologico
- Backend: FastAPI (Python 3.12)
- Base de datos: SQLite local / Postgres (Neon) en produccion + SQLAlchemy
- PDF: fpdf2 + PyMuPDF (pymupdf)
- IA: Groq (modelo openai/gpt-oss-120b) via OpenAI SDK
- RPA: Playwright (simulado actualmente en produccion; ver SIMULATE_BOT)
- WhatsApp: Meta Cloud API (numero +57 310 6386975)
- Hosting: Render (https://tutela-online.onrender.com)
- Pagos: Mercado Pago (Checkout Pro, $29.000 COP)
- Captcha: 2Captcha (para radicacion real con bot)
- Repo: github.com/jhonqrz1988/tutela-online

---

## Estado Actual (Septiembre 2026)

### En produccion (Render)
- URL: https://tutela-online.onrender.com
- Deploy: Dockerfile (Python 3.12-slim), `run_server.py` arranca uvicorn en :8000
- Webhook WhatsApp: `https://tutela-online.onrender.com/webhook/meta` (Meta Cloud API)
- WhatsApp: Meta Cloud API (WHATSAPP_PROVIDER=meta)
- IA: Groq con AI_CHAT_MODEL=openai/gpt-oss-120b
- Pagos: Mercado Pago (webhook /webhook/mercadopago)
- SIMULATE_BOT=true — la radicacion real por Playwright esta pendiente de habilitar

### Funcional
- Flujo completo de conversacion WhatsApp:
  - Bienvenida -> consentimiento -> datos personales (8 pasos)
  - Recepcion de relato del caso -> extraccion IA de datos
  - Revision/correccion de datos extraidos
  - Recepcion de fotos/soportes como pruebas
  - Resumen + juramento
  - Generacion de PDF de la tutela (Unicode/DejaVu)
  - Envio del PDF por WhatsApp (Meta documentos/medios)
  - Flujo de pago (Mercado Pago) y radicacion
- Dashboard admin en /admin (login con ADMIN_PASSWORD) — historial, filtros, detalle, reintentar
- Estado de la tutela es una maquina de estados (ver AGENTS.md)
- Verificacion de citas legales contra whitelist

### Estado del bot de radicacion (Playwright)
- `app/bot/` tiene el codigo (browser.py + navegador.py) para llenar el formulario del portal
- En produccion NO esta instalado aún (requirements.txt/Dockerfile sin Playwright) y SIMULATE_BOT=true
- Habilitar conlleva: instalar Playwright+Chromium en el Dockerfile, ENABLE_SCHEDULER=true,
  SIMULATE_BOT=false, y TWOCAPTCHA_API_KEY

---

## Pendientes por Desarrollar

### 1. Habilitar radicacion real (quitar simulacion)
- Instalar Playwright/Chromium en el Dockerfile y requirements.txt
- ENABLE_SCHEDULER=true, SIMULATE_BOT=false en produccion
- Validar el bot en staging antes de apagar la simulacion
- Arreglar jobs.py: sesion cerrada + timezone Bogota (UTC-5)

### 2. Preparacion a produccion (blockers)
- Storage persistente: STORAGE_DIR configurable + volumen en Render (/data)
- Healthcheck /health con chequeo de DB (hoy solo devuelve {"status":"ok"})
- Seguridad: CORS restringido, cookie admin secure=True, CSRF, rate-limit login,
  STRICT_WEBHOOK_FIRMA=true + META_APP_SECRET, SSRF en descarga de pruebas
- Rotar credenciales de git y secretos hardcodeados

### 3. Migraciones/backups de BD
- Hoy solo create_all (sin Alembic) — planear Alembic o pg_dump pre-deploy

### 4. Pruebas automaticas
- Tests unitarios por estado del flujo, jobs, storage (ver tests/ y test_integracion.py)

---

## Arquitectura del Proyecto

```
app/
  api/
    admin.py          - Dashboard admin + autenticacion + constancias
    health.py         - Health check
    tutelas.py        - CRUD tutelas
    webhook_whatsapp.py - Webhook Meta (y legacy Twilio/ZAPI) + flujo conversacion
  bot/
    browser.py        - Administrador de navegador Playwright
    navegador.py      - RadicadorBot (llenar formulario Rama Judicial)
  models/
    user.py, tutela.py, whatsapp.py, radicacion.py, cita.py
  services/
    whatsapp_service.py  - Envio de mensajes (Meta Cloud API; legacy Twilio/ZAPI/Infobip)
    ia_service.py        - IA: extraccion, generacion, transcripcion, vision
    documento_service.py - Generacion PDF con fpdf2
    radicacion_service.py - Orquestador de radicacion
    mercadopago_service.py - Preferencias de pago y verificacion de webhook
    verificacion_service.py - Verificacion de citas legales
  tasks/
    scheduler.py      - APScheduler para radicacion programada
  config.py           - Settings (variables de entorno)
  database.py         - SQLAlchemy engine + session
  main.py             - FastAPI app + routers
```

---

## Flujo Completo (WhatsApp)

1. Usuario envia "Hola" al numero de WhatsApp
2. Bot responde con aviso de privacidad y pide consentimiento
3. Usuario acepta y el bot recoge sus datos personales (8 pasos)
4. Bot pide relatar el caso
5. IA extrae datos automaticamente y los muestra para revisar/corregir
6. Bot pide adjuntar pruebas (fotos/documentos)
7. Bot muestra resumen y pide juramento
8. Bot genera el PDF de la tutela
9. Flujo de pago (Mercado Pago) — el usuario paga y el equipo radica
10. [PENDIENTE] Radicacion real en portal Rama Judicial (hoy SIMULATE_BOT=true)
11. Entrega de numero de radicado y constancia

---

## Variables de Entorno Clave

```
WHATSAPP_PROVIDER=meta
META_ACCESS_TOKEN, META_PHONE_NUMBER_ID, META_VERIFY_TOKEN, META_APP_SECRET
STRICT_WEBHOOK_FIRMA

AI_PROVIDER=groq
AI_API_KEY
AI_CHAT_MODEL=openai/gpt-oss-120b

APP_URL=https://tutela-online.onrender.com
SECRET_KEY, ADMIN_PASSWORD
DATABASE_URL  (sqlite local / postgres Neon en prod)
STORAGE_DIR

MERCADOPAGO_ACCESS_TOKEN, MERCADOPAGO_WEBHOOK_SECRET, MERCADOPAGO_ENV,
MERCADOPAGO_AMOUNT, MERCADOPAGO_CURRENCY

RAMA_JUDICIAL_URL, BROWSER_HEADLESS, SIMULATE_BOT, TWOCAPTCHA_API_KEY, ENABLE_SCHEDULER
```
Ver `.env.example` para la referencia completa.

---

## Modelo de Negocio

| Producto | Precio | Descripción |
|---|---|---|
| PDF tutela + guía para radicar manual | GRATIS | El usuario recibe el PDF y guía para radicar él mismo |
| PDF + Radicación automática completa | $29.000 COP | El bot radica y entrega numero de radicado + constancia |

### Cobro
- **Mercado Pago** (Checkout Pro) con webhook de confirmacion
- Flujo: bot envia link de pago por WhatsApp -> usuario paga -> webhook confirma -> radicacion
