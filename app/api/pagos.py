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
from app.services.whatsapp_service import enviar_texto
from app.services.wompi_service import (
    WOMPI_EVENT_APPROVED,
    consultar_transaccion,
    url_checkout,
    verificar_evento,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/pago/{tutela_id}")
async def iniciar_pago(
    tutela_id: int,
    session: Session = Depends(get_session),
):
    """Crea el link de pago Wompi y redirige al checkout (o al resultado si no hay Wompi)."""
    tutela = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not tutela:
        raise HTTPException(404, "Tutela no encontrada")

    reference = f"TUT-{tutela_id}"

    if settings.wompi_public_key and settings.wompi_integrity_secret:
        checkout_url = url_checkout(tutela_id, reference)
        return RedirectResponse(checkout_url, status_code=302)

    # Sin Wompi configurado: página informativa + opción de confirmar manualmente
    html = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head><meta charset="utf-8"><title>Pago - Tutela</title>
    <style>
      body {{ font-family: Arial; max-width: 480px; margin: 40px auto; padding: 0 16px; color:#222; }}
      h1 {{ color:#1a5fb4; }} .card {{ border:1px solid #ddd; border-radius:10px; padding:24px; }}
      .btn {{ display:block; text-align:center; padding:14px; border-radius:8px; text-decoration:none;
              font-weight:bold; margin:10px 0; }}
      .btn-primary {{ background:#2ecc71; color:#fff; }}
      .btn-outline {{ border:1px solid #999; color:#333; }}
      .small {{ font-size:13px; color:#666; }}
    </style></head>
    <body>
      <div class="card">
        <h1>Radicación de tutela</h1>
        <p>Radicamos tu tutela ante la Rama Judicial por <b>$20.000 COP</b>.</p>
        <p><b>Importante:</b> Radicamos tu tutela y te entregamos el número de radicado.</p>
        <p class="small">Para pagar por Nequi o transferencia, escríbenos por WhatsApp
           con la palabra <b>Pagado</b> y el número de referencia
           <code>{reference}</code>, y nuestro equipo confirmará el pago.</p>
      </div>
    </body></html>
    """
    return HTMLResponse(html)


@router.get("/pago/resultado")
async def resultado_pago(
    id: str = "",
    reference: str = "",
    transaction_id: str = "",
    session: Session = Depends(get_session),
):
    """Página a la que Wompi redirige tras el pago.

    Wompi añade ``?id=<transaction_id>`` a la URL de redirección; se usa para
    resolver la referencia y mostrar el estado. Es informativo: la confirmación
    real llega por webhook.
    """
    mensaje = "No pudimos confirmar tu pago."
    txn_id = transaction_id or id
    if txn_id:
        txn = await consultar_transaccion(txn_id)
        if txn:
            reference = txn.get("reference") or reference
    if reference:
        tutela_id = None
        try:
            tutela_id = int(reference.replace("TUT-", ""))
        except ValueError:
            pass
        if tutela_id:
            tutela = session.execute(
                select(Tutela).where(Tutela.id == tutela_id)
            ).scalar_one_or_none()
            if tutela and tutela.estado in ("esperando_pago", "pago_confirmado"):
                mensaje = (
                    "✅ ¡Pago registrado! Nuestro equipo radicará tu tutela "
                    "y te enviaremos el número de radicado por WhatsApp."
                )
            elif tutela:
                mensaje = "Tu pago ya fue procesado. Te llegará el número de radicado por WhatsApp."
    html = f"""
    <!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
    <title>Resultado del pago</title><style>
      body {{ font-family: Arial; max-width: 480px; margin: 40px auto; padding: 0 16px; text-align:center; }}
      .card {{ border:1px solid #ddd; border-radius:10px; padding:32px; }}
    </style></head><body><div class="card"><h1>{mensaje}</h1>
    <p>Puedes cerrar esta página.</p></div></body></html>
    """
    return HTMLResponse(html)


@router.post("/webhook/wompi")
async def webhook_wompi(request: Request, session: Session = Depends(get_session)):
    """Recibe eventos de Wompi (transaction.updated) y confirma el pago."""
    try:
        evento = await request.json()
    except Exception:  # noqa: BLE001
        return {"ok": False}

    checksum_header = request.headers.get("X-Event-Checksum") or request.headers.get("x-event-checksum")
    if not verificar_evento(evento, checksum_header):
        logger.error("Webhook Wompi rechazado: checksum inválido")
        return {"ok": False}

    if evento.get("event") != "transaction.updated":
        return {"ok": True}

    txn = (evento.get("data", {}) or {}).get("transaction", {}) or {}
    status = txn.get("status")
    reference = txn.get("reference", "")
    transaction_id = txn.get("id", "")

    if status != WOMPI_EVENT_APPROVED:
        return {"ok": True}

    if not reference or not reference.startswith("TUT-"):
        return {"ok": True}
    try:
        tutela_id = int(reference.replace("TUT-", ""))
    except ValueError:
        return {"ok": True}

    tutela = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not tutela:
        return {"ok": True}

    # Registrar la radicación con estado pagado (esperando radicación manual)
    if tutela.estado in ("esperando_pago", "confirmar_pago"):
        rad = Radicacion(
            tutela_id=tutela.id,
            estado="pendiente",
            num_radicado=None,
        )
        session.add(rad)
        tutela.estado = "pago_confirmado"
        session.commit()
        logger.info(f"Pago confirmado vía Wompi para tutela {tutela.id} (txn {transaction_id})")
        if tutela.user and tutela.user.telefono:
            enviar_texto(
                tutela.user.telefono,
                "✅ *¡Pago confirmado!* Recibimos tu pago de $20.000 COP.\n\n"
                "Nuestro equipo radicará tu tutela y te enviaremos el "
                "*número de radicado* por este chat.",
            )
    return {"ok": True}


@router.post("/pago/{tutela_id}/verificar")
async def verificar_pago(
    tutela_id: int,
    session: Session = Depends(get_session),
):
    """Respaldo: verifica el pago consultando la transacción en Wompi."""
    tutela = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not tutela:
        raise HTTPException(404, "Tutela no encontrada")
    datos = json.loads(tutela.datos_json or "{}")
    transaction_id = datos.get("wompi_transaction_id", "")
    if not transaction_id:
        return {"ok": False, "error": "No hay transacción registrada"}
    txn = await consultar_transaccion(transaction_id)
    if txn and txn.get("status") == WOMPI_EVENT_APPROVED:
        tutela.estado = "pago_confirmado"
        session.commit()
        if tutela.user and tutela.user.telefono:
            enviar_texto(
                tutela.user.telefono,
                "✅ *¡Pago confirmado!* Nuestro equipo radicará tu tutela y te "
                "enviaremos el número de radicado por este chat.",
            )
        return {"ok": True, "status": "APPROVED"}
    return {"ok": False, "status": (txn or {}).get("status", "DESCONOCIDO")}
