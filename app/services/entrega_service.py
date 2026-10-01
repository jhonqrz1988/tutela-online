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
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import EnvioWhatsApp, MensajeWhatsApp
from app.services.whatsapp_service import META_ERRORES_CONOCIDOS

logger = logging.getLogger(__name__)

# Las fechas se guardan como UTC (func.now()); el panel las muestra en hora de
# Colombia, igual que el resto del admin, para no comparar dos relojes.
BOGOTA_TZ = ZoneInfo("America/Bogota")

DIAS_POR_DEFECTO = 7
DIAS_MAXIMO = 90


def _utc_naive() -> datetime.datetime:
    """UTC sin zona: es como la BD guarda ``created_at`` (``func.now()``)."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _sin_digitos(telefono: str | None) -> bool:
    """True si el teléfono no trae dígitos: basura del bug de ``from`` vacío."""
    return not (telefono or "").replace("+", "").strip()


def _iso(valor) -> str | None:
    """Fecha en hora de Bogotá (la BD guarda UTC)."""
    if valor is None:
        return None
    if not isinstance(valor, datetime.datetime):
        return str(valor)
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=datetime.UTC)
    return valor.astimezone(BOGOTA_TZ).strftime("%Y-%m-%d %H:%M")


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


def _etapa(estado: str | None) -> tuple[str, str]:
    """Traduce el estado de la tutela a "hasta dónde llegó el bot" + tono.

    Responde de un vistazo a la pregunta que más importa en soporte: ¿este
    número arrancó el bot o se quedó en el primer mensaje?
    """
    if not estado:
        return "No arrancó", "mal"
    grupos = (
        (("borrador",), "No arrancó", "mal"),
        (
            (
                "recogiendo_datos",
                "confirmar_datos_personales",
                "corrigiendo_datos_personales",
            ),
            "Recogiendo datos",
            "aviso",
        ),
        (("narracion", "confirmar_audio", "revision_datos"), "Narración", "aviso"),
        (("preguntas_clinicas",), "Datos clínicos", "aviso"),
        (("pruebas_pendiente", "recibiendo_pruebas"), "Pruebas", "aviso"),
        (
            ("datos_listos", "pdf_generado"),
            "Datos completos",
            "ok",
        ),
        (
            (
                "esperando_decision_radicacion",
                "hazlo_tu_mismo",
                "esperando_pago",
                "confirmar_pago",
                "pago_por_confirmar",
                "pago_confirmado",
            ),
            "Pendiente de radicación",
            "aviso",
        ),
        (("radicada", "completado"), "Radicada", "ok"),
        (("fallida", "pendiente_radicacion", "esperando_codigo_email"), "Con incidencias", "mal"),
    )
    for estados, etiqueta, tono in grupos:
        if estado in estados:
            return etiqueta, tono
    return estado, "info"


def reporte_entrega(session, dias: int = DIAS_POR_DEFECTO, limite: int = 100) -> dict:
    """Números que escribieron en la ventana: hasta dónde llegó el bot y si recibió."""
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

    # Último mensaje recibido de cada número: qué escribió la persona al final.
    ultimo_texto: dict[str, str] = {}
    for mensaje in session.execute(
        select(MensajeWhatsApp).order_by(MensajeWhatsApp.id.desc()).limit(50000)
    ).scalars():
        if mensaje.from_number and mensaje.from_number not in ultimo_texto:
            cuerpo = (mensaje.body or "").strip().replace("\n", " ")
            if cuerpo:
                ultimo_texto[mensaje.from_number] = cuerpo[:90]

    # Última tutela de cada número: en qué punto del flujo quedó.
    tutela_por_numero: dict[str, Tutela] = {}
    for tutela in session.execute(
        select(Tutela).join(User, Tutela.user_id == User.id)
        .where(User.telefono.isnot(None))
        .order_by(Tutela.id.desc())
        .limit(50000)
    ).scalars():
        if tutela.user and tutela.user.telefono:
            tutela_por_numero.setdefault(tutela.user.telefono, tutela)

    sin_respuesta: list[dict] = []
    fallidos: list[dict] = []
    conversaciones: list[dict] = []
    respondidos = 0
    sin_arrancar = 0

    for from_number, total, ultimo in entrantes:
        if _sin_digitos(from_number):
            continue
        envio = ultimo_envio.get(from_number)
        tutela = tutela_por_numero.get(from_number)
        etiqueta, tono = _etapa(tutela.estado if tutela else None)
        base = {"telefono": from_number, "mensajes": int(total), "ultimo": _iso(ultimo)}

        if envio is None:
            # Escribió dentro de la ventana y no hay ninguna salida registrada.
            sin_respuesta.append(base)
            entrega = "sin rastro"
        elif envio.estado == "fallido":
            entrega = "fallida"
            fallidos.append({
                **base,
                "estado": envio.estado,
                "codigo": envio.error_code,
                "motivo": _lectura_error(envio.error_code, envio.error_detalle),
            })
        else:
            entrega = envio.estado
            respondidos += 1

        if etiqueta == "No arrancó":
            sin_arrancar += 1
        conversaciones.append({
            **base,
            "bot_estado": tutela.estado if tutela else None,
            "bot_etiqueta": etiqueta,
            "bot_tono": tono,
            "tutela_id": tutela.id if tutela else None,
            "ultimo_texto": ultimo_texto.get(from_number, ""),
            "entrega": entrega,
            "motivo": (
                _lectura_error(envio.error_code, envio.error_detalle)
                if envio is not None and envio.estado == "fallido"
                else ""
            ),
        })

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
            "sin_arrancar": sin_arrancar,
            "arrancaron": numeros_escribieron - sin_arrancar,
        },
        "estados_envio": estados,
        "errores_meta": errores,
        "sin_respuesta": sin_respuesta[:limite],
        "fallidos": fallidos[:limite],
        "conversaciones": sorted(
            conversaciones[:limite],
            key=lambda item: (item["bot_etiqueta"] == "No arrancó", item["ultimo"] or ""),
            reverse=True,
        ),
        "basura_del_bug": _basura_del_bug(session),
    }