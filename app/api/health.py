from fastapi import APIRouter, Depends
from sqlalchemy import text
import httpx
import os
from app.database import get_session

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/debug/meta-token")
async def debug_meta_token():
    token = os.environ.get("META_ACCESS_TOKEN", "")
    phone_id = os.environ.get("META_PHONE_NUMBER_ID", "")
    if not token:
        return {"error": "META_ACCESS_TOKEN not set"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://graph.facebook.com/v21.0/{phone_id}",
                params={"access_token": token},
            )
            return {"status": r.status_code, "body": r.text[:500], "phone_id": phone_id}
    except Exception as e:
        return {"error": str(e)}


@router.get("/debug/send-test")
async def debug_send_test():
    from app.services.whatsapp_service import enviar_texto
    try:
        result = enviar_texto("573106386975", "🔧 Test de diagnostico desde Render")
        return {"enviar_texto_result": result}
    except Exception as e:
        import traceback
        return {"error": str(e), "traceback": traceback.format_exc()}


@router.get("/debug/db-test")
async def debug_db_test(session=Depends(get_session)):
    try:
        result = session.execute(text("SELECT count(*) FROM users"))
        count = result.scalar()
        return {"users_count": count}
    except Exception as e:
        return {"error": str(e)}


@router.get("/debug/procesar-hola")
async def debug_procesar_hola(session=Depends(get_session)):
    import traceback
    from app.api.webhook_whatsapp import procesar_mensaje
    try:
        resultado = await procesar_mensaje(session, "5511998887777", "Hola", 0, "", False)
        return {"resultado": resultado}
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


@router.get("/debug/enviar-raw")
async def debug_enviar_raw(destino: str = "573003838218"):
    from app.services.whatsapp_service import _enviar_meta_texto, enviar_texto, enviar_botones, _meta_headers
    import httpx as _httpx
    import traceback
    
    results = {}
    
    # Test raw Meta API call with full response body
    try:
        from app.config import settings
        url = f"https://graph.facebook.com/v25.0/{settings.meta_phone_number_id}/messages"
        r = _httpx.post(url, json={
            "messaging_product": "whatsapp",
            "to": destino,
            "type": "text",
            "text": {"body": "Test de diagnostico"}
        }, headers=_meta_headers(), timeout=15)
        results["meta_raw_status"] = r.status_code
        results["meta_raw_body"] = r.text[:500]
        results["meta_raw_ok"] = r.is_success
    except Exception as e:
        results["meta_raw_error"] = str(e)
        results["meta_traceback"] = traceback.format_exc()
    
    return results


@router.post("/debug/simular-webhook")
async def debug_simular_webhook(session=Depends(get_session)):
    """Simula exactamente lo que hace webhook_meta POST pero retorna todo el detalle"""
    import traceback
    from app.api.webhook_whatsapp import procesar_mensaje
    body_text = "Hola"
    telefono = "573106386975"
    try:
        logger.info(f"DEBUG-SIM: llamando procesar_mensaje para {telefono}")
        respuesta = await procesar_mensaje(session, telefono, body_text, 0, "", False)
        logger.info(f"DEBUG-SIM: respuesta completa: {respuesta}")
        resp = {
            "ok": True,
            "respuesta_type": type(respuesta).__name__,
            "respuesta_keys": list(respuesta.keys()) if isinstance(respuesta, dict) else None,
            "respuesta": respuesta,
        }
        return resp
    except Exception as e:
        return {"ok": False, "error": str(e), "traceback": traceback.format_exc()}


import logging
logger = logging.getLogger("debug")
