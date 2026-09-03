import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.database import SessionLocal
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela
from app.services.radicacion_service import iniciar_radicacion

logger = logging.getLogger(__name__)

BOGOTA_TZ = ZoneInfo("America/Bogota")


def es_horario_habil(ahora: datetime | None = None) -> bool:
    """Verifica si la hora actual (zona Bogotá) está dentro del horario hábil.

    Horario: 8am-12pm y 2pm-4pm, lunes a viernes.
    """
    if ahora is None:
        ahora = datetime.now(BOGOTA_TZ)
    if ahora.weekday() > 4:  # 5=sábado, 6=domingo
        return False
    hora = ahora.hour
    return (8 <= hora < 12) or (14 <= hora < 16)


def procesar_cola_radicacion():
    """Procesa todas las tutelas pendientes de radicación.

    Solo ejecuta si estamos en horario hábil (Bogotá).
    Tutelas con estado 'esperando_codigo_email' se saltan (esperan input del usuario).
    Cada tutela se procesa con su propia sesión para evitar usar una sesión cerrada.
    """
    if not es_horario_habil():
        logger.info("Fuera de horario hábil, saltando cola de radicación")
        return

    ids_tutelas = []
    session = SessionLocal()
    try:
        tutelas = session.execute(
            select(Tutela).where(
                Tutela.estado.in_(["pendiente_radicacion", "fallida", "pendiente"])
            )
        ).scalars().all()
        ids_tutelas = [t.id for t in tutelas]
    finally:
        session.close()

    if not ids_tutelas:
        logger.info("Cola de radicación vacía")
        return

    logger.info(f"Procesando {len(ids_tutelas)} tutelas en cola de radicación")

    for tutela_id in ids_tutelas:
        s2 = SessionLocal()
        try:
            # Verificar si ya tiene una radicación en curso esperando código
            rad = s2.execute(
                select(Radicacion).where(Radicacion.tutela_id == tutela_id)
            ).scalar_one_or_none()
            if rad and rad.estado == "esperando_codigo_email":
                logger.info(f"Tutela {tutela_id} esperando código de email, saltando")
                continue

            # Reintentar solo si no tiene muchos intentos fallidos
            intentos = rad.intentos if rad else 0
            if intentos >= 3:
                logger.warning(f"Tutela {tutela_id} agotó intentos ({intentos}), saltando")
                continue

            logger.info(f"Iniciando radicación automática para tutela {tutela_id}")
            asyncio.run(iniciar_radicacion(tutela_id))
        except Exception as e:
            logger.error(f"Error procesando tutela {tutela_id} en cola: {e}")
        finally:
            s2.close()
