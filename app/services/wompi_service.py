import hashlib
import hmac
import logging
from urllib.parse import urlencode

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
    params = {
        "public-key": settings.wompi_public_key,
        "currency": currency,
        "amount-in-cents": str(amount),
        "reference": reference,
        "redirect-url": f"{settings.app_url}/pago/resultado?reference={reference}",
        "signature:integrity": signature,
    }
    return "https://checkout.wompi.co/p/?" + urlencode(params)


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
    timestamp = evento.get("timestamp") or signature.get("timestamp")
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
