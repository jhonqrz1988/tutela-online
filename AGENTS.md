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
1. `borrador` - Initial state on creation
2. `recogiendo_datos` - Collect personal info (8 steps)
3. `narracion` - User tells their case
4. `confirmar_audio` - Confirm transcribed audio
5. `revision_datos` - Review AI-extracted data before proceeding
6. `pruebas_pendiente` - Ask to attach evidence
7. `recibiendo_pruebas` - Receive attachments
8. `datos_listos` - Show summary + get juramento
9. `pdf_generado` - PDF generated (transient, goes to next)
10. `esperando_decision_radicacion` - Awaiting radicacion result
11. `confirmar_pago` - Payment flow
12. `esperando_pago` - Payment link sent, awaiting user confirmation
13. `pago_por_confirmar` - User reported payment, human verifies in admin
14. `pago_confirmado` - Payment confirmed, awaiting manual radicacion by team
15. `radicada` - Radicado number registered from admin panel
16. `completado` - Done
17. `pendiente_radicacion` - Retry queued (Reintentar or nightly job)
18. `fallida` - Radicacion attempt failed

## Payment Flow (Wompi Checkout + manual radicacion)
- Bot sends `{app_url}/pago/{tutela_id}` → endpoint `app/api/pagos.py` builds the Wompi hosted checkout URL via `url_checkout()` (reference `TUT-{id}`) and redirects (302) — NO `POST /transactions` (that flow requires card tokenization + acceptance_token and is NOT used)
- Checkout URL: `https://checkout.wompi.co/p/?public-key=...&currency=COP&amount-in-cents=...&reference=TUT-{id}&redirect-url=...&signature:integrity={firma}`
- Wompi notifies webhook `POST /webhook/wompi` (event `transaction.updated`, status APPROVED) → verifies checksum SHA256 → sets `pago_confirmado`
- If no Wompi configured, `/pago/{id}` shows informational page; user reports "Pagado" → state `pago_por_confirmar`
- Human team verifies payment + does manual radicacion in admin panel:
  - `POST /admin/tutelas/{id}/confirmar-pago` → `pago_confirmado` + WhatsApp to user
  - `POST /admin/tutelas/{id}/registrar-radicado` (form: `num_radicado`) → `radicada` + WhatsApp with number
- Wompi env vars: `WOMPI_PUBLIC_KEY`, `WOMPI_PRIVATE_KEY`, `WOMPI_INTEGRITY_SECRET`, `WOMPI_EVENTS_SECRET`, `WOMPI_ENV`, `WOMPI_AMOUNT_CENTS`, `WOMPI_CURRENCY`
- `firma_integridad()` = SHA256(reference+amount+currency+integrity_secret); `verificar_evento()` validates webhook checksum
- Wompi `redirect-url` MUST be clean (no query string) — Wompi appends its own `?id=<transaction_id>`. `/pago/resultado` must be declared BEFORE `/pago/{tutela_id}` in `pagos.py` (FastAPI route order).

## Admin Panel
- All `/admin` routes require login. Protected via `Depends(require_admin)` in `app/api/admin.py` (signed cookie `tutela_admin`, 12h TTL).
- `ADMIN_PASSWORD` env var (in `app/config.py`); if empty, admin returns 401 "no configurado". Login page at `/admin/login`, logout at `/admin/logout`.
- Unauthenticated HTML GET → 303 to `/admin/login`; unauthenticated API/JSON routes → 401 (handled via `NoAuthRedirect` exception handler in `app/main.py`).
- Panel features: pagination (`?pagina=N`, 50/page), status filter + badge colors for all 18 states, auto-refresh every 30s (paused when modal open), reference/payment link in detail modal, `Reintentar` on `fallida`/`pdf_generado`/`pendiente_radicacion`.
- Jinja2 `env` in `admin.py` uses `select_autoescape` (HTML escaped).

## Key Conventions
- WhatsApp bot flow: `procesar_mensaje()` handles state machine in `webhook_whatsapp.py`
- PDF generation: `generar_pdf(datos, None)` in `documento_service.py` - reads from `datos` dict, NOT from IA-generated text
- Citation verification: `normalizar_referencia()` normalizes legal references, `verificar_citas()` checks against whitelist
- IA calls: All async via `AsyncOpenAI` client, model from `settings.ai_chat_model`
- WhatsApp provider: Configured via `WHATSAPP_PROVIDER` (meta|zapi|twilio|infobip)
