import json
import logging

from sqlalchemy import select

from app.database import SessionLocal
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela

logger = logging.getLogger(__name__)


async def iniciar_radicacion(
    tutela_id: int,
    token_usuario: str | None = None,
) -> dict:
    """
    Inicia la radicacion MANUAL.

    Con radicacion manual, esta funcion solo marca la tutela como
    'pendiente_radicacion' y devuelve informacion para que el equipo
    admin la radique en el portal de la Rama Judicial.
    """
    session = SessionLocal()
    try:
        tutela = session.execute(
            select(Tutela).where(Tutela.id == tutela_id)
        ).scalar_one_or_none()
        if not tutela:
            return {"ok": False, "error": "Tutela no encontrada"}

        # Si ya estaba en estado de reintento, mantener; si no, poner pendiente
        if tutela.estado not in ("pendiente_radicacion", "fallida"):
            tutela.estado = "pendiente_radicacion"
            session.commit()

        datos = json.loads(tutela.datos_json or "{}")

        # Crear/actualizar registro de radicacion
        rad = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela_id)
        ).scalar_one_or_none()
        if not rad:
            rad = Radicacion(tutela_id=tutela.id, estado="pendiente_radicacion")
            session.add(rad)
        else:
            rad.estado = "pendiente_radicacion"
        session.commit()

        # Preparar datos para radicacion manual
        dr = {
            "tipo_tutela": datos.get("tipo", "salud"),
            "ciudad": datos.get("ciudad", ""),
            "accionante_nombre": datos.get("accionante_nombre", ""),
            "accionante_cedula": datos.get("accionante_cedula", ""),
            "accionante_telefono": datos.get("accionante_telefono", ""),
            "accionante_email": datos.get("accionante_email", ""),
            "accionado": datos.get("accionado", ""),
            "derechos": ", ".join(datos.get("derechos_vulnerados", [])),
            "pdf_path": tutela.pdf_path,
        }

        logger.info(f"Radicacion manual pendiente para tutela {tutela_id}")

        return {
            "ok": True,
            "manual": True,
            "message": "Radicacion manual pendiente. El equipo admin debe radicar en el portal y registrar el numero via /admin.",
            "datos_para_radicar": dr,
            "radicacion_id": rad.id,
        }

    except Exception as e:
        logger.error(f"Error preparando radicacion manual tutela {tutela_id}: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        session.close()