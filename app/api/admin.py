import hashlib
import hmac
import json
import logging
import os
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select

from app.config import settings
from app.database import get_session
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela

router = APIRouter(prefix="/admin")
logger = logging.getLogger(__name__)
env = Environment(
    loader=FileSystemLoader("app/templates"),
    cache_size=0,
    autoescape=select_autoescape(["html", "htm"]),
)

SESSION_COOKIE = "tutela_admin"
SESSION_TTL = 12 * 3600  # 12 horas

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
    return HTMLResponse(_LOGIN_HTML)


@router.post("/login")
async def admin_login_post(request: Request):
    data = await request.form()
    password = data.get("password", "")
    if not settings.admin_password:
        return HTMLResponse("<h3>ADMIN_PASSWORD no está configurado en el servidor.</h3>", status_code=400)
    if password != settings.admin_password:
        return HTMLResponse(_LOGIN_HTML.replace("<!--ERROR-->", '<p style="color:#c62828">Contraseña incorrecta</p>'), status_code=401)
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(SESSION_COOKIE, _crear_sesion(), max_age=SESSION_TTL, httponly=True, samesite="lax", secure=False)
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

    total = session.execute(select(Tutela)).scalars().all()
    total_tutelas = len(total)
    total_paginas = max(1, -(-total_tutelas // por_pagina))
    pagina = min(pagina, total_paginas)

    tutelas = session.execute(
        select(Tutela).order_by(Tutela.created_at.desc()).offset((pagina - 1) * por_pagina).limit(por_pagina)
    ).scalars().all()

    rows = []
    stats = {"total": 0, "radicadas": 0, "pendientes": 0, "fallidas": 0}
    for t in total:
        stats["total"] += 1
        if t.estado == "radicada":
            stats["radicadas"] += 1
        elif t.estado in ("fallida", "token_fallido"):
            stats["fallidas"] += 1
        else:
            stats["pendientes"] += 1

    for t in tutelas:
        num_rad = ""
        constancia = ""
        if t.radicacion:
            r = t.radicacion[0]
            num_rad = r.num_radicado or ""
            constancia = r.constancia_path or ""

        user_nombre = t.user.nombre if t.user else ""
        user_telefono = t.user.telefono.replace("whatsapp:", "") if t.user and t.user.telefono else ""
        rows.append({
            "id": t.id,
            "tipo": t.tipo,
            "estado": t.estado,
            "num_radicado": num_rad,
            "pdf_path": t.pdf_path,
            "constancia_path": constancia,
            "user_nombre": user_nombre,
            "user_telefono": user_telefono,
            "created_at": str(t.created_at) if t.created_at else "",
        })

    template = env.get_template("admin.html")
    html = template.render(
        request=request,
        tutelas=rows,
        stats=stats,
        pagina=pagina,
        total_paginas=total_paginas,
        total_tutelas=total_tutelas,
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
        rad = {
            "id": r.id,
            "estado": r.estado,
            "num_radicado": r.num_radicado,
            "intentos": r.intentos,
            "ultimo_error": r.ultimo_error,
            "constancia_path": r.constancia_path,
            "token_verificacion": r.token_verificacion,
            "created_at": str(r.created_at) if r.created_at else "",
            "updated_at": str(r.updated_at) if r.updated_at else "",
        }

    mensajes = []
    for m in (t.mensajes or []):
        mensajes.append({
            "id": m.id,
            "body": m.body,
            "tipo": m.tipo_mensaje,
            "media_url": m.media_url,
            "es_recibido": m.es_recibido,
            "created_at": str(m.created_at) if m.created_at else "",
        })

    return {
        "id": t.id,
        "tipo": t.tipo,
        "estado": t.estado,
        "referencia": f"TUT-{t.id}",
        "link_pago": f"{settings.app_url}/pago/{t.id}",
        "datos": datos,
        "pdf_path": t.pdf_path,
        "created_at": str(t.created_at) if t.created_at else "",
        "updated_at": str(t.updated_at) if t.updated_at else "",
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
    t = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not t:
        return {"error": "No encontrada"}
    if t.estado not in ("fallida", "pdf_generado", "pendiente_radicacion"):
        return {"error": f"No se puede reintentar (estado: {t.estado})"}

    import asyncio

    from app.services.radicacion_service import iniciar_radicacion

    t.estado = "pendiente_radicacion"
    session.commit()
    try:
        resultado = asyncio.run(iniciar_radicacion(t.id))
        return resultado
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
            "✅ *¡Pago confirmado!*\n\n"
            "Nuestro equipo radicará tu tutela y te enviaremos el "
            "*número de radicado* por este chat en máximo *4 horas hábiles* "
            "(lun-vie 8am-5pm).",
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
            f"✅ *¡Tutela radicada!*\n\n"
            f"N° radicado: *{num_radicado}*\n\n"
            f"Gracias por usar nuestro servicio.",
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
    return FileResponse(t.pdf_path, filename=f"tutela_{tutela_id}.pdf", media_type="application/pdf")


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
