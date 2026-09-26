import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import delete, func, select

from app.config import settings
from app.database import get_session
from app.models.radicacion import PasoRadicacion, Radicacion
from app.models.clic import ClicWhatsApp
from app.models.tutela import Tutela
from app.models.visita import VisitaLanding
from app.services.visitas_service import (
    agrupar_por_periodo,
    agrupar_tutelas_por_periodo,
    contar_conversaciones,
    nombre_fuente,
    rango_mes_utc,
    visitas_clasificadas,
    visitas_legacy,
)

router = APIRouter(prefix="/admin")
logger = logging.getLogger(__name__)

BOGOTA_TZ = ZoneInfo("America/Bogota")
env = Environment(
    loader=FileSystemLoader("app/templates"),
    cache_size=0,
    autoescape=select_autoescape(["html", "htm"]),
)


def _nombre_mes(clave: str) -> str:
    """Traduce ``2026-09`` a ``Septiembre 2026`` (para el selector del panel)."""
    try:
        year, month = clave.split("-")
        return f"{_MESES_ES[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return clave


# Globals del template: el helper del título del selector.
env.globals["_nombre_mes"] = _nombre_mes

SESSION_COOKIE = "tutela_admin"
SESSION_TTL = 12 * 3600  # 12 horas
CSRF_COOKIE = "tutela_admin_csrf"


def _fecha_bogota(dt) -> str:
    """Convierte un datetime naive guardado como UTC a hora de Bogotá (UTC-5).

    Las fechas se persisten con func.now() (UTC). El panel las muestra en
    hora local de Colombia para que coincidan con el horario de radicación.
    """
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BOGOTA_TZ).strftime("%Y-%m-%d %H:%M")

# Etiquetas cortas para el resumen de pasos del bot en la tabla del panel.
_ETIQUETA_PASO = {
    "iniciar_bot": "Inicio",
    "navegar_portal": "Portal",
    "llenar_formulario": "Formulario",
    "esperando_codigo_email": "Código email",
    "completar_formulario": "Pasos 5-8",
    "resolver_captcha": "reCAPTCHA",
    "enviar_y_descargar": "Enviar",
    "radicada": "Radicada",
    "completar_radicacion": "Completar",
}

_MESES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]


def _nombre_mes_activo(clave: str) -> str:
    """Traduce ``2026-09`` a ``Septiembre 2026``."""
    try:
        year, month = clave.split("-")
        return f"{_MESES_ES[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return clave

# Rate-limit del login: máx intentos fallidos por ventana por IP.
_LOGIN_MAX_ATTEMPTS = 8
_LOGIN_WINDOW_SECS = 15 * 60  # 15 minutos
_contador_login: dict[str, list[float]] = {}
_limite_login = _LOGIN_MAX_ATTEMPTS


def _ip_cliente(request: Request) -> str:
    # Render y proxies inversos; usa X-Forwarded-For si está presente.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _secret_key_valida(key: str) -> bool:
    # Clave segura de al menos 32 chars sin espacios, distinta del default.
    return bool(key) and len(key) >= 32 and " " not in key


def _validar_secret_key_prod():
    """Advierte en el log si la SECRET_KEY es insegura en producción."""
    if settings.app_url.lower().startswith("https") and not _secret_key_valida(settings.secret_key):
        logger.warning(
            "SECRET_KEY insegura en producción: defina una clave de >=32 chars "
            "vía la variable de entorno SECRET_KEY (actual se regenera en cada arranque)."
        )


def _intentos_recientes(ip: str) -> int:
    ahora = time.time()
    lista = [t for t in _contador_login.get(ip, []) if ahora - t < _LOGIN_WINDOW_SECS]
    _contador_login[ip] = lista
    return len(lista)


def _registrar_intento(ip: str):
    _contador_login.setdefault(ip, []).append(time.time())


def _login_bloqueado(ip: str) -> bool:
    return _intentos_recientes(ip) >= _LOGIN_MAX_ATTEMPTS


