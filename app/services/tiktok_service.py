"""Canal server-side (Events API 2.0) del pixel de TikTok.

El navegador genera un ``event_id`` al hacer clic en wa.me; ese mismo id se usa
en el Pixel SDK (``ttq.track('Contact', {...}, {event_id})``) y se reenvía aquí
para deduplicar: TikTok combina eventos de Pixel y Events API cuando
(event_id, event, pixel_code) coinciden.

Estos envíos nunca deben romper el flujo ni loguear el access_token.
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TIKTOK_EVENTS_API_URL = "https://business-api.tiktok.com/open_api/v1.3/event/track/"


def _payload_evento(
    event: str,
    event_id: str,
    event_time: int,
    ttclid: str | None = None,
    ttp: str | None = None,
) -> dict:
    """Construye el body de un evento web para Events API 2.0.

    Solo se incluyen ``ttclid``/``ttp`` cuando tienen valor (TikTok los usa
    para hacer match con el ttclid del clic del anuncio). ``test_event_code``
    se agrega solo si está configurado (modo pruebas).
    """
    user: dict = {}
    if ttclid:
        user["ttclid"] = ttclid
    if ttp:
        user["ttp"] = ttp
    evento = {
        "event": event,
        "event_time": event_time,
        "event_id": event_id,
        "user": user,
    }
    payload: dict = {
        "event_source": "web",
        "event_source_id": settings.tiktok_pixel_id,
        "data": [evento],
    }
    if settings.tiktok_test_event_code:
        payload["test_event_code"] = settings.tiktok_test_event_code
    return payload


async def enviar_evento_tiktok(
    event: str,
    event_id: str,
    event_time: int,
    ttclid: str | None = None,
    ttp: str | None = None,
) -> bool:
    """Envía un evento web a TikTok via Events API 2.0.

    Tolerante: nunca lanza excepción. Sin token o pixel configurados devuelve
    False sin hacer ninguna petición (no-op en desarrollo).
    """
    if not settings.tiktok_access_token or not settings.tiktok_pixel_id:
        return False
    payload = _payload_evento(event, event_id, event_time, ttclid, ttp)
    headers = {"Access-Token": settings.tiktok_access_token}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(TIKTOK_EVENTS_API_URL, json=payload, headers=headers)
        if r.status_code == 200:
            data = r.json()
            if data.get("code") == 0:
                return True
            logger.error(f"TikTok track code={data.get('code')} msg={data.get('message')}")
        else:
            logger.error(f"TikTok track http {r.status_code}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"TikTok track exception: {e}")
    return False