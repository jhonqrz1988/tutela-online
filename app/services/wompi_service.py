import hashlib
import hmac
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

WOMPI_EVENT_APPROVED = "APPROVED"


def wompi_base_url() -> str:
    if settings.wompi_env == "production":
        return "https://production.wompi.co/v1"
    return "https://sandbox.wompi.co/v1"


def firma_integridad(reference: str, amount_in_cents: int, currency: str) -> str:
    """Firma de integridad para el checkout de Wompi: SHA256(reference+amount+currency+secret)."""
    cadena = f"{reference}{amount_in_cents}{currency}{settings.wompi_integrity_secret}"
    return hashlib.sha256(cadena.encode("utf-8")).hexdigest()


def url_checkout(tutela_id: int, reference: str) -> str:
    """Construye la URL del checkout hosted de Wompi."""
    amount = settings.wompi_amount_cents
    currency = settings.wompi_currency
    signature = firma_integridad(reference, amount, currency)
    redirect = f"{settings.app_url}/pago/resultado?reference={reference}"
    return (
        "https://checkout.wompi.co/p/"
        f"?public-key={settings.wompi_public_key}"
        f"&currency={currency}"
        f"&amount-in-cents={amount}"
        f"&reference={reference}"
        f"&redirect-url={redirect}"
        f"&signature:integrity={signature}"
    )


async def crear_transaccion(
    tutela_id: int, reference: str, email: str | None = None
) -> dict:
    """Crea una transacción Wompi (para el botón de pago) y devuelve la URL del checkout."""
    if not settings.wompi_private_key:
        return {
            "ok": False,
            "error": "Wompi no configurado (falta wompi_private_key)",
            "checkout_url": None,
        }
    amount = settings.wompi_amount_cents
    currency = settings.wompi_currency
    signature = firma_integridad(reference, amount, currency)
    payload = {
        "amount_in_cents": amount,
        "currency": currency,
        "reference": reference,
        "customer_email": email or "cliente@tutela.co",
        "payment_method": {"type": "CARD"},
        "signature": {
            "integrity": signature,
            "properties": ["amount_in_cents", "reference", "currency"],
        },
        "redirect_url": f"{settings.app_url}/pago/resultado?reference={reference}",
    }
    headers = {"Authorization": f"Bearer {settings.wompi_private_key}"}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{wompi_base_url()}/transactions", json=payload, headers=headers)
        if r.status_code not in (200, 201):
            logger.error(f"Wompi crear_transaccion falló: {r.status_code} {r.text[:300]}")
            return {"ok": False, "error": f"HTTP {r.status_code}", "checkout_url": None}
        data = r.json()
        checkout_url = (
            data.get("data", {}).get("payment_method", {}).get("extra", {}).get("checkout_url")
            or f"https://checkout.wompi.co/p/{data.get('data', {}).get('id', '')}"
        )
        return {
            "ok": True,
            "transaction_id": data.get("data", {}).get("id"),
            "checkout_url": checkout_url,
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Wompi crear_transaccion error: {e}")
        return {"ok": False, "error": str(e), "checkout_url": None}


async def consultar_transaccion(transaction_id: str) -> dict | None:
    """Consulta el estado de una transacción en Wompi (respaldo si el webhook falla)."""
    if not settings.wompi_private_key or not transaction_id:
        return None
    headers = {"Authorization": f"Bearer {settings.wompi_private_key}"}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{wompi_base_url()}/transactions/{transaction_id}", headers=headers)
        if r.status_code == 200:
            data = r.json().get("data", {})
            return {
                "id": data.get("id"),
                "status": data.get("status"),
                "reference": data.get("reference"),
                "amount_in_cents": data.get("amount_in_cents"),
            }
    except Exception as e:  # noqa: BLE001
        logger.error(f"Wompi consultar_transaccion error: {e}")
    return None


def verificar_evento(evento: dict, checksum_header: str | None) -> bool:
    """Valida la autenticidad de un evento de Wompi con el checksum SHA256."""
    if not settings.wompi_events_secret:
        logger.warning("wompi_events_secret vacío; no se puede verificar el evento")
        return False
    signature = evento.get("signature", {})
    checksum = (checksum_header or "").strip() or signature.get("checksum", "")
    properties = signature.get("properties", [])
    timestamp = signature.get("timestamp")
    if not properties or not timestamp or not checksum:
        logger.error("Evento Wompi sin signature/properties/timestamp")
        return False
    valores = []
    for prop in properties:
        partes = prop.split(".")
        valor = evento.get("data", {})
        for p in partes:
            if not isinstance(valor, dict):
                break
            valor = valor.get(p)
        valores.append(str(valor) if valor is not None else "")
    cadena = "".join(valores) + str(timestamp) + settings.wompi_events_secret
    esperado = hashlib.sha256(cadena.encode("utf-8")).hexdigest()
    return hmac.compare_digest(esperado.lower(), checksum.lower())
