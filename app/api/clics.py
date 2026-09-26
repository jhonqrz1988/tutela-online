"""Registro server-side de clics en wa.me (endpoint /api/click-wa).

El clic de un humano en cualquier botón/enlace de la landing se informa con
``sendBeacon('/api/click-wa')`` para medir cuántos visitantes realmente
intentaron abrir el chat de WhatsApp, independiente de si la conversación
llega al webhook. Rechaza query strings excesivas y aplica rate limit por IP.
"""
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.services import visitas_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["clics"])

# Máximo de clics por IP en la ventana: un humano puede hacer varios clics
# (hero, precio, flotante), pero no cientos por minuto.
_LIMITE_POR_MINUTO = 60
_VENTANA_SEG = 60
_LIMIT_POR_IP: dict[str, list[float]] = {}

_MAX_QUERY_LEN = 2000
_MAX_UBICACION_LEN = 100


class _ClicWhatsApp(BaseModel):
    query: str = ""
    ubicacion: str = ""


def _permite(ip: str, ahora: float | None = None) -> bool:
    ahora = ahora if ahora is not None else time.time()
    lista = _LIMIT_POR_IP.setdefault(ip, [])
    lista[:] = [t for t in lista if ahora - t < _VENTANA_SEG]
    if len(lista) >= _LIMITE_POR_MINUTO:
        return False
    lista.append(ahora)
    return True


@router.post("/click-wa")
async def clic_whatsapp(payload: _ClicWhatsApp | None, request: Request):
    if payload is None:
        payload = _ClicWhatsApp()

    ip = request.client.host if request.client else "desconocido"
    if not _permite(ip):
        return JSONResponse({"ok": False, "error": "rate limit"}, status_code=429)

    if len(payload.query or "") > _MAX_QUERY_LEN:
        return JSONResponse(
            {"ok": False, "error": "query demasiado larga"}, status_code=400
        )

    user_agent = request.headers.get("user-agent", "")
    visitas_service.registrar_clic_whatsapp(payload.query, payload.ubicacion, user_agent)
    return {"ok": True}