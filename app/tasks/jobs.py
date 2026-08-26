import asyncio
import logging
from datetime import datetime

from sqlalchemy import select

from app.database import SessionLocal
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela
from app.services.radicacion_service import iniciar_radicacion

logger = logging.getLogger(__name__)


def es_horario_habil() -> bool:
    """Verifica si la hora actual está dentro del horario hábil del portal.

    Horario: 8am-12pm y 2pm-4pm, lunes a viernes.
    """
    ahora = datetime.now()
    if ahora.weekday() > 4:  # 5=sábado, 6=domingo
        return False
    hora = ahora.hour
    return (8 <= hora < 12) or (14 <= hora < 16)


def procesar_cola_radicacion():
    """Procesa todas las tutelas pendientes de radicación.

    Solo ejecuta si estamos en horario hábil.
    Tutelas con estado 'esperando_codigo_email' se saltan (esperan input del usuario).
    """
    if not es_horario_habil():
        logger.info("Fuera de horario hábil, saltando cola de radicación")
        return

    session = SessionLocal()
    try:
        tutelas = session.execute(
            select(Tutela).where(
                Tutela.estado.in_(["pendiente_radicacion", "fallida"])
            )
        ).scalars().all()
    finally:
        session.close()

    if not tutelas:
        logger.info("Cola de radicación vacía")
        return

    logger.info(f"Procesando {len(tutelas)} tutelas en cola de radicación")

    for tutela in tutelas:
        try:
            # Verificar si ya tiene una radicación en curso esperando código
            rad = session.execute(
                select(Radicacion).where(Radicacion.tutela_id == tutela.id)
            ).scalar_one_or_none()
            if rad and rad.estado == "esperando_codigo_email":
                logger.info(f"Tutela {tutela.id} esperando código de email, saltando")
                continue

            # Reintentar solo si no tiene muchos intentos fallidos
            intentos = rad.intentos if rad else 0
            if intentos >= 3:
                logger.warning(f"Tutela {tutela.id} agotó intentos ({intentos}), saltando")
                continue

            logger.info(f"Iniciando radicación automática para tutela {tutela.id}")
            asyncio.run(iniciar_radicacion(tutela.id))

        except Exception as e:
            logger.error(f"Error procesando tutela {tutela.id} en cola: {e}")
