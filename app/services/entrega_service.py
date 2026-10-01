"""Reporte de entrega real de WhatsApp.

Responde la pregunta que el panel no podía contestar: *"¿quién escribió y nunca
recibió respuesta, y por qué?"*.

Contexto: el POST a la Graph API devuelve HTTP 200 y eso NO prueba entrega. Meta
notifica después ``sent``/``delivered``/``read``/``failed`` en el webhook. Hasta
que esa información se guardó (``EnvioWhatsApp``) el único síntoma era "un número
no arranca" sin ninguna pista.

Importante sobre el alcance: el seguimiento de entregas empieza con el
despliegue de la tabla ``envios_whatsapp``. Los mensajes anteriores a esa fecha no
tienen datos de entrega, así que el reporte solo considera una ventana reciente
y no reporta como "sin respuesta" a quien escribió hace meses.
"""
import datetime
import logging

from sqlalchemy import func, select

from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import EnvioWhatsApp, MensajeWhatsApp
from app.services.whatsapp_service import META_ERRORES_CONOCIDOS

logger = logging.getLogger(__name__)

DIAS_POR_DEFECTO = 7
DIAS_MAXIMO = 90


def _utc_naive() -> datetime.datetime:
    """UTC sin zona: es como la BD guarda ``created_at`` (``func.now()``)."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _sin_digitos(telefono: str | None) -> bool:
    """True si el teléfono no trae dígitos: basura del bug de ``from`` vacío."""
    return not (telefono or "").replace("+", "").strip()


def _iso(valor) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, datetime.datetime):
        return valor.replace(microsecond=0).isoformat()
    return str(valor)


def _lectura_error(codigo, detalle) -> str:
    return META_ERRORES_CONOCIDOS.get(codigo) or (str(detalle)[:140] if detalle else "sin detalle")


def _basura_del_bug(session) -> dict:
    """Filas que dejó el bug de `from` vacío (usuario/tutela sin número real).

    Se accumulating: todas las peticiones malformadas caían en el mismo usuario
    ``""``. No afectan a números reales, pero ensucian la base.
    """
    usuarios = session.execute(
        select(func.count(User.id)).where(func.trim(User.telefono) == "")
    ).scalar() or 0
    tutelas = session.execute(
        select(func.count(Tutela.id))
        .join(User, Tutela.user_id == User.id)
        .where(func.trim(User.telefono) == "")
    ).scalar() or 0
    return {"usuarios_sin_numero": int(usuarios), "tutelas_sin_numero": int(tutelas)}


def reporte_entrega(session, dias: int = DIAS_POR_DEFECTO, limite: int = 100) -> dict:
    """Números que escribieron en la ventana y el estado real de nuestra respuesta."""
    dias = max(1, min(int(dias or DIAS_POR_DEFECTO), DIAS_MAXIMO))
    limite = max(1, min(int(limite or 100), 500))
    desde = _utc_naive() - datetime.timedelta(days=dias)

    entrantes = session.execute(
        select(
            MensajeWhatsApp.from_number,
            func.count(MensajeWhatsApp.id),
            func.max(MensajeWhatsApp.created_at),
        )
        .where(MensajeWhatsApp.created_at >= desde)
        .group_by(MensajeWhatsApp.from_number)
    ).all()

    # El envío más reciente de cada número (id descendente = lo último).
    ultimo_envio: dict[str, EnvioWhatsApp] = {}
    for envio in session.execute(
        select(EnvioWhatsApp).order_by(EnvioWhatsApp.id.desc()).limit(50000)
    ).scalars():
        if envio.from_number:
            ultimo_envio.setdefault(envio.from_number, envio)

    sin_respuesta: list[dict] = []
    fallidos: list[dict] = []
    respondidos = 0

    for from_number, total, ultimo in entrantes:
        if _sin_digitos(from_number):
            continue
        envio = ultimo_envio.get(from_number)
        base = {"telefono": from_number, "mensajes": int(total), "ultimo": _iso(ultimo)}
        if envio is None:
            # Escribió dentro de la ventana y no hay ninguna salida registrada.
            sin_respuesta.append(base)
        elif envio.estado == "fallido":
            fallidos.append({
                **base,
                "estado": envio.estado,
                "codigo": envio.error_code,
                "motivo": _lectura_error(envio.error_code, envio.error_detalle),
            })
        else:
            respondidos += 1

    orden_por_ultimo = lambda item: item["ultimo"] or ""  # noqa: E731
    sin_respuesta.sort(key=orden_por_ultimo, reverse=True)
    fallidos.sort(key=orden_por_ultimo, reverse=True)

    estados = {
        str(estado or "desconocido"): int(total)
        for estado, total in session.execute(
            select(EnvioWhatsApp.estado, func.count(EnvioWhatsApp.id))
            .group_by(EnvioWhatsApp.estado)
        ).all()
    }

    errores = [
        {"codigo": codigo, "veces": int(veces), "motivo": _lectura_error(codigo, None)}
        for codigo, veces in session.execute(
            select(EnvioWhatsApp.error_code, func.count(EnvioWhatsApp.id))
            .where(
                EnvioWhatsApp.estado == "fallido",
                EnvioWhatsApp.error_code.isnot(None),
            )
            .group_by(EnvioWhatsApp.error_code)
            .order_by(func.count(EnvioWhatsApp.id).desc())
            .limit(10)
        ).all()
    ]

    numeros_escribieron = sum(1 for numero, _, _ in entrantes if not _sin_digitos(numero))
    return {
        "generado": _iso(_utc_naive()),
        "ventana_dias": dias,
        "desde": _iso(desde),
        "nota": (
            "El seguimiento de entregas empieza con el despliegue de esta tabla: "
            "los mensajes anteriores no tienen datos de entrega."
        ),
        "resumen": {
            "numeros_que_escribieron": numeros_escribieron,
            "con_respuesta_confirmada": respondidos,
            "con_envio_fallido": len(fallidos),
            "sin_respuesta": len(sin_respuesta),
        },
        "estados_envio": estados,
        "errores_meta": errores,
        "sin_respuesta": sin_respuesta[:limite],
        "fallidos": fallidos[:limite],
        "basura_del_bug": _basura_del_bug(session),
    }