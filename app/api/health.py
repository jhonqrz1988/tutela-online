from fastapi import APIRouter
from sqlalchemy import text

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
        return {"status": "degraded", "database": "unreachable"}
    return {"status": "ok", "database": "ok"}
