"""Seguimiento de personas que quedaron atrapadas en el flujo de WhatsApp.

Con el comando SALIR ya arreglado (commit 99236ca), los usuarios que quedaron
atrapados ANTES del fix siguen bloqueados. Este módulo permite:

1. ``listar_atrapados()``: quién tiene una tutela en curso, no llegó a un estado
   terminal y lleva más de N horas sin escribir (ya fuera de la ventana de 24 h
   de Meta, donde ni un mensaje normal llegaría).
2. ``desbloquear_usuario()``: reinicia el flujo de esa persona para que vuelva a
   empezar si escribe de nuevo.

No envía mensajes por su cuenta: eso requiere plantillas aprobadas por Meta
(ver `plantillas` y las notas de la ventana de 24 h).
"""
import datetime
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import MensajeWhatsApp

logger = logging.getLogger(__name__)

# Estados en los que el proceso ya terminó: el usuario no está "atrapado".
ESTADOS_TERMINALES = frozenset({
    "radicada", "completado", "fallida",
})

HORAS_INACTIVO_POR_DEFECTO = 24


def _utc_naive() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _iso(valor) -> str | None:
    """Fecha en hora de Bogotá (la BD guarda UTC)."""
    if valor is None:
        return None
    if not isinstance(valor, datetime.datetime):
        return str(valor)
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=datetime.UTC)
    return valor.astimezone(ZoneInfo("America/Bogota")).strftime("%Y-%m-%d %H:%M")


def listar_atrapados(session, horas_inactivo: int = HORAS_INACTIVO_POR_DEFECTO) -> list[dict]:
    """Tutelas en curso cuyo usuario lleva más de ``horas_inactivo`` sin escribir."""
    horas = max(1, int(horas_inactivo or HORAS_INACTIVO_POR_DEFECTO))
    corte = _utc_naive() - datetime.timedelta(hours=horas)

    # Tutelas en curso (no terminales), ordenadas por más antigua primero.
    tutelas = session.execute(
        select(Tutela)
        .where(Tutela.estado.notin_(ESTADOS_TERMINALES))
        .order_by(Tutela.created_at.asc())
    ).scalars().all()

    atrapados: list[dict] = []
    for tutela in tutelas:
        user = tutela.user
        if not user or not user.telefono:
            continue
        # Última actividad real = mensaje más reciente de ese número.
        ultimo = session.execute(
            select(func.max(MensajeWhatsApp.created_at))
            .where(MensajeWhatsApp.from_number == user.telefono)
        ).scalar()
        # Si no hay mensajes, la antigüedad de la tutela es la referencia.
        referencia = ultimo or tutela.created_at
        if referencia is not None and referencia >= corte:
            # Escribió recientemente: no está atrapado.
            continue
        atrapados.append({
            "user_id": user.id,
            "tutela_id": tutela.id,
            "telefono": user.telefono,
            "nombre": user.nombre or "",
            "estado": tutela.estado,
            "creada": _iso(tutela.created_at),
            "ultima_actividad": _iso(referencia),
        })

    # Más inactivo primero.
    atrapados.sort(key=lambda item: item["ultima_actividad"] or "", reverse=False)
    return atrapados


def desbloquear_usuario(session, user_id: int) -> dict:
    """Reinicia el flujo de un usuario: borra su tutela en curso y lo deja en 'nuevo'.

    Es la recuperación manual de quien quedó atrapado antes del comando SALIR.
    No borra el histórico de mensajes.
    """
    user = session.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        return {"ok": False, "error": "Usuario no encontrado"}

    try:
        from app.api.webhook_whatsapp import _liberar_tutelas

        tutela_ids = session.execute(
            select(Tutela.id).where(Tutela.user_id == user.id)
        ).scalars().all()
        # Desancla mensajes/envíos y borra citas/radicaciones ANTES del DELETE:
        # sin esto el borrado de la tutela revierte la transacción entera
        # (ForeignKeyViolation) y el desbloqueo no ocurre.
        _liberar_tutelas(session, tutela_ids)
        for t in session.execute(
            select(Tutela).where(Tutela.user_id == user.id)
        ).scalars():
            session.delete(t)

        user.estado = "nuevo"
        user.consentimiento = False
        user.consentimiento_version = None
        user.consentimiento_timestamp = None
        session.commit()

        logger.info(
            "Admin desbloqueó usuario %s (tutelas borradas: %s)", user.telefono, len(tutela_ids)
        )
        return {"ok": True, "telefono": user.telefono, "tutelas_borradas": len(tutela_ids)}
    except Exception as e:  # noqa: BLE001
        logger.error("Error desbloqueando usuario %s: %s", user_id, e, exc_info=True)
        session.rollback()
        return {"ok": False, "error": str(e)}