import json
import logging
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_session
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela
from app.services.mercadopago_service import (
    consultar_pago,
    crear_preferencia_checkout,
    formatear_monto,
    texto_precio,
    verificar_firma,
)
from app.services.radicacion_service import programar_radicacion_inmediata
from app.services.whatsapp_service import enviar_texto
from app.tasks.jobs import es_horario_habil

logger = logging.getLogger(__name__)

router = APIRouter()


def texto_aviso_horario(habile: bool) -> str:
    """Texto del aviso de horario hábil de la Rama Judicial mostrado antes de radicar.

    Args:
        habile: True si ahora es horario hábil (se radica de inmediato).
    """
    if habile:
        return (
            "Horario de radicación de la Rama Judicial: lun a vie 8:00 am - 12:00 m y "
            "2:00 pm - 5:00 pm (hora de Bogotá). Estamos *en horario*, radicaremos tu "
            "tutela de inmediato y te pediremos el código de verificación aquí mismo."
        )
    return (
        "Horario de radicación de la Rama Judicial: lun a vie 8:00 am - 12:00 m y "
        "2:00 pm - 5:00 pm (hora de Bogotá). Ahora estamos *fuera de horario*: tu pago "
        "quedará en cola y se procesará el próximo día hábil, cuando te pediremos el "
        "código de verificación aquí mismo."
    )


def _pagina_pago(
    aviso: str,
    email: str = "",
    init_point: str | None = None,
) -> str:
    """Página intermedia de pago (Radicación de tutela).

    Avisa el horario de la Rama Judicial y pide compartir por WhatsApp el posible
    código de verificación que llegue por correo una vez pagado. El pago se hace
    con el botón de Mercado Pago, grande y llamativo, sin leyendas de medios
    alternativos (Nequi/transferencia no están activos).
    ``init_point`` es el checkout de MP; si es None se muestra una página informativa.
    """
    precio = formatear_monto()
    email_txt = f" <b>{escape(email)}</b>" if email else ""
    boton = f'<a class="btn" href="{escape(init_point)}">&#128179; Continuar al pago</a>' if init_point else ""
    return f"""<!DOCTYPE html>
    <html lang="es">
    <head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Pago - Tutela</title>
    <style>
      * {{ box-sizing: border-box; }}
      body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
              max-width: 480px; margin: 0 auto; padding: 20px 18px; color:#222;
              font-size:19px; line-height:1.55; -webkit-text-size-adjust:100%; }}
      h1 {{ color:#1a5fb4; font-size:29px; line-height:1.2; margin:0 0 14px; }}
      p {{ margin:0 0 16px; }}
      .card {{ border:1px solid #e0e0e0; border-radius:16px; padding:26px 22px;
              background:#fff; box-shadow:0 2px 10px rgba(0,0,0,.06); }}
      .aviso {{ background:#fff3cd; border:1px solid #ffe08a; border-radius:10px; padding:18px 16px;
                font-size:18px; color:#7a5c00; margin:18px 0; line-height:1.6; }}
      .aviso.email {{ background:#e3f2fd; border:1px solid #90caf9; color:#0d47a1; }}
      .aviso b {{ display:block; margin-bottom:4px; }}
      .btn {{ display:flex; align-items:center; justify-content:center; min-height:60px;
              background:linear-gradient(180deg,#00a650,#008745); color:#fff; text-decoration:none;
              padding:16px 18px; border-radius:14px; font-weight:800; font-size:22px;
              margin-top:18px; margin-bottom:6px; box-shadow:0 4px 14px rgba(0,166,80,.35); }}
      @media (max-width:380px) {{
        body {{ padding:14px 14px; font-size:18px; }}
        .aviso, .aviso.email {{ font-size:17px; }}
        .btn {{ font-size:20px; }}
      }}
    </style></head>
    <body>
      <div class="card">
        <h1>Radicación de tutela</h1>
        <p style="margin-bottom:0;">Radicamos tu tutela ante la Rama Judicial por <b>{precio} COP</b>.</p>
        {boton}
        <div class="aviso"><b>&#128197; Horario de radicación de la Rama Judicial</b>{aviso}</div>
        <div class="aviso email"><b>&#128231; Código de verificación por correo</b>
            Al radicar, la Rama Judicial podría enviar un mensaje con un código de
            verificación a tu correo{email_txt}. <b>Una vez pagues</b>, compártenos ese
            código por WhatsApp para continuar con la radicación.</div>
      </div>
    </body></html>
    """


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
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Resultado del pago</title><style>
      * {{ box-sizing: border-box; }}
      body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
              max-width: 480px; margin: 0 auto; padding: 24px 18px; text-align:center;
              font-size:20px; line-height:1.5; color:#222; -webkit-text-size-adjust:100%; }}
      .card {{ border:1px solid #e0e0e0; border-radius:16px; padding:32px 22px; background:#fff;
              box-shadow:0 2px 10px rgba(0,0,0,.06); }}
      h1 {{ font-size:24px; line-height:1.35; margin:0 0 16px; color:#1a5fb4; }}
      p {{ margin:0; font-size:18px; }}
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

            aviso = texto_aviso_horario(es_horario_habil())
            return HTMLResponse(_pagina_pago(
                aviso,
                email=datos.get("accionante_email", ""),
                init_point=init_point,
            ))

    # Sin Mercado Pago configurado: página informativa
    datos = json.loads(tutela.datos_json or "{}")
    aviso = texto_aviso_horario(es_horario_habil())
    return HTMLResponse(_pagina_pago(
        aviso,
        email=datos.get("accionante_email", ""),
    ))


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

    if tutela.estado not in ("esperando_pago", "confirmar_pago", "pago_por_confirmar", "pago_confirmado"):
        return {"ok": True}

    # Idempotencia: MP reenvía el webhook varias veces; ya procesado → no-op.
    datos = json.loads(tutela.datos_json or "{}")
    if datos.get("mercadopago_payment_id") == payment_id:
        logger.info(f"Webhook MP duplicado (pago {payment_id}) para tutela {tutela.id}: ignorado")
        return {"ok": True}

    # Un solo registro de radicación por tutela pagada (nunca radicar dos veces).
    rad = session.execute(
        select(Radicacion).where(Radicacion.tutela_id == tutela.id)
    ).scalar_one_or_none()
    if rad is None:
        rad = Radicacion(
            tutela_id=tutela.id,
            estado="pendiente",
            num_radicado=None,
        )
        session.add(rad)

    ya_confirmada = tutela.estado == "pago_confirmado"
    datos["mercadopago_payment_id"] = payment_id
    tutela.datos_json = json.dumps(datos)
    tutela.estado = "pago_confirmado"
    session.commit()
    logger.info(
        f"Pago {payment_id} confirmado vía Mercado Pago para tutela {tutela.id}"
        f"{' (reconfirmación)' if ya_confirmada else ''}"
    )
    if tutela.user and tutela.user.telefono and not ya_confirmada:
        enviar_texto(
            tutela.user.telefono,
            f"✅ *¡Pago recibido!* Hemos confirmado tu pago de {texto_precio()}.\n\n"
            "Ya iniciamos la radicación de tu tutela ante la Rama Judicial. "
            "En cuanto quede *radicada* te enviaremos el *número de radicado* por este chat.",
        )
    # Radicar de inmediato (sin esperar los 15 min del scheduler) solo si
    # hay horario hábil de la Rama Judicial; si no, queda en cola para el
    # próximo ciclo del scheduler en horario laboral.
    if es_horario_habil() and not ya_confirmada:
        programar_radicacion_inmediata(tutela.id)
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