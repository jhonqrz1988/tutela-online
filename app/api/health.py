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
