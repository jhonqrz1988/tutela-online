"""Recordatorios a usuarios que dejaron el flujo a medias.

Idea: la ventana de servicio de 24 h de Meta se abre cuando el USUARIO escribe.
Mientras esa ventana esté abierta se puede mandar texto libre (sin plantillas
aprobadas). Por eso el recordatorio solo se envía dentro de la ventana:

    ahora - última_mensaje >= HORAS_RECORDATORIO   (ya leva rato parado)
    ahora - última_mensaje <= 24 h                  (ventana de Meta abierta)

Fuera de las 24 h haría falta una plantilla aprobada por Meta, así que
``candidatos()`` excluye a cualquiera que lleve más de 24 h sin escribir. Nunca
se manda recordatorio a quien pidió "detener/stop".

Criterios de elegibilidad (todos deben cumplirse):
  * Consentimiento de datos vigente (sin consentimiento no hay nada que recordar).
  * ``no_mensajes_proactivos`` falso (opt-out respetado).
  * Al menos una tutela NO terminal (el proceso sigue abierto).
  * Silencio de al menos ``horas`` y como máximo ``ventana_horas``.
  * No haber recibido ya un recordatorio para el mismo estado dentro del
    enfriamiento (evita repetirnos).

El recordatorio NO reinicia nada: solo recuerda que puede seguir. Si responde,
la ventana se reabre y el bot retoma desde el estado guardado.
"""
import datetime
import logging

from sqlalchemy import func, select

from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import EnvioWhatsApp, MensajeWhatsApp
from app.services.seguimiento_service import ESTADOS_TERMINALES

logger = logging.getLogger(__name__)

# Silencio mínimo antes de recordar (horas).
HORAS_RECORDATORIO = 4
# Ventana de servicio de Meta (horas). Máximo absoluto: más allá no llega nada
# sin plantilla aprobada.
VENTANA_META_HORAS = 24
# Enfriamiento por estado: no repetir el mismo recordatorio antes de esto.
ENFRIAMIENTO_HORAS = 10
# Tope de envíos por corrida (evita una ráfaga si algo sale mal).
MAX_POR_CORRIDA = 50
# Candado para los envíos a mano (botón del panel / caja de prueba). El job
# automático ya usa ENFRIAMIENTO_HORAS; esto cubre los clics, que son
# instantáneos y podían repetirse tantas veces como se pulsara (bug: 3
# recordatorios a la misma persona).
MINUTOS_ENTRE_ENVIO = 15

TEXTO_RECORDATORIO = (
    "Hola 👋 Tu solicitud de TutelApp quedó a la espera de un dato. "
    "Si quieres retomarla, escríbenos por aquí. Seguimos ayudándote con tu tutela."
)

BOTON_CONTINUAR = ("continuar", "Continuar mi tutela")


def _utc_naive() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def texto_recordatorio() -> str:
    return TEXTO_RECORDATORIO


def _ultima_actividad(session, telefono: str):
    return session.execute(
        select(func.max(MensajeWhatsApp.created_at)).where(
            MensajeWhatsApp.from_number == telefono
        )
    ).scalar()


def candidatos(
    session,
    horas: int = HORAS_RECORDATORIO,
    ventana_horas: int = VENTANA_META_HORAS,
    enfriamiento_horas: int = ENFRIAMIENTO_HORAS,
) -> list[dict]:
    """Usuarios a los que sí se les puede (y debe) recordar ahora mismo."""
    horas = max(1, int(horas))
    ventana_horas = max(horas + 1, int(ventana_horas))
    enfriamiento_horas = max(1, int(enfriamiento_horas))
    ahora = _utc_naive()

    # Tutelas abiertas: no terminales. El estado manda para el enfriamiento.
    abiertas = session.execute(
        select(Tutela).where(Tutela.estado.notin_(ESTADOS_TERMINALES))
    ).scalars().all()
    if not abiertas:
        return []

    por_usuario: dict[int, dict] = {}
    for t in abiertas:
        if t.user is None or not t.user.telefono or not t.user.consentimiento:
            continue
        if t.user.no_mensajes_proactivos:
            continue
        actual = por_usuario.get(t.user.id)
        # Si tiene varias abiertas, la más reciente manda (es la que vive).
        if actual is None or (t.created_at or ahora) > (actual["tutela"].created_at or ahora):
            por_usuario[t.user.id] = {"tutela": t, "user": t.user}

    elegibles: list[dict] = []
    for item in por_usuario.values():
        user: User = item["user"]
        tutela: Tutela = item["tutela"]
        referencia = _ultima_actividad(session, user.telefono) or tutela.created_at
        if referencia is None:
            continue
        silencio = (ahora - referencia).total_seconds() / 3600.0
        if silencio < horas:
            continue
        # Fuera de la ventana de Meta no llega: se traga el mensaje.
        if silencio > ventana_horas:
            continue
        # Enfriamiento: mismo estado + usuario que ya proverbos.
        if (
            user.recordatorio_enviado_at is not None
            and user.recordatorio_estado == tutela.estado
            and (ahora - user.recordatorio_enviado_at).total_seconds() / 3600.0 < enfriamiento_horas
        ):
            continue
        elegibles.append({
            "user_id": user.id,
            "tutela_id": tutela.id,
            "telefono": user.telefono,
            "nombre": user.nombre or "",
            "estado": tutela.estado,
            "silencio_horas": round(silencio, 1),
        })

    # A quien más lleva parado, primero.
    elegibles.sort(key=lambda x: x["silencio_horas"], reverse=True)
    return elegibles


def _estado_real_de_usuario(session, user) -> str:
    """Estado de la tutela abierta del usuario (para el enfriamiento).

    Importa que sea el estado REAL y no una etiqueta fija: el enfriamiento
    compara ``recordatorio_estado == tutela.estado``, así que marcar
    "prueba_manual" haces que nunca coincida y el usuario recibe el mensaje
    una y otra vez (bug: 3 recordatorios a la misma persona).
    """
    if user is None:
        return ""
    tutela = session.execute(
        select(Tutela)
        .where(Tutela.user_id == user.id, Tutela.estado.notin_(ESTADOS_TERMINALES))
        .order_by(Tutela.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return tutela.estado if tutela is not None else ""


def _registrar_envio(session, telefono: str, user) -> None:
    """Guarda el wamid del recordatorio como EnvioWhatsApp.

    El accumulate ``_WAMIDS`` de whatsapp_service solo se vuelca a la base
    dentro del webhook (``_registrar_envio``). Los envíos que nacen del panel o
    del job nunca pasaban por ahí, así que quedaban invisibles en el reporte de
    entrega: no había forma de saber cuántos se habían mandado a mano.
    """
    from app.services.whatsapp_service import reiniciar_wamids_envio, wamids_envio

    pendientes = wamids_envio()
    if not pendientes:
        return
    tutela_id = None
    if user is not None:
        tutela = session.execute(
            select(Tutela)
            .where(Tutela.user_id == user.id)
            .order_by(Tutela.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        tutela_id = tutela.id if tutela is not None else None
    for item in pendientes:
        session.add(EnvioWhatsApp(
            wamid=item["wamid"][:255],
            from_number=(telefono or "")[:20],
            tutela_id=tutela_id,
        ))
    session.commit()
    # Se vacía siempre: si no, el wamid se arrastra al siguiente envío.
    reiniciar_wamids_envio()


def enviar_recordatorio(
    session, telefono: str, estado: str = "", forzar: bool = False,
) -> bool:
    """Manda el recordatorio con botón y marca el envío (enfriamiento + wamid).

    ``forzar=True`` omite el candado de repeats (lo usa el dueño al testear).
    """
    from app.services.whatsapp_service import enviar_botones

    user = session.execute(
        select(User).where(User.telefono == telefono)
    ).scalar_one_or_none()

    # Candado de servidor: el botón del navegador se deshabilita, pero el POST
    # se podía repetir. Sin esto, N clics = N mensajes a la misma persona.
    if not forzar and user is not None and user.recordatorio_enviado_at is not None:
        transcurrido = (_utc_naive() - user.recordatorio_enviado_at).total_seconds() / 60.0
        if transcurrido < MINUTOS_ENTRE_ENVIO:
            logger.info(
                "Recordatorio omitido a %s: ya se envió hace %.0f min",
                telefono[:6] + "***", transcurrido,
            )
            return False

    ok = enviar_botones(telefono, TEXTO_RECORDATORIO, [BOTON_CONTINUAR])
    if ok:
        # Primero la marca de enfriamiento, en su propia transacción: si el
        # registro del wamid falla, NO puede perderse la marca (si se perdía, el
        # usuario volvía a entrar como candidato y recibía otro recordatorio).
        if user is not None:
            try:
                user.recordatorio_enviado_at = _utc_naive()
                user.recordatorio_estado = _estado_real_de_usuario(session, user) or None
                session.commit()
            except Exception as e:  # noqa: BLE001 - marcar no debe romper el envío
                logger.error(
                    "No se pudo marcar el recordatorio de %s: %s", telefono[:6] + "***", e
                )
                session.rollback()
        try:
            _registrar_envio(session, telefono, user)
        except Exception as e:  # noqa: BLE001 - contabilidad aparte del envío
            logger.warning(
                "Recordatorio enviado a %s pero sin registrar el wamid: %s",
                telefono[:6] + "***", e,
            )
            session.rollback()
    return ok


def enviar_recordatorios(
    session,
    horas: int = HORAS_RECORDATORIO,
    ventana_horas: int = VENTANA_META_HORAS,
    enfriamiento_horas: int = ENFRIAMIENTO_HORAS,
    maximo: int = MAX_POR_CORRIDA,
) -> dict:
    """Corrida del job: manda recordatorios a todos los elegibles."""
    lista = candidatos(session, horas, ventana_horas, enfriamiento_horas)[: max(1, maximo)]
    enviados = 0
    fallidos = 0
    for fila in lista:
        try:
            if enviar_recordatorio(session, fila["telefono"], fila["estado"]):
                enviados += 1
                logger.info(
                    "Recordatorio enviado (silencio %.1fh, estado=%s)",
                    fila["silencio_horas"], fila["estado"],
                )
            else:
                fallidos += 1
        except Exception as e:  # noqa: BLE001 - un fallo no corta la corrida
            fallidos += 1
            logger.error("Error mandando recordatorio: %s", e)
    if enviados or fallidos:
        logger.info("Recordatorios: %d enviados, %d fallidos", enviados, fallidos)
    return {"enviados": enviados, "fallidos": fallidos, "candidatos": len(lista)}