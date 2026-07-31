# AGENTS.md - Project Instructions

## Environment Setup
- Python venv: `.\.venv\Scripts\python.exe`
- Activate: `.\.venv\Scripts\activate`

## Key Commands
- Compile check: `python -m py_compile <file>.py`
- Lint: `python -m ruff check app/ seed_citas.py`
- Run integration test: `python test_integracion.py`
- Run seed: `python seed_citas.py`
- Start dev server: `uvicorn app.main:app --reload`
- Start dev server: `python -m uvicorn app.main:app --reload`

## Database
- SQLite DB: `storage/tutelas.db`
- DB init: Called automatically via `init_db()` in `app/database.py`
- Session: `SessionLocal()` (sync SQLAlchemy)
- Tables: `User`, `Tutela`, `MensajeWhatsApp`, `CitaLegal`, `CitaPendiente`, `Radicacion`

## Project Structure
- `app/api/` - FastAPI routers (webhook_whatsapp, admin, tutelas, health)
- `app/services/` - Business logic (ia_service, documento_service, verificacion_service, whatsapp_service, radicacion_service)
- `app/models/` - SQLAlchemy models
- `app/bot/` - Playwright browser automation for judicial portal
- `app/tasks/` - APScheduler for nightly radicacion
- `storage/` - Generated PDFs and downloaded proofs
- `seed_citas.py` - Seeds legal citation whitelist (10 citas)

## Critical State Machine (Tutela.estado)
1. `recogiendo_datos` - Collect personal info (8 steps)
2. `narracion` - User tells their case
3. `confirmar_audio` - Confirm transcribed audio
4. `revision_datos` - **NEW** Review AI-extracted data before proceeding
5. `pruebas_pendiente` - Ask to attach evidence
6. `recibiendo_pruebas` - Receive attachments
7. `datos_listos` - Show summary + get juramento
8. `pdf_generado` - PDF generated
9. `pendiente_radicacion` - Queued for radicacion
10. `esperando_decision_radicacion` - Awaiting radicacion result
11. `confirmar_pago` / `esperando_pago` - Payment flow
12. `completado` - Done

## Key Conventions
- WhatsApp bot flow: `procesar_mensaje()` handles state machine in `webhook_whatsapp.py`
- PDF generation: `generar_pdf(datos, None)` in `documento_service.py` - reads from `datos` dict, NOT from IA-generated text
- Citation verification: `normalizar_referencia()` normalizes legal references, `verificar_citas()` checks against whitelist
- IA calls: All async via `AsyncOpenAI` client, model from `settings.ai_chat_model`
- WhatsApp provider: Configured via `WHATSAPP_PROVIDER` (meta|zapi|twilio|infobip)
