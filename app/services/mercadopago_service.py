import hashlib
import hmac
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


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
            return {
                "id": d.get("id"),
                "status": d.get("status"),
                "external_reference": d.get("external_reference"),
            }
    except Exception as e:  # noqa: BLE001
        logger.error(f"MercadoPago consultar_pago exception: {e}")
    return None


def verificar_firma(x_signature: str, x_request_id: str, data_id: str) -> bool:
    """Valida la firma de una notificación webhook de Mercado Pago.

    ``x-signature`` llega como ``ts=<timestamp>,v1=<hash>``. Manifest según la
    documentación: ``id:<data_id>;request-id:<x_request_id>;ts:<timestamp>;``
    firmado con el webhook_secret mediante HMAC-SHA256.
    """
    if not settings.mercadopago_webhook_secret:
        logger.warning("mercadopago_webhook_secret vacío; no se puede verificar el evento")
        return False
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
    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
    esperado = hmac.new(
        settings.mercadopago_webhook_secret.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(esperado.lower(), firma.lower())