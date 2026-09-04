from fastapi import APIRouter
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal

router = APIRouter()


@router.get("/health")
async def health():
    """Healthcheck: valida que el proceso responde y que la BD está accesible.

    El plan gratis de Render duerme el servicio por inactividad; un monitor
    externo (p. ej. UptimeRobot) debe hacer ping a esta ruta cada pocos
    minutos para mantenerlo despierto.
    """
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        return {
            "status": "degraded",
            "database": "unreachable",
            "config": _config_diagnostico(),
        }
    return {"status": "ok", "database": "ok", "config": _config_diagnostico()}


def _config_diagnostico() -> dict:
    """Diagnóstico de configuración para verificar el despliegue sin exponer secretos.

    Los tokens/NIT solo se reportan como presentes (True/False); nunca se
    muestran sus valores.
    """
    return {
        "app_url": settings.app_url,
        "whatsapp_provider": settings.whatsapp_provider,
        "webhook_auth_token": bool(settings.webhook_auth_token),
        "strict_webhook_firma": settings.strict_webhook_firma,
        "ai_provider": settings.ai_provider,
        "ai_chat_model": settings.ai_chat_model,
        "ai_api_key": bool(settings.ai_api_key),
        "meta_phone_number_id": bool(settings.meta_phone_number_id),
        "meta_access_token": bool(settings.meta_access_token),
        "meta_app_secret": bool(settings.meta_app_secret),
        "database_url": settings.database_url.split(":")[0].split("+")[0],
        "mercadopago_access_token": bool(settings.mercadopago_access_token),
        "mercadopago_webhook_secret": bool(settings.mercadopago_webhook_secret),
        "mercadopago_env": settings.mercadopago_env,
        "mercadopago_amount": settings.mercadopago_amount,
        "simulate_bot": settings.simulate_bot,
        "enable_scheduler": settings.enable_scheduler,
        "storage_dir": settings.storage_dir,
    }
