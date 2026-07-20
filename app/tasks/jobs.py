import asyncio

from sqlalchemy import select

from app.database import SessionLocal
from app.models.tutela import Tutela
from app.services.radicacion_service import iniciar_radicacion


def procesar_cola_radicacion():
    session = SessionLocal()
    try:
        tutelas = session.execute(
            select(Tutela).where(Tutela.estado == "pendiente_radicacion")
        ).scalars().all()
    finally:
        session.close()

    for tutela in tutelas:
        asyncio.run(iniciar_radicacion(tutela.id))
