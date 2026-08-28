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
async def debug_enviar_raw():
    from app.services.whatsapp_service import _enviar_meta_texto, enviar_texto, enviar_botones
    import traceback
    results = {}
    try:
        results["enviar_texto_ok"] = enviar_texto("573106386975", "Test directo ok")
    except Exception as e:
        results["enviar_texto_error"] = str(e)
        results["traceback_enviar"] = traceback.format_exc()
    try:
        results["enviar_botones_ok"] = enviar_botones("573106386975", "Test botones", [("btn1", "Opcion 1")])
    except Exception as e:
        results["enviar_botones_error"] = str(e)
        results["traceback_botones"] = traceback.format_exc()
    return results