_LOGIN_HTML = """<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><title>Admin - TutelApp</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background:#1a237e; display:flex; align-items:center; justify-content:center; min-height:100vh; margin:0; }
  .card { background:#fff; border-radius:12px; padding:32px; width:320px; box-shadow:0 8px 30px rgba(0,0,0,.3); }
  h1 { font-size:18px; color:#1a237e; margin:0 0 4px; }
  p { font-size:13px; color:#666; margin:0 0 20px; }
  input { width:100%; padding:10px; border:1px solid #ddd; border-radius:6px; font-size:14px; box-sizing:border-box; }
  button { width:100%; padding:11px; background:#1a237e; color:#fff; border:none; border-radius:6px;
           font-size:14px; font-weight:600; cursor:pointer; margin-top:16px; }
  button:hover { background:#283593; }
</style></head>
<body><div class="card">
  <h1>&#x2696;&#xFE0F; TutelApp</h1>
  <p>Panel de administración</p>
  <!--ERROR-->
  <form method="POST" action="/admin/login">
    <!--CSRF-->
    <input type="password" name="password" placeholder="Contraseña" autofocus required>
    <button type="submit">Ingresar</button>
  </form>
</div></body></html>"""


def _firma_cookie(payload: str) -> str:
    return hmac.new(settings.secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _crear_sesion() -> str:
    payload = f"{int(time.time()) + SESSION_TTL}:{secrets.token_hex(16)}"
    return f"{payload}.{_firma_cookie(payload)}"


def _validar_sesion(token: str | None) -> bool:
    if not token:
        return False
    try:
        payload, firma = token.rsplit(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(_firma_cookie(payload), firma):
        return False
    try:
        exp = int(payload.split(":", 1)[0])
    except ValueError:
        return False
    return time.time() < exp


class NoAuthRedirect(Exception):
    """Excepción interna para redirigir a /admin/login cuando no hay sesión."""


def require_admin(request: Request):
    """Dependencia que protege el panel. Redirige a /admin/login si no hay sesión."""
    if not settings.admin_password:
        raise HTTPException(401, "ADMIN_PASSWORD no está configurado")
    if not _validar_sesion(request.cookies.get(SESSION_COOKIE)):
        raise NoAuthRedirect()
    return True


@router.get("/login", response_class=HTMLResponse)
def admin_login(request: Request):
    resp = HTMLResponse(_LOGIN_HTML)
    return _establecer_csrf(resp)


def _establecer_csrf(resp):
    token = secrets.token_urlsafe(32)
    html = _LOGIN_HTML.replace(
        "<!--CSRF-->",
        f'<input type="hidden" name="csrf" value="{token}">',
    )
    resp = HTMLResponse(html)
    resp.set_cookie(
        CSRF_COOKIE, token, max_age=3600, httponly=True, samesite="strict", secure=_cookie_secure()
    )
    return resp


def _cookie_secure() -> bool:
    return settings.app_url.lower().startswith("https")


@router.post("/login")
async def admin_login_post(request: Request):
    ip = _ip_cliente(request)
    if _login_bloqueado(ip):
        resp = HTMLResponse(
            _LOGIN_HTML.replace("<!--ERROR-->", '<p style="color:#c62828">Demasiados intentos. Espera unos minutos.</p>'),
            status_code=429,
        )
        _establecer_csrf(resp)
        return resp

    data = await request.form()
    password = data.get("password", "")
    csrf = data.get("csrf", "")
    csrf_esperado = request.cookies.get(CSRF_COOKIE, "")
    if not csrf_esperado or not hmac.compare_digest(csrf_esperado, csrf):
        resp = HTMLResponse(
            _LOGIN_HTML.replace("<!--ERROR-->", '<p style="color:#c62828">Sesión inválida, recarga la página.</p>'),
            status_code=403,
        )
        _establecer_csrf(resp)
        return resp

    if not settings.admin_password:
        return HTMLResponse("<h3>ADMIN_PASSWORD no está configurado en el servidor.</h3>", status_code=400)
    if password != settings.admin_password:
        _registrar_intento(ip)
        resp = HTMLResponse(_LOGIN_HTML.replace("<!--ERROR-->", '<p style="color:#c62828">Contraseña incorrecta</p>'), status_code=401)
        _establecer_csrf(resp)
        return resp

    _contador_login.pop(ip, None)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE,
        _crear_sesion(),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="strict",
        secure=_cookie_secure(),
    )
    resp.delete_cookie(CSRF_COOKIE)
    return resp


@router.get("/logout")
def admin_logout():
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.get("", response_class=HTMLResponse)
def admin_panel(request: Request, session=Depends(get_session), _=Depends(require_admin)):

    pagina = 1
    try:
        pagina = max(1, int(request.query_params.get("pagina", "1")))
    except (TypeError, ValueError):
        pagina = 1
    por_pagina = 50

    # Mes activo del dashboard: por defecto el mes actual (hora de Bogotá) para
    # que cada mes el panel reinicie limpio. Los meses anteriores se consultan
    # con ?mes=YYYY-MM (los datos nunca se borran).
    mes_activo = (request.query_params.get("mes") or "").strip()
    if mes_activo:
        try:
            rango_mes_utc(mes_activo)
        except ValueError:
            mes_activo = ""
    if not mes_activo:
        mes_activo = datetime.now(BOGOTA_TZ).strftime("%Y-%m")
    inicio_mes, fin_mes = rango_mes_utc(mes_activo)

    # Meses disponibles para el selector (semanas/meses con datos o el activo).
    meses_disponibles = session.execute(
        select(VisitaLanding.created_at).with_only_columns(VisitaLanding.created_at)
    ).scalars().all()
    tutelas_created = session.execute(
        select(Tutela.created_at).with_only_columns(Tutela.created_at)
    ).scalars().all()
    meses_set = set()
    for dt in list(meses_disponibles) + list(tutelas_created):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        meses_set.add(dt.astimezone(BOGOTA_TZ).strftime("%Y-%m"))
    meses_set.add(mes_activo)
    meses_set = {m for m in meses_set if m}
    meses_ordenados = sorted(meses_set, reverse=True)
    mes_etiqueta = _nombre_mes_activo(mes_activo)

    rows = []
    stats = {"total": 0, "radicadas": 0, "pendientes": 0, "fallidas": 0}
    # Stats solo del mes activo
    total_mes = session.execute(
        select(Tutela).where(
            Tutela.created_at >= inicio_mes, Tutela.created_at < fin_mes
        )
    ).scalars().all()
    for t in total_mes:
        stats["total"] += 1
        if t.estado == "radicada":
            stats["radicadas"] += 1
        elif t.estado in ("fallida", "token_fallido"):
            stats["fallidas"] += 1
        else:
            stats["pendientes"] += 1

    total_tutelas = stats["total"]
    total_paginas = max(1, -(-total_tutelas // por_pagina))
    pagina = min(pagina, total_paginas)

    tutelas = session.execute(
        select(Tutela)
        .where(Tutela.created_at >= inicio_mes, Tutela.created_at < fin_mes)
        .order_by(Tutela.created_at.desc())
        .offset((pagina - 1) * por_pagina).limit(por_pagina)
    ).scalars().all()

    # Mini-resumen de pasos del bot para el indicador en la tabla
    # (últimos 3 pasos de cada tutela de la página actual).
    rad_ids = []
    rad_por_tutela: dict[int, int] = {}
    for t in tutelas:
        if t.radicacion:
            rad_por_tutela[t.id] = t.radicacion[0].id
            rad_ids.append(t.radicacion[0].id)
    pasos_por_rad: dict[int, list] = {}
    if rad_ids:
        recientes = session.execute(
            select(PasoRadicacion)
            .where(PasoRadicacion.radicacion_id.in_(rad_ids))
            .order_by(PasoRadicacion.created_at.desc())
        ).scalars().all()
        for p in recientes:
            pasos_por_rad.setdefault(p.radicacion_id, []).append(p)

    rows = []
    for t in tutelas:
        num_rad = ""
        constancia = ""
        if t.radicacion:
            r = t.radicacion[0]
            num_rad = r.num_radicado or ""
            constancia = r.constancia_path or ""

        pasos_row = []
        rad_id = rad_por_tutela.get(t.id)
        if rad_id and rad_id in pasos_por_rad:
            pasos_row = [
                {
                    "paso": p.paso,
                    "estado": p.estado,
                    "detalle": p.detalle or "",
                    "label": _ETIQUETA_PASO.get(p.paso, p.paso),
                }
                for p in pasos_por_rad[rad_id][:3]
            ]

        user_nombre = t.user.nombre if t.user else ""
        user_telefono = t.user.telefono.replace("whatsapp:", "") if t.user and t.user.telefono else ""
        datos_row = json.loads(t.datos_json) if t.datos_json else {}
        cedula_row = (datos_row.get("accionante_cedula") or "").strip()
        rows.append({
            "id": t.id,
            "cedula": cedula_row,
            "tipo": t.tipo,
            "estado": t.estado,
            "num_radicado": num_rad,
            "pdf_path": t.pdf_path,
            "constancia_path": constancia,
            "user_nombre": user_nombre,
            "user_telefono": user_telefono,
"created_at": _fecha_bogota(t.created_at),
            "pasos": pasos_row,
        })

    # Visitas a la landing (tráfico de pauta Facebook/UTM) del mes activo.
    # Se descartan los crawlers/previews (es_bot) para medir tráfico humano real.
    hace_24h = datetime.utcnow() - timedelta(hours=24)
    visitas_base = [VisitaLanding.es_bot.is_(False)]
    visitas_total = session.execute(
        select(func.count()).select_from(VisitaLanding).where(
            VisitaLanding.created_at >= inicio_mes, VisitaLanding.created_at < fin_mes,
            *visitas_base,
        )
    ).scalar() or 0
    visitas_24h = session.execute(
        select(func.count()).select_from(VisitaLanding).where(
            VisitaLanding.created_at >= hace_24h,
            VisitaLanding.created_at >= inicio_mes, VisitaLanding.created_at < fin_mes,
            *visitas_base,
        )
    ).scalar() or 0
    visitas_pauta = session.execute(
        select(func.count()).select_from(VisitaLanding).where(
            VisitaLanding.es_pauta,
            VisitaLanding.created_at >= inicio_mes, VisitaLanding.created_at < fin_mes,
            *visitas_base,
        )
    ).scalar() or 0
    visitas_por_fuente = session.execute(
        select(VisitaLanding.fuente, func.count().label("n"))
        .where(VisitaLanding.created_at >= inicio_mes, VisitaLanding.created_at < fin_mes,
               VisitaLanding.es_bot.is_(False))
        .group_by(VisitaLanding.fuente)
        .order_by(func.count().desc())
        .limit(6)
    ).all()
    ultimas_visitas = session.execute(
        select(VisitaLanding)
        .where(VisitaLanding.created_at >= inicio_mes, VisitaLanding.created_at < fin_mes,
               VisitaLanding.es_bot.is_(False))
        .order_by(VisitaLanding.created_at.desc()).limit(6)
    ).scalars().all()
    visitas_bots = session.execute(
        select(func.count()).select_from(VisitaLanding).where(
            VisitaLanding.created_at >= inicio_mes, VisitaLanding.created_at < fin_mes,
            VisitaLanding.es_bot.is_(True),
        )
    ).scalar() or 0
    clics_total = session.execute(
        select(func.count()).select_from(ClicWhatsApp).where(
            ClicWhatsApp.created_at >= inicio_mes, ClicWhatsApp.created_at < fin_mes
        )
    ).scalar() or 0
    clics_por_fuente = session.execute(
        select(ClicWhatsApp.fuente, func.count().label("n"))
        .where(ClicWhatsApp.created_at >= inicio_mes, ClicWhatsApp.created_at < fin_mes)
        .group_by(ClicWhatsApp.fuente)
        .order_by(func.count().desc())
        .limit(6)
    ).all()

    # Cortes semanales (hora de Bogotá) DENTRO del mes activo para ver cómo va
    # la semana actual del mes, no totales acumulados históricos.
    filas_visitas = session.execute(
        select(VisitaLanding.created_at, VisitaLanding.es_pauta).where(
            VisitaLanding.created_at >= inicio_mes, VisitaLanding.created_at < fin_mes,
            VisitaLanding.es_bot.is_(False)
        )
    ).all()
    visitas_semanales = agrupar_por_periodo(
        [{"created_at": f[0], "es_pauta": f[1]} for f in filas_visitas], "semana"
    )

    filas_tutelas = session.execute(
        select(Tutela.created_at, Tutela.estado).where(
            Tutela.created_at >= inicio_mes, Tutela.created_at < fin_mes
        )
    ).all()
    tutelas_semanales = agrupar_tutelas_por_periodo(
        [{"created_at": f[0], "estado": f[1]} for f in filas_tutelas], "semana"
    )

    visitas = {
        "total": visitas_total,
        "ultimas_24h": visitas_24h,
        "pauta": visitas_pauta,
        "bots": visitas_bots,
        "clasificadas": visitas_clasificadas(session, inicio_mes, fin_mes),
        "legacy": visitas_legacy(session, inicio_mes, fin_mes),
        "conversaciones": contar_conversaciones(session, inicio_mes, fin_mes),
        "conversaciones_24h": contar_conversaciones(session, hace_24h, fin_mes),
        "por_fuente": [{"fuente": nombre_fuente(f), "n": n} for f, n in visitas_por_fuente],
        "clics": clics_total,
        "clics_por_fuente": [{"fuente": nombre_fuente(f), "n": n} for f, n in clics_por_fuente],
        "por_semana": visitas_semanales,
        "tutelas_por_semana": tutelas_semanales,
        "ultimas": [
            {
                "fuente": nombre_fuente(v.fuente),
                "fuente_cruda": v.fuente,
                "medio": v.medio or "",
                "campania": v.campania or "",
                "es_pauta": bool(v.es_pauta),
                "created_at": _fecha_bogota(v.created_at),
            }
            for v in ultimas_visitas
        ],
    }

    template = env.get_template("admin.html")
    html = template.render(
        request=request,
        tutelas=rows,
        stats=stats,
        visitas=visitas,
        pagina=pagina,
        total_paginas=total_paginas,
        total_tutelas=total_tutelas,
        mes_activo=mes_activo,
        mes_etiqueta=mes_etiqueta,
        meses_disponibles=meses_ordenados,
        hay_meses_previos=any(m != mes_activo for m in meses_ordenados),
    )
    return HTMLResponse(html)


@router.get("/api/tutelas/{tutela_id}")
def detalle_tutela(tutela_id: int, request: Request, session=Depends(get_session), _=Depends(require_admin)):
    t = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not t:
        return JSONResponse({"error": "No encontrada"}, status_code=404)

    datos = json.loads(t.datos_json) if t.datos_json else {}

    rad = None
    if t.radicacion:
        r = t.radicacion[0]
        pasos = session.execute(
            select(PasoRadicacion)
            .where(PasoRadicacion.radicacion_id == r.id)
            .order_by(PasoRadicacion.created_at.asc())
        ).scalars().all()
        rad = {
            "id": r.id,
            "estado": r.estado,
            "num_radicado": r.num_radicado,
            "intentos": r.intentos,
            "ultimo_error": r.ultimo_error,
            "constancia_path": r.constancia_path,
            "token_verificacion": r.token_verificacion,
            "created_at": _fecha_bogota(r.created_at),
            "updated_at": _fecha_bogota(r.updated_at),
            "pasos": [
                {
                    "paso": p.paso,
                    "estado": p.estado,
                    "detalle": p.detalle,
                    "created_at": _fecha_bogota(p.created_at),
                }
                for p in pasos
            ],
        }

    mensajes = []
    for m in (t.mensajes or []):
        mensajes.append({
            "id": m.id,
            "body": m.body,
            "tipo": m.tipo_mensaje,
            "media_url": m.media_url,
            "es_recibido": m.es_recibido,
            "created_at": _fecha_bogota(m.created_at),
        })

    return {
        "id": t.id,
        "tipo": t.tipo,
        "estado": t.estado,
        "cedula": (datos.get("accionante_cedula") or "").strip(),
        "referencia": f"TUT-{t.id}",
        "link_pago": f"{settings.app_url}/pago/{t.id}",
        "datos": datos,
        "pdf_path": t.pdf_path,
        "created_at": _fecha_bogota(t.created_at),
        "updated_at": _fecha_bogota(t.updated_at),
        "usuario": {
            "telefono": t.user.telefono.replace("whatsapp:", "") if t.user and t.user.telefono else "",
            "nombre": t.user.nombre if t.user else "",
            "estado": t.user.estado if t.user else "",
        },
        "radicacion": rad,
        "mensajes": mensajes,
    }


@router.post("/tutelas/{tutela_id}/reintentar")
def reintentar_radicacion(tutela_id: int, request: Request, session=Depends(get_session), _=Depends(require_admin)):
    from app.tasks.scheduler import automatico_activo

    t = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not t:
        return {"error": "No encontrada"}
    if t.estado not in ("fallida", "pdf_generado", "pendiente_radicacion", "pago_confirmado", "esperando_codigo_email"):
        return {"error": f"No se puede reintentar (estado: {t.estado})"}

    from app.services.radicacion_service import despachar_radicacion

    t.estado = "pendiente_radicacion"

    # Al reintentar manualmente se reinicia el presupuesto de reintentos: sin
    # esto la tutela queda muerta para siempre para el scheduler
    # (guard `intentos >= 3` en jobs.py) aunque el admin la quiera re-lanzar.
    rad = session.execute(
        select(Radicacion).where(Radicacion.tutela_id == t.id)
    ).scalar_one_or_none()
    if rad:
        rad.intentos = 0
        rad.ultimo_error = None

    session.commit()
    try:
        resultado = despachar_radicacion(t.id, forzar=True)
        resultado["scheduler_automatico"] = automatico_activo()
        return resultado
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/scheduler")
def estado_scheduler(request: Request, _=Depends(require_admin)):
    """Estado actual del scheduler de radicación automática."""
    from app.tasks.scheduler import automatico_activo, scheduler_en_ejecucion

    return {
        "automatico": automatico_activo(),
        "ejecutandose": scheduler_en_ejecucion(),
        "horario": "lun a vie 8:00-12:00 y 14:00-16:00 (hora de Bogotá)",
    }


@router.get("/api/wa-status")
def estado_numero_whatsapp(request: Request, _=Depends(require_admin)):
    """Estado del número de WhatsApp (code_verification_status, etc.) vía Graph API."""
    from app.services.whatsapp_service import consultar_estado_numero

    return consultar_estado_numero()


@router.post("/api/wa-request-code")
async def wa_request_code(request: Request, _=Depends(require_admin)):
    """Pide a la Graph API un nuevo código de verificación (Cloud API).

    La UI de WhatsApp Manager redirige al sunset de On-Premises para números
    con `code_verification_status` expirado; por API sí se puede re-verificar.
    """
    from app.services.whatsapp_service import solicitar_codigo_verificacion

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    metodo = (body.get("metodo") or "VOICE").upper()
    if metodo not in ("VOICE", "SMS"):
        return JSONResponse({"error": "metodo invalido"}, status_code=400)
    return solicitar_codigo_verificacion(metodo)


@router.post("/api/wa-verify-code")
async def wa_verify_code(request: Request, _=Depends(require_admin)):
    """Envía el código recibido por SMS/llamada para verificar el número."""
    from app.services.whatsapp_service import verificar_codigo_whatsapp

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    codigo = (body.get("codigo") or "").strip()
    if not codigo or len(codigo) > 10:
        return JSONResponse({"error": "codigo requerido"}, status_code=400)
    res = verificar_codigo_whatsapp(codigo)
    return res


@router.post("/api/scheduler/toggle")
def toggle_scheduler(request: Request, _=Depends(require_admin)):
    """Activa/desactiva en caliente la radicación automática."""
    from app.tasks.scheduler import automatico_activo, set_scheduler_automatico

    habilitado = set_scheduler_automatico(not automatico_activo())
    return {"automatico": habilitado}


@router.post("/tutelas/{tutela_id}/confirmar-pago")
def confirmar_pago(tutela_id: int, request: Request, session=Depends(get_session), _=Depends(require_admin)):
    """Confirma manualmente el pago de una tutela y avisa al usuario por WhatsApp."""
    t = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not t:
        return {"ok": False, "error": "No encontrada"}
    if t.estado not in ("esperando_pago", "pago_por_confirmar", "confirmar_pago"):
        return {"ok": False, "error": f"No se puede confirmar pago (estado: {t.estado})"}

    from app.services.whatsapp_service import enviar_texto

    t.estado = "pago_confirmado"
    session.commit()
    if t.user and t.user.telefono:
        enviar_texto(
            t.user.telefono,
            "✅ *¡Pago verificado!*\n\n"
            "Nuestro equipo procederá con el procesamiento "
            "de tu solicitud. Te notificaremos cuando esté completa.",
        )
    return {"ok": True, "estado": t.estado}


@router.post("/tutelas/{tutela_id}/registrar-radicado")
async def registrar_radicado_manual(
    tutela_id: int,
    request: Request,
    session=Depends(get_session),
    _=Depends(require_admin),
):
    """Registra el número de radicado hecho manualmente por el equipo y avisa al usuario."""
    t = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not t:
        return {"ok": False, "error": "No encontrada"}

    data = await request.form()
    num_radicado = (data.get("num_radicado") or "").strip()
    if not num_radicado:
        return {"ok": False, "error": "Número de radicado requerido"}

    constancia_img = data.get("constancia_img")
    ruta_constancia = ""
    if constancia_img and getattr(constancia_img, "filename", ""):
        try:
            contenido = await constancia_img.read()
            if contenido:
                ext = os.path.splitext(constancia_img.filename or "")[1].lower()
                if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                    ext = ".png"
                from app.utils.file_utils import path_constancia_imagen

                import aiofiles

                ruta_constancia = path_constancia_imagen(ext)
                async with aiofiles.open(ruta_constancia, "wb") as f:
                    await f.write(contenido)
        except Exception as e:
            logger.error(f"Error guardando constancia imagen tutela {tutela_id}: {e}")

    rad = None
    if t.radicacion:
        rad = t.radicacion[0]
    if not rad:
        rad = Radicacion(tutela_id=t.id)
        session.add(rad)

    rad.num_radicado = num_radicado
    rad.estado = "radicada"
    if ruta_constancia:
        rad.constancia_path = ruta_constancia
    t.estado = "radicada"
    session.commit()

    from app.services.whatsapp_service import enviar_imagen, enviar_texto

    if t.user and t.user.telefono:
        enviar_texto(
            t.user.telefono,
            f"✅ *¡Tu trámite ha sido completado!*\n\n"
            f"Número de referencia: *{num_radicado}*\n\n"
            f"Gracias por confiar en nosotros.",
        )
        if ruta_constancia:
            enviar_imagen(
                t.user.telefono,
                ruta_constancia,
                caption=f"📄 *Constancia de radicación*\nN° radicado: {num_radicado}",
            )
    return {"ok": True, "estado": t.estado, "num_radicado": num_radicado, "constancia_path": ruta_constancia}


@router.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request, _=Depends(require_admin)):
    template = env.get_template("chat.html")
    return HTMLResponse(template.render(request=request))


@router.get("/tutelas/{tutela_id}/pdf")
def descargar_pdf(tutela_id: int, request: Request, session=Depends(get_session), _=Depends(require_admin)):
    t = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not t or not t.pdf_path or not os.path.exists(t.pdf_path):
        return JSONResponse({"error": "PDF no encontrado"}, status_code=404)
    return FileResponse(t.pdf_path, filename=os.path.basename(t.pdf_path), media_type="application/pdf")


@router.get("/tutelas/{tutela_id}/constancia")
def descargar_constancia(tutela_id: int, request: Request, session=Depends(get_session), _=Depends(require_admin)):
    r = session.execute(
        select(Radicacion).where(Radicacion.tutela_id == tutela_id)
    ).scalar_one_or_none()
    if not r or not r.constancia_path or not os.path.exists(r.constancia_path):
        return JSONResponse({"error": "Constancia no encontrada"}, status_code=404)

    ext = os.path.splitext(r.constancia_path)[1].lower()
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")
    return FileResponse(
        r.constancia_path,
        filename=f"constancia_{tutela_id}{ext}",
        media_type=media_type,
    )


@router.get("/screenshots")
def listar_screenshots(
    request: Request,
    tutela_id: int | None = None,
    _=Depends(require_admin),
):
    """Lista los screenshots de diagnóstico que toma el bot en momentos críticos
    (rechazo del portal, código de email, constancia, etc.) ordenados por fecha
    descendente. Sirven para ver de un vistazo en qué falla la radicación.

    Acepta ``?tutela_id=N`` para devolver SOLO las capturas de esa tutela: el bot
    prefija sus screenshots con ``t{id}_`` (y las constancias legacy se guardan
    como ``constancia_{id}``). Sin filtro se listan las últimas 40."""
    prefijo = f"t{tutela_id}_" if tutela_id is not None else ""
    constancia_legacy = f"constancia_{tutela_id}" if tutela_id is not None else ""
    dir_screenshots = Path(settings.storage_dir or "storage") / "screenshots"
    if not dir_screenshots.exists():
        return {"imagenes": []}
    imagenes = []
    for p in sorted(dir_screenshots.glob("*.png"), key=lambda x: x.stat().st_mtime, reverse=True)[:40]:
        if prefijo and not p.name.startswith(prefijo) and not (
            constancia_legacy and p.stem == constancia_legacy
        ):
            continue
        imagenes.append({
            "nombre": p.name,
            "modificado": _fecha_bogota(datetime.fromtimestamp(p.stat().st_mtime, tz=BOGOTA_TZ)),
        })
    return {"imagenes": imagenes}


@router.get("/screenshots/{nombre}")
def ver_screenshot(nombre: str, request: Request, _=Depends(require_admin)):
    """Sirve un screenshot de diagnóstico del bot."""
    nombre = Path(nombre).name
    dir_screenshots = Path(settings.storage_dir or "storage") / "screenshots"
    ruta = dir_screenshots / nombre
    if not ruta.exists() or not ruta.is_file():
        return JSONResponse({"error": "No encontrado"}, status_code=404)
    return FileResponse(ruta, media_type="image/png")


@router.post("/api/limpiar-historial")
def limpiar_historial(request: Request, session=Depends(get_session), _=Depends(require_admin)):
    """Borra el historial de radicaciones de los intentos previos (fallidas y
    pendientes/intermedias) junto con sus pasos y los screenshots de diagnóstico
    sueltos. Conserva: tutelas, usuarios, PDFs, constancias de radicadas y
    mensajes. Util para que la próxima radicación (ej. reintento de una tutela
    pendiente) aparezca como limpie en el panel admin."""
    # 1) Radicaciones que NO terminaron radicadas (intentos pasados) + sus pasos
    radicaciones = session.execute(
        select(Radicacion).where(Radicacion.estado != "radicada")
    ).scalars().all()
    ids_a_borrar = [r.id for r in radicaciones]
    n_pasos = 0
    if ids_a_borrar:
        res_pasos = session.execute(
            delete(PasoRadicacion).where(PasoRadicacion.radicacion_id.in_(ids_a_borrar))
        )
        n_pasos = res_pasos.rowcount
        session.execute(delete(Radicacion).where(Radicacion.id.in_(ids_a_borrar)))

    # 2) Screenshots sueltos que no pertenecen a una constancia de radicada
    dir_screenshots = Path(settings.storage_dir or "storage") / "screenshots"
    constancias = set()
    for p in session.execute(select(Radicacion.constancia_path).where(
        Radicacion.estado == "radicada", Radicacion.constancia_path.isnot(None)
    )).scalars().all():
        constancias.add(Path(p).resolve())
    n_screens = 0
    if dir_screenshots.exists():
        for archivo in dir_screenshots.glob("*.png"):
            try:
                if archivo.resolve() not in constancias:
                    archivo.unlink()
                    n_screens += 1
            except OSError:
                logger.warning(f"No se pudo eliminar screenshot {archivo}")

    session.commit()
    return JSONResponse({
        "ok": True,
        "radicaciones_eliminadas": len(ids_a_borrar),
        "pasos_eliminados": n_pasos,
        "screenshots_eliminados": n_screens,
    })
