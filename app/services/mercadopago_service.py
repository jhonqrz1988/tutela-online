import hashlib
import hmac
import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_WEBHOOK_MAX_AGE_SECS = 300


def formatear_monto() -> str:
    """Monto legible sin moneda con separador de miles (ej.: '$29.000')."""
    return f"${settings.mercadopago_amount:,.0f}".replace(",", ".")


def texto_precio() -> str:
    """Precio legible para mensajes y página de pago (ej.: '$29.000 COP')."""
    return f"{formatear_monto()} COP"


def crear_preferencia_checkout(tutela_id: int, reference: str) -> dict:
    """Crea una preferencia de pago en Mercado Pago (Checkout Pro) y devuelve la URL de checkout.

    Retorna dict con keys ``init_point`` / ``sandbox_init_point`` según el ambiente.
    """
    if not settings.mercadopago_access_token:
        return {}
    body = {
        "items": [
            {
                "id": str(tutela_id),
                "title": "Radicación de tutela",
                "quantity": 1,
                "unit_price": settings.mercadopago_amount,
                "currency_id": settings.mercadopago_currency,
            }
        ],
        "external_reference": reference,
        "back_urls": {
            "success": f"{settings.app_url}/pago/resultado",
            "pending": f"{settings.app_url}/pago/resultado",
            "failure": f"{settings.app_url}/pago/resultado",
        },
        "auto_return": "approved",
        "notification_url": f"{settings.app_url}/webhook/mercadopago",
        "statement_descriptor": "TUTELAS ONLINE",
    }
    headers = {
        "Authorization": f"Bearer {settings.mercadopago_access_token}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=20) as c:
            r = c.post("https://api.mercadopago.com/checkout/preferences", json=body, headers=headers)
        if r.status_code in (200, 201):
            data = r.json()
            return {
                "id": data.get("id"),
                "init_point": data.get("init_point") or data.get("sandbox_init_point"),
            }
        logger.error(f"MercadoPago crear_preferencia error {r.status_code}: {r.text[:300]}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"MercadoPago crear_preferencia exception: {e}")
    return {}


async def consultar_pago(payment_id: str) -> dict | None:
    """Consulta el estado de un pago en Mercado Pago."""
    if not settings.mercadopago_access_token or not payment_id:
        return None
    headers = {"Authorization": f"Bearer {settings.mercadopago_access_token}"}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"https://api.mercadopago.com/v1/payments/{payment_id}", headers=headers)
        if r.status_code == 200:
            d = r.json()
            logger.info(f"consultar_pago id={payment_id} status={d.get('status')} "
                        f"ref={d.get('external_reference')}")
            return {
                "id": d.get("id"),
                "status": d.get("status"),
                "external_reference": d.get("external_reference"),
                "transaction_amount": d.get("transaction_amount"),
                "currency_id": d.get("currency_id"),
            }
    except Exception as e:  # noqa: BLE001
        logger.error(f"MercadoPago consultar_pago exception: {e}")
    return None


def verificar_firma(
    x_signature: str,
    x_request_id: str,
    data_id: str,
    max_age_secs: int = _DEFAULT_WEBHOOK_MAX_AGE_SECS,
) -> bool:
    """Valida la firma de una notificación webhook de Mercado Pago.

    ``x-signature`` llega como ``ts=<timestamp>,v1=<hash>``. Manifest según la
    documentación: ``id:<data_id>;request-id:<x_request_id>;ts:<timestamp>;``
    firmado con el webhook_secret mediante HMAC-SHA256.

    En producción (``mercadopago_env == "production"``) o con
    ``strict_webhook_firma=True``, un ``MERCADOPAGO_WEBHOOK_SECRET`` vacío se
    rechaza (fail-closed). Además valida que ``ts`` sea numérico y no supere
    ``max_age_secs`` (protección contra replay).
    """
    if not settings.mercadopago_webhook_secret:
        if settings.mercadopago_env == "production" or settings.strict_webhook_firma:
            logger.error("verificar_firma: MERCADOPAGO_WEBHOOK_SECRET vacío en producción/estricto — rechazando")
            return False
        logger.warning("mercadopago_webhook_secret vacío; aceptando webhook sin verificar firma (modo pruebas)")
        return True
    if not x_signature:
        return False
    params = {}
    for parte in x_signature.split(","):
        if "=" in parte:
            k, _, v = parte.partition("=")
            params[k.strip()] = v.strip()
    ts = params.get("ts", "")
    firma = params.get("v1", "")
    if not ts or not firma:
        return False
    try:
        ts_num = float(ts)
    except ValueError:
        return False
    ts_secs = ts_num / 1000.0 if ts_num > 1e12 else ts_num
    if max_age_secs > 0 and abs(time.time() - ts_secs) > max_age_secs:
        logger.warning(f"Webhook MP rechazado: timestamp fuera de ventana ({ts})")
        return False
    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
    esperado = hmac.new(
        settings.mercadopago_webhook_secret.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(esperado.lower(), firma.lower())