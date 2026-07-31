import json
import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select

from app.database import get_session
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela

router = APIRouter(prefix="/admin")
env = Environment(loader=FileSystemLoader("app/templates"), cache_size=0)


@router.get("", response_class=HTMLResponse)
def admin_panel(request: Request, session=Depends(get_session)):
    tutelas = session.execute(
        select(Tutela).order_by(Tutela.created_at.desc()).limit(50)
    ).scalars().all()

    rows = []
    stats = {"total": 0, "radicadas": 0, "pendientes": 0, "fallidas": 0}
    for t in tutelas:
        stats["total"] += 1
        if t.estado == "radicada":
            stats["radicadas"] += 1
        elif t.estado in ("fallida", "token_fallido"):
            stats["fallidas"] += 1
        else:
            stats["pendientes"] += 1

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
    html = template.render(request=request, tutelas=rows, stats=stats)
    return HTMLResponse(html)


@router.get("/api/tutelas/{tutela_id}")
def detalle_tutela(tutela_id: int, session=Depends(get_session)):
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
def reintentar_radicacion(tutela_id: int, session=Depends(get_session)):
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
def confirmar_pago(tutela_id: int, session=Depends(get_session)):
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
):
    """Registra el número de radicado hecho manualmente por el equipo y avisa al usuario."""
    t = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not t:
        return {"ok": False, "error": "No encontrada"}

    data = await request.form()
    num_radicado = (data.get("num_radicado") or "").strip()
    if not num_radicado:
        return {"ok": False, "error": "Número de radicado requerido"}

    rad = None
    if t.radicacion:
        rad = t.radicacion[0]
    if not rad:
        rad = Radicacion(tutela_id=t.id)
        session.add(rad)

    rad.num_radicado = num_radicado
    rad.estado = "radicada"
    t.estado = "radicada"
    session.commit()

    from app.services.whatsapp_service import enviar_texto

    if t.user and t.user.telefono:
        enviar_texto(
            t.user.telefono,
            f"✅ *¡Tutela radicada!*\n\n"
            f"N° radicado: *{num_radicado}*\n\n"
            f"Gracias por usar nuestro servicio.",
        )
    return {"ok": True, "estado": t.estado, "num_radicado": num_radicado}


@router.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    template = env.get_template("chat.html")
    return HTMLResponse(template.render(request=request))


@router.get("/tutelas/{tutela_id}/pdf")
def descargar_pdf(tutela_id: int, session=Depends(get_session)):
    t = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not t or not t.pdf_path or not os.path.exists(t.pdf_path):
        return {"error": "PDF no encontrado"}, 404
    return FileResponse(t.pdf_path, filename=f"tutela_{tutela_id}.pdf", media_type="application/pdf")


@router.get("/tutelas/{tutela_id}/constancia")
def descargar_constancia(tutela_id: int, session=Depends(get_session)):
    r = session.execute(
        select(Radicacion).where(Radicacion.tutela_id == tutela_id)
    ).scalar_one_or_none()
    if not r or not r.constancia_path or not os.path.exists(r.constancia_path):
        return {"error": "Constancia no encontrada"}, 404
    return FileResponse(r.constancia_path, filename=f"constancia_{tutela_id}.pdf", media_type="application/pdf")
