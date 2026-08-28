from fastapi import APIRouter
import httpx
import os

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
