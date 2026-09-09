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
    verificar_firma,
)
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
            "2:00 pm - 4:00 pm (hora de Bogotá). Estamos *en horario*, radicaremos tu "
            "tutela de inmediato y te pediremos el código de verificación aquí mismo."
        )
    return (
        "Horario de radicación de la Rama Judicial: lun a vie 8:00 am - 12:00 m y "
        "2:00 pm - 4:00 pm (hora de Bogotá). Ahora estamos *fuera de horario*: tu pago "
        "quedará en cola y se procesará el próximo día hábil, cuando te pediremos el "
        "código de verificación aquí mismo."
    )


def _pagina_pago(
    aviso: str,
    email: str = "",
    init_point: str | None = None,
    reference: str = "",
) -> str:
    """Página intermedia de pago (Radicación de tutela).

    Avisa el horario de la Rama Judicial, pide compartir por WhatsApp el posible
    código de verificación que llegue por correo una vez pagado, e indica que el
    pago es únicamente vía el botón de Mercado Pago (no Nequi ni transferencia).
    ``init_point`` es el checkout de MP; si es None se muestra una página informativa.
    """
    precio = f"${settings.mercadopago_amount:,.0f}".replace(",", ".")
    email_txt = f" <b>{escape(email)}</b>" if email else ""
    boton = f'<a class="btn" href="{escape(init_point)}">Continuar al pago</a>' if init_point else ""
    ref_line = f'<p class="nota">Referencia: <code>{escape(reference)}</code></p>' if reference else ""
    return f"""<!DOCTYPE html>
    <html lang="es">
    <head><meta charset="utf-8"><title>Pago - Tutela</title>
    <style>
      body {{ font-family: Arial; max-width: 480px; margin: 40px auto; padding: 0 16px; color:#222; }}
      h1 {{ color:#1a5fb4; }}
      .card {{ border:1px solid #ddd; border-radius:10px; padding:24px; }}
      .aviso {{ background:#fff3cd; border:1px solid #ffe08a; border-radius:8px; padding:12px 14px;
                font-size:14px; color:#7a5c00; margin:16px 0; }}
      .aviso.email {{ background:#e3f2fd; border:1px solid #90caf9; color:#0d47a1; }}
      .btn {{ display:block; text-align:center; background:#1a5fb4; color:#fff; text-decoration:none;
              padding:14px; border-radius:8px; font-weight:600; font-size:16px; }}
      .nota {{ font-size:16px; color:#555; margin-top:16px; }}
      code {{ background:#f0f0f0; padding:2px 6px; border-radius:4px; }}
    </style></head>
    <body>
      <div class="card">
        <h1>Radicación de tutela</h1>
        <p style="font-size:16px;">Radicamos tu tutela ante la Rama Judicial por <b>{precio} COP</b>.</p>
        <div class="aviso"><b>&#128197; Horario de radicación de la Rama Judicial</b><br>{aviso}</div>
        <div class="aviso email"><b>&#128231; Código de verificación por correo</b><br>
            Al radicar, la Rama Judicial podría enviar un mensaje con un código de
            verificación a tu correo{email_txt}. <b>Una vez pagues</b>, compártenos ese
            código por WhatsApp para continuar con la radicación.</div>
        {boton}
        <p class="nota">El pago se realiza <b>únicamente</b> a través de
            <b>Mercado Pago</b> con el botón de arriba. No solicitamos pagos por otros medios.</p>
        {ref_line}
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

            aviso = texto_aviso_horario(es_horario_habil())
            return HTMLResponse(_pagina_pago(
                aviso,
                email=datos.get("accionante_email", ""),
                init_point=init_point,
                reference=reference,
            ))

    # Sin Mercado Pago configurado: página informativa + opción de confirmar manualmente
    datos = json.loads(tutela.datos_json or "{}")
    aviso = texto_aviso_horario(es_horario_habil())
    return HTMLResponse(_pagina_pago(
        aviso,
        email=datos.get("accionante_email", ""),
        reference=reference,
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
                "Nuestro equipo técnico ya está trabajando en la generación y radicación "
                "de tu documento. Te notificaremos por este medio en cuanto el proceso finalice.",
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