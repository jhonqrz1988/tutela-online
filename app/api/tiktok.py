"""Punto de entrada del navegador para reenviar eventos a TikTok (Events API 2.0).

El clic en wa.me genera un ``event_id`` y hace ``sendBeacon('/api/tiktok/track')``
con el JSON del evento; este endpoint valida la entrada (sin confiar en el
cliente), hace un rate limit por IP para no quemar cuota ni el access_token, y
reenvía a TikTok. Nunca expone ni loguea el token.
"""
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.config import settings
from app.services import tiktok_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tiktok", tags=["tiktok"])

# Máximo de reenvíos por IP en la ventana, para que un tercero no pueda quemar
# la cuota de la API de TikTok usando nuestro access_token.
_LIMITE_POR_MINUTO = 30
_VENTANA_SEG = 60

# IP -> [timestamps] de envíos recientes.
_LIMIT_POR_IP: dict[str, list[float]] = {}

_MAX_EVENT_ID_LEN = 200
_MAX_TTCLID_TPP_LEN = 500

# Solo eventos que disparamos desde la landing; todo lo demás se rechaza.
_EVENTOS_PERMITIDOS = {"Contact"}


class _EventoTikTok(BaseModel):
    event: str | None = None
    event_id: str | None = None
    ttclid: str | None = None
    ttp: str | None = None


def _permite(ip: str, ahora: float | None = None) -> bool:
    """Rate limit por IP (ventana deslizante). True si puede enviar."""
    ahora = ahora if ahora is not None else time.time()
    lista = _LIMIT_POR_IP.setdefault(ip, [])
    lista[:] = [t for t in lista if ahora - t < _VENTANA_SEG]
    if len(lista) >= _LIMITE_POR_MINUTO:
        return False
    lista.append(ahora)
    return True


@router.get("")
def salud():
    """Health del canal TikTok. NUNCA devuelve el access_token."""
    return {"tiktok": "ok", "pixel_id": settings.tiktok_pixel_id}


@router.post("/track")
async def track(payload: _EventoTikTok, request: Request):
    ip = request.client.host if request.client else "desconocido"
    if not _permite(ip):
        return JSONResponse({"ok": False, "error": "rate limit"}, status_code=429)

    event = (payload.event or "").strip()
    event_id = (payload.event_id or "").strip()
    if event not in _EVENTOS_PERMITIDOS:
        logger.warning(f"TikTok track rechazó evento no permitido: {event!r}")
        return JSONResponse({"ok": False, "error": "evento no permitido"})
    if not event_id or len(event_id) > _MAX_EVENT_ID_LEN:
        return JSONResponse({"ok": False, "error": "event_id inválido"})
    if len(payload.ttclid or "") > _MAX_TTCLID_TPP_LEN or len(payload.ttp or "") > _MAX_TTCLID_TPP_LEN:
        return JSONResponse({"ok": False, "error": "parámetro demasiado largo"})

    # El timestamp NUNCA viene del cliente: lo pone el servidor (epoch UTC).
    event_time = int(datetime.now(timezone.utc).timestamp())
    ok = await tiktok_service.enviar_evento_tiktok(
        event,
        event_id,
        event_time,
        ttclid=payload.ttclid or None,
        ttp=payload.ttp or None,
    )
    return JSONResponse({"ok": ok})