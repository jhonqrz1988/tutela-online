import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela
from app.services.mercadopago_service import (
    consultar_pago,
    crear_preferencia_checkout,
    verificar_firma,
)
from app.services.whatsapp_service import enviar_texto

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/pago/resultado")
async def resultado_pago(request: Request, session: Session = Depends(get_session)):
    """Página a la que Mercado Pago redirige tras el pago (back_urls).

    Mercado Pago añade por query string: payment_id, external_reference, status,
    collection_id, etc. Es informativo: la confirmación real llega por webhook.
    """
    params = request.query_params
    status = params.get("status", "")
    external_reference = params.get("external_reference", "")

    if status == "approved":
        mensaje = (
            "✅ ¡Pago registrado! Nuestro equipo radicará tu tutela "
            "y te enviaremos el número de radicado por WhatsApp."
        )
    elif external_reference and status in ("", "pending"):
        mensaje = "⏳ Estamos confirmando tu pago. Te avisaremos por WhatsApp."
    else:
        mensaje = "No pudimos confirmar tu pago. Si ya pagaste, escríbenos *Pagado* por WhatsApp."

    html = f"""
    <!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
    <title>Resultado del pago</title><style>
      body {{ font-family: Arial; max-width: 480px; margin: 40px auto; padding: 0 16px; text-align:center; }}
      .card {{ border:1px solid #ddd; border-radius:10px; padding:32px; }}
    </style></head><body><div class="card"><h1>{mensaje}</h1>
    <p>Puedes cerrar esta página.</p></div></body></html>
    """
    return HTMLResponse(html)


@router.get("/pago/{tutela_id}")
async def iniciar_pago(
    tutela_id: int,
    session: Session = Depends(get_session),
):
    """Crea una preferencia en Mercado Pago y redirige al checkout alojado."""
    tutela = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not tutela:
        raise HTTPException(404, "Tutela no encontrada")

    reference = f"TUT-{tutela_id}"

    if settings.mercadopago_access_token:
        pref = crear_preferencia_checkout(tutela_id, reference)
        init_point = pref.get("init_point")
        if init_point:
            # Guardamos la referencia en caso de que el webhook no llegue
            datos = json.loads(tutela.datos_json or "{}")
            datos["mercadopago_reference"] = reference
            tutela.datos_json = json.dumps(datos)
            session.commit()
            return RedirectResponse(init_point, status_code=302)

    # Sin Mercado Pago configurado: página informativa + opción de confirmar manualmente
    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head><meta charset="utf-8"><title>Pago - Tutela</title>
    <style>
      body {{ font-family: Arial; max-width: 480px; margin: 40px auto; padding: 0 16px; color:#222; }}
      h1 {{ color:#1a5fb4; }} .card {{ border:1px solid #ddd; border-radius:10px; padding:24px; }}
      .small {{ font-size:13px; color:#666; }}
    </style></head>
    <body>
      <div class="card">
        <h1>Radicación de tutela</h1>
        <p>Radicamos tu tutela ante la Rama Judicial por <b>$29.000 COP</b>.</p>
        <p><b>Importante:</b> Radicamos tu tutela y te entregamos el número de radicado.</p>
        <p class="small">Para pagar por Nequi o transferencia, escríbenos por WhatsApp
           con la palabra <b>Pagado</b> y el número de referencia
           <code>{reference}</code>, y nuestro equipo confirmará el pago.</p>
      </div>
    </body></html>
    """
    return HTMLResponse(html)


@router.post("/webhook/mercadopago")
async def webhook_mercadopago(request: Request, session: Session = Depends(get_session)):
    """Recibe notificaciones de Mercado Pago y confirma el pago.

    Cuerpo típico: ``{"type": "payment", "data": {"id": "123..."}}``.
    El status real se consulta a la API de Mercado Pago (los webhooks no traen el monto).
    """
    x_signature = request.headers.get("x-signature", "")
    x_request_id = request.headers.get("x-request-id", "")
    logger.info(f"Webhook MP recibido — tipo={request.headers.get('x-type','?')} "
                f"x-request-id={x_request_id} signature_presente={bool(x_signature)}")

    try:
        evento = await request.json()
    except Exception:  # noqa: BLE001
        return {"ok": False}

    tipo = evento.get("type") or evento.get("topic")
    data = evento.get("data", {}) or {}
    payment_id = str(data.get("id", ""))
    if tipo != "payment" or not payment_id:
        return {"ok": True}

    if not verificar_firma(x_signature, x_request_id, payment_id):
        logger.error(f"Webhook Mercado Pago rechazado: firma inválida ({payment_id})")
        return {"ok": False}

    logger.info(f"Webhook MP pago={payment_id} — consultando estado...")
    txn = await consultar_pago(payment_id)
    if not txn or txn.get("status") != "approved":
        return {"ok": True}

    reference = (txn.get("external_reference") or "").strip()
    if not reference.startswith("TUT-"):
        return {"ok": True}
    try:
        tutela_id = int(reference.replace("TUT-", ""))
    except ValueError:
        return {"ok": True}

    tutela = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not tutela:
        return {"ok": True}

    if tutela.estado in ("esperando_pago", "confirmar_pago", "pago_por_confirmar"):
        rad = Radicacion(
            tutela_id=tutela.id,
            estado="pendiente",
            num_radicado=None,
        )
        session.add(rad)
        datos = json.loads(tutela.datos_json or "{}")
        datos["mercadopago_payment_id"] = payment_id
        tutela.datos_json = json.dumps(datos)
        tutela.estado = "pago_confirmado"
        session.commit()
        logger.info(f"Pago confirmado vía Mercado Pago para tutela {tutela.id} (pago {payment_id})")
        if tutela.user and tutela.user.telefono:
            enviar_texto(
                tutela.user.telefono,
                "✅ *¡Pago recibido!* Hemos confirmado tu pago de $29.000 COP.\n\n"
                "Nuestro equipo procesará tu solicitud y te notificaremos "
                "cuando esté lista.",
            )
    return {"ok": True}


@router.post("/pago/{tutela_id}/verificar")
async def verificar_pago(
    tutela_id: int,
    session: Session = Depends(get_session),
):
    """Respaldo: verifica el pago consultando el payment_id guardado en la tutela."""
    tutela = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not tutela:
        raise HTTPException(404, "Tutela no encontrada")
    datos = json.loads(tutela.datos_json or "{}")
    payment_id = datos.get("mercadopago_payment_id", "")
    if not payment_id:
        return {"ok": False, "error": "No hay pago registrado"}
    txn = await consultar_pago(payment_id)
    if txn and txn.get("status") == "approved":
        if tutela.estado != "pago_confirmado":
            tutela.estado = "pago_confirmado"
            session.commit()
        if tutela.user and tutela.user.telefono:
            enviar_texto(
                tutela.user.telefono,
                "✅ *¡Pago verificado!* Nuestro equipo procederá con el procesamiento "
                "de tu solicitud. Te notificaremos cuando esté completa.",
            )
        return {"ok": True, "status": "approved"}
    return {"ok": False, "status": (txn or {}).get("status", "DESCONOCIDO")}