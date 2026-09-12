import logging
from datetime import date
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.admin import NoAuthRedirect
from app.api.admin import router as admin_router
from app.api.health import router as health_router
from app.api.pagos import router as pagos_router
from app.api.tutelas import router as tutelas_router
from app.api.webhook_whatsapp import router as whatsapp_router
from app.config import settings
from app.database import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from seed_citas import seed_citas
    seed_citas()
    from app.api.admin import _validar_secret_key_prod
    _validar_secret_key_prod()
    from app.tasks.scheduler import iniciar_scheduler
    iniciar_scheduler()
    yield


def _allowed_origins(app_url: str, cors_origins: str = "") -> list[str]:
    """Construye la lista de orígenes CORS permitidos.

    Solo se permiten orígenes explícitos: ``app_url`` más cualquier origen
    adicional configurado en ``cors_origins`` (separados por coma). Nunca se
    abre a ``*``. Los valores se normalizan recortando espacios y descartando
    entradas vacías.
    """
    origenes: list[str] = []
    for raw in [app_url, *cors_origins.split(",")]:
        valor = raw.strip()
        if valor:
            origenes.append(valor)
    return list(dict.fromkeys(origenes))


def _config_docs(es_produccion: bool) -> dict:
    """Configuración de docs para FastAPI.

    En producción se ocultan Swagger (/docs), ReDoc (/redoc) y el esquema
    OpenAPI (/openapi.json) para no exponer públicamente las rutas internas.
    """
    if es_produccion:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


app = FastAPI(
    title="TutelApp",
    description="API para radicación automática de tutelas vía WhatsApp",
    version="0.1.0",
    lifespan=lifespan,
    **_config_docs(settings.app_url.lower().startswith("https")),
)

# CORS restringido: jamás "*". Permite app_url + orígenes extra de CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins(settings.app_url, settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


app.mount("/static", StaticFiles(directory="app/static"), name="static")


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


_LANDING_HTML = Path("app/templates/landing.html").read_text(encoding="utf-8")
_PRIVACIDAD_HTML = Path("app/templates/privacidad.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def pagina_inicio(request: Request):
    from app.services.visitas_service import registrar_visita_landing
    registrar_visita_landing(str(request.query_params))
    return HTMLResponse(_LANDING_HTML)


@app.get("/privacidad", response_class=HTMLResponse)
async def pagina_privacidad():
    return HTMLResponse(_PRIVACIDAD_HTML)


_GOOGLE_VERIFY_CONTENT = "google-site-verification: google06550146ee012678.html"


@app.get("/google06550146ee012678.html", response_class=Response)
async def google_site_verification():
    return Response(content=_GOOGLE_VERIFY_CONTENT, media_type="text/html")


@app.get("/robots.txt", response_class=Response)
async def robots_txt():
    base = settings.app_url.rstrip("/")
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /pago\n"
        "Disallow: /webhook\n"
        "Disallow: /health\n"
        "\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    return Response(content=body, media_type="text/plain")


@app.get("/sitemap.xml", response_class=Response)
async def sitemap_xml():
    base = settings.app_url.rstrip("/")
    hoy = date.today().isoformat()
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url><loc>{base}/</loc><lastmod>{hoy}</lastmod>"
        "<changefreq>weekly</changefreq><priority>1.0</priority></url>\n"
        f"  <url><loc>{base}/privacidad</loc><lastmod>{hoy}</lastmod>"
        "<changefreq>monthly</changefreq><priority>0.3</priority></url>\n"
        "</urlset>\n"
    )
    return Response(content=body, media_type="application/xml")
