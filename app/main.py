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
<meta name="description" content="Servicios de consultoria juridica y radicacion de acciones de tutela ante la Rama Judicial de Colombia.">
<title>Tutelas Online — Consultoria Juridica</title>
<style>
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family: Arial, Helvetica, sans-serif; color:#222; background:#fff; line-height:1.6; }
  .topbar { background:#0d3b8a; color:#fff; padding:12px 24px; font-size:14px; text-align:center; }
  nav { display:flex; justify-content:space-between; align-items:center; padding:16px 40px;
        border-bottom:1px solid #eee; }
  nav .logo { font-weight:bold; font-size:20px; color:#0d3b8a; }
  nav a { margin-left:20px; color:#333; text-decoration:none; font-size:14px; }
  .hero { background:linear-gradient(135deg,#0d3b8a,#1a5fb4); color:#fff; padding:70px 24px; text-align:center; }
  .hero h1 { font-size:38px; margin-bottom:14px; }
  .hero p { font-size:18px; max-width:680px; margin:0 auto 26px; opacity:.95; }
  .btn { display:inline-block; background:#2ecc71; color:#fff; padding:15px 34px; border-radius:8px;
         text-decoration:none; font-weight:bold; font-size:16px; }
  .container { max-width:980px; margin:0 auto; padding:44px 24px; }
  .sec { margin-bottom:40px; }
  .sec h2 { color:#0d3b8a; font-size:26px; margin-bottom:14px; }
  .sec p { font-size:16px; max-width:760px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:20px; margin-top:20px; }
  .card { background:#f5f8fc; border:1px solid #e3e8ef; border-radius:12px; padding:22px; }
  .card h3 { color:#0d3b8a; font-size:18px; margin-bottom:8px; }
  .card p { font-size:14px; color:#444; }
  ul.steps { margin:18px 0 0 20px; }
  ul.steps li { margin-bottom:8px; }
  footer { background:#f2f3f5; text-align:center; padding:26px; font-size:13px; color:#666; }
</style></head><body>
  <div class="topbar">Asesoria juridica · Radicacion de tutelas · Respuesta en 4 horas habiles</div>
  <nav>
    <span class="logo">Tutelas Online</span>
    <span>
      <a href="#servicios">Servicios</a>
      <a href="#como-funciona">Como funciona</a>
      <a href="#preguntas">Preguntas</a>
      <a href="#contacto">Contacto</a>
    </span>
  </nav>
  <div class="hero">
    <h1>Consultoria juridica y radicacion de tutelas</h1>
    <p>Acompañamiento profesional para proteger tus derechos: preparamos tu accion de tutela,
       la radicamos ante la Rama Judicial y te entregamos el numero de radicado.</p>
    <a class="btn" href="https://wa.me/" target="_blank" rel="noopener">Consultar por WhatsApp</a>
  </div>
  <div class="container">
    <div class="sec" id="servicios">
      <h2>Nuestros servicios</h2>
      <div class="grid">
        <div class="card"><h3>Radicacion de tutela</h3>
          <p>Preparamos y radicamos tu accion de tutela ante el juzgado competente.</p></div>
        <div class="card"><h3>Consultoria legal</h3>
          <p>Orientacion sobre tus derechos y las medidas para protegerlos.</p></div>
        <div class="card"><h3>Seguimiento del caso</h3>
          <p>Acompañamiento hasta la respuesta del juzgado y la constancia.</p></div>
      </div>
    </div>
    <div class="sec" id="como-funciona">
      <h2>Como funciona</h2>
      <ul class="steps">
        <li>1. Escribenos por WhatsApp y cuentanos tu caso.</li>
        <li>2. Completamos los datos de la solicitud contigo.</li>
        <li>3. Generamos la tutela y la radicamos ante la Rama Judicial.</li>
        <li>4. Recibes el numero de radicado por WhatsApp.</li>
      </ul>
    </div>
    <div class="sec" id="preguntas">
      <h2>Preguntas frecuentes</h2>
      <div class="card"><h3>¿Cuanto toma el proceso?</h3>
        <p>La tutela se radica dentro de las 4 horas habiles siguientes a la confirmacion del pago.</p></div>
      <div class="card"><h3>¿Que necesito?</h3>
        <p>Tu documento de identidad, correo, telefono y la narracion de los hechos que quieres proteger.</p></div>
    </div>
    <div class="sec" id="contacto">
      <h2>Contacto</h2>
      <p>Escibenos por WhatsApp y uno de nuestros asesores te atendra de lunes a viernes de 8am a 5pm.</p>
      <p style="margin-top:14px"><a class="btn" href="https://wa.me/" target="_blank" rel="noopener">Iniciar conversacion</a></p>
    </div>
  </div>
  <footer>Tutelas Online &copy; 2026 · Servicios de consultoria e intermediacion juridica.</footer>
</body></html>"""
