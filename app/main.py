import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.api.admin import NoAuthRedirect
from app.api.admin import router as admin_router
from app.api.health import router as health_router
from app.api.pagos import router as pagos_router
from app.api.tutelas import router as tutelas_router
from app.api.webhook_whatsapp import router as whatsapp_router
from app.bot.browser import BrowserManager
from app.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.tasks.scheduler import iniciar_scheduler
    iniciar_scheduler()
    yield
    await BrowserManager.close()


app = FastAPI(
    title="Tutelas Online AI",
    description="API para radicación automática de tutelas vía WhatsApp",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(NoAuthRedirect)
async def _no_auth_handler(request: Request, exc: NoAuthRedirect):
    if request.url.path.startswith("/admin/api"):
        return JSONResponse({"error": "No autorizado"}, status_code=401)
    return RedirectResponse("/admin/login", status_code=303)

app.include_router(admin_router)
app.include_router(health_router)
app.include_router(whatsapp_router)
app.include_router(tutelas_router)
app.include_router(pagos_router)


@app.get("/", response_class=HTMLResponse)
async def pagina_inicio():
    return """<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tutelas Online</title>
<style>
  body { font-family: Arial, sans-serif; margin:0; padding:0; color:#222; background:#f6f8fa; }
  .hero { background:#1a5fb4; color:#fff; padding:60px 20px; text-align:center; }
  .hero h1 { margin:0 0 10px; } .hero p { font-size:18px; opacity:.9; }
  .container { max-width:720px; margin:0 auto; padding:24px 20px 60px; }
  .card { background:#fff; border:1px solid #e3e8ef; border-radius:12px; padding:24px; margin:20px 0; }
  .btn { display:inline-block; background:#2ecc71; color:#fff; padding:14px 28px;
         border-radius:8px; text-decoration:none; font-weight:bold; }
</style></head><body>
<div class="hero"><h1>Tutelas Online</h1>
<p>Radicamos tu acción de tutela ante la Rama Judicial</p></div>
<div class="container">
  <div class="card">
    <h2>¿Cómo funciona?</h2>
    <ol>
      <li>Escríbenos por WhatsApp.</li>
      <li>Cuéntanos tu caso con tus datos.</li>
      <li>Generamos tu tutela en PDF.</li>
      <li>Radicamos ante el juzgado y te entregamos el número de radicado.</li>
    </ol>
    <p><a class="btn" href="https://wa.me/" target="_blank" rel="noopener">Iniciar por WhatsApp</a></p>
  </div>
</div></body></html>"""
