import contextlib
import datetime
import functools
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import socket
import unicodedata
from contextvars import ContextVar
from urllib.parse import urlparse

import aiofiles
import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import delete, select, update

from app.config import settings
from app.database import get_session
from app.models.cita_legal import CitaLegal, CitaPendiente
from app.models.radicacion import PasoRadicacion, Radicacion
from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import EnvioWhatsApp, MensajeWhatsApp
from app.services.documento_service import generar_pdf
from app.services.mercadopago_service import texto_precio
from app.services.ia_service import (
    aplicar_extraccion,
    analizar_imagen,
    campos_faltantes,
    extraer_citas,
    extraer_datos_caso,
    generar_preview,
    generar_tutela,
    transcribir_audio,
)
from app.services.verificacion_service import (
    fundamentacion_juridica_extra,
    guardar_pendientes,
    insertar_fundamentacion,
    limpiar_texto_para_pdf,
    verificar_citas,
)
from app.services.whatsapp_service import (
    META_ERRORES_CONOCIDOS,
    enviar_botones,
    enviar_documento,
    enviar_texto,
    reiniciar_wamids_envio,
    wamids_envio,
)
from app.utils.file_utils import path_prueba
from app.utils.validacion import procesar_campo_personal, validar_campo_personal

router = APIRouter()

logger = logging.getLogger(__name__)


def _auth_webhook_legacy(request: Request) -> bool:
    """Valida el token compartido en los webhooks legacy.

    Si ``webhook_auth_token`` no está configurado, en modo simular (provider
    ``simular``) se permite con warning; si el provider es un legacy
    (twilio/zapi/infobip) se rechaza (fail-closed) para no dejar un webhook
    sin autenticar en producción.
    """
    if settings.whatsapp_provider == "simular":
        return True
    if not settings.webhook_auth_token:
        logger.error("_auth_webhook_legacy: proveedor legacy sin WEBHOOK_AUTH_TOKEN — rechazando")
        return False
    auth = request.headers.get("authorization", "")
    token = request.query_params.get("token", "")
    esperado = settings.webhook_auth_token
    if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], esperado):
        return True
    return hmac.compare_digest(token, esperado)


@router.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request, session=Depends(get_session)):
    if not _auth_webhook_legacy(request):
        return JSONResponse({"ok": False, "error": "No autorizado"}, status_code=401)
    try:
        content_type = request.headers.get("content-type", "")
        if "json" in content_type or "application/json" in content_type:
            data = await request.json()
            results = data.get("results", [data])
            respuestas = []
            for msg in results:
                telefono = msg.get("from", "").replace("whatsapp:", "")
                msg_type = msg.get("type", "")
                body_text = ""
                num_media = 0
                media_url = ""
                es_audio = False
                if msg_type == "text":
                    body_text = msg.get("text", {}).get("body", "").strip()
                elif msg_type == "interactive":
                    interactive = msg.get("interactive", {})
                    ireply = interactive.get("button_reply", {}) or interactive.get("list_reply", {})
                    body_text = (ireply.get("id", "") or ireply.get("title", "")).strip().lower()
                elif msg_type in ("image", "document"):
                    num_media = 1
                    media_data = msg.get(msg_type, {})
                    media_url = media_data.get("link", "") or media_data.get("id", "")
                elif msg_type == "audio":
                    es_audio = True
                    media_url = msg.get("audio", {}).get("id", "")

                respuesta = await procesar_mensaje(session, telefono, body_text, num_media, media_url, es_audio)
                if respuesta.get("respuestas"):
                    respuestas.extend(respuesta["respuestas"])
            return {"ok": True, "respuestas": respuestas} if respuestas else {"ok": True}
        else:
            form = await request.form()
            telefono = form.get("From", "").replace("whatsapp:", "")
            body_text = (form.get("Body", "") or "").strip()
            num_media = int(form.get("NumMedia", "0"))
            media_url = form.get("MediaUrl0")
            es_audio = "audio" in str(form.get("MediaContentType0", ""))
            return await procesar_mensaje(session, telefono, body_text, num_media, media_url, es_audio)
    except Exception as e:
        logger.error(f"Error en webhook_whatsapp: {e}")
        return {"ok": False, "error": str(e)}


def _verify_meta_signature(payload: bytes, signature_header: str) -> bool:
    """Verifica la firma HMAC-SHA256 del webhook de Meta.

    Si el proveedor es meta y META_APP_SECRET no está configurado, el
    comportamiento depende de strict_webhook_firma (app/config.py:23):
    - False (default, modo pruebas): acepta con warning.
    - True: rechaza (útil en producción para no dejar webhooks sin validar).
    En cuanto se configure el secret, la firma se valida obligatoriamente.
    """
    if settings.whatsapp_provider != "meta":
        return True
    if not settings.meta_app_secret:
        if settings.strict_webhook_firma:
            logger.error("_verify_meta_signature: STRICT — META_APP_SECRET vacío, rechazando webhook")
            return False
        logger.warning("_verify_meta_signature: proveedor meta sin META_APP_SECRET — aceptando webhook (modo pruebas, strict_webhook_firma=False)")
        return True
    expected = hmac.new(
        settings.meta_app_secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    provided = signature_header.replace("sha256=", "")
    return hmac.compare_digest(expected, provided)


@router.get("/webhook/meta")
async def verificar_webhook_meta(request: Request):
    modo = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if modo == "subscribe" and token == settings.meta_verify_token:
        return PlainTextResponse(challenge)
    return {"error": "Verification failed"}


def _numero_util(telefono: str | None) -> bool:
    """True si el teléfono trae dígitos reales (no solo prefijo o símbolos).

    Evita que un payload con ``from`` vacío/ausente termine enviando a Meta un
    ``to: ""`` (HTTP 400 "The parameter to is required") y, peor, crees un
    usuario y una tutela con el número en blanco.
    """
    return bool((telefono or "").replace("whatsapp:", "").replace("+", "").strip())


def _texto_corto(valor, limite: int) -> str | None:
    """Normaliza un campo de texto del payload a string acotado (o None)."""
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto[:limite] if texto else None


# Campos del bloque `referral` de Meta que se conservan: permiten reconciliar
# la conversación con el anuncio que la originó. El resto se descarta para no
# guardar payloads arbitrarios del remitente.
_CAMPOS_REFERRAL = {
    "ad_id": 50,
    "source_id": 50,
    "headline": 120,
    "body": 200,
    "source_type": 30,
    "source_url": 200,
    "media_type": 30,
}


def _aislar_origen(value: dict) -> list[dict]:
    """Extrae el origen de cada mensaje de un ``value`` del webhook de Meta.

    Devuelve una lista de dicts ``{telefono, phone_number_id, ad_id, ...}``, uno
    por mensaje. Los anuncios click-to-WhatsApp incluyen ``referral`` con el
    ``ad_id`` que originó el chat, y ``metadata.phone_number_id`` indica a qué
    número del negocio llegó el mensaje (permite detectar conversaciones que
    entran por un número distinto al configurado).

    Nunca lanza: un payload inesperado devuelve lista vacía.
    """
    if not isinstance(value, dict):
        return []
    metadata = value.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    phone_number_id = _texto_corto(metadata.get("phone_number_id"), 40)

    origenes: list[dict] = []
    for msg in value.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        telefono = _texto_corto(msg.get("from"), 20)
        if not telefono:
            continue
        origen = {"telefono": telefono, "phone_number_id": phone_number_id}
        referral = msg.get("referral")
        if isinstance(referral, dict):
            for campo, limite in _CAMPOS_REFERRAL.items():
                origen[campo] = _texto_corto(referral.get(campo), limite)
        origenes.append(origen)
    return origenes


def _parsear_mensaje(msg: dict) -> dict:
    """Normaliza un mensaje entrante de Meta a los campos que usa el bot.

    Cubre los tipos que antes quedaban con ``body_text`` vacío (y dejaban el
    flujo mudo): ``interactive`` con ``button`` (clic en CTA de un anuncio, que
    NO viene en ``button_reply``), ``reaction``, ``sticker``, ``location``,
    ``contacts``, ``video`` y ``unsupported``.

    Devuelve ``{body_text, num_media, media_url, es_audio}``. Nunca lanza.
    """
    msg_type = msg.get("type", "")
    body_text = ""
    num_media = 0
    media_url = ""
    es_audio = False

    if msg_type == "text":
        body_text = (msg.get("text", {}) or {}).get("body", "").strip()
    elif msg_type == "interactive":
        interactive = msg.get("interactive", {}) or {}
        # button_reply/list_reply: respuesta a un botón que enviamos nosotros.
        ireply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        texto = (ireply.get("id", "") or ireply.get("title", "")).strip()
        if not texto:
            # CTA de anuncio o formulario: el texto llega en button.text o nfm_reply.
            texto = (interactive.get("button", {}) or {}).get("text", "").strip()
        if not texto:
            texto = (interactive.get("nfm_reply", {}) or {}).get("response_json", "").strip()
        # Sin lower(): `procesar_mensaje` normaliza a minúsculas para comparar y
        # así `raw_body` conserva el texto real que escribió el usuario.
        body_text = texto
    elif msg_type in ("image", "document"):
        num_media = 1
        media_data = msg.get(msg_type, {}) or {}
        media_url = media_data.get("link", "") or media_data.get("id", "")
    elif msg_type == "audio":
        es_audio = True
        media_url = (msg.get("audio", {}) or {}).get("id", "")
    elif msg_type == "video":
        num_media = 1
        media_data = msg.get("video", {}) or {}
        media_url = media_data.get("link", "") or media_data.get("id", "")
    elif msg_type == "reaction":
        # El emoji sirve como texto para no dejar el flujo sin contenido.
        body_text = (msg.get("reaction", {}) or {}).get("emoji", "").strip()
    elif msg_type == "sticker":
        body_text = "[sticker]"
    elif msg_type == "location":
        body_text = "[ubicacion]"
    elif msg_type == "contacts":
        body_text = "[contacto]"
    elif msg_type == "button":
        body_text = (msg.get("button", {}) or {}).get("text", "").strip()
    else:
        # unsupported o cualquier tipo futuro: marcador que permite reanudar.
        body_text = "[no soportado]"

    return {
        "body_text": body_text,
        "num_media": num_media,
        "media_url": media_url,
        "es_audio": es_audio,
    }


def _serializar_origen(origen: dict | None) -> str | None:
    """Serializa el origen a JSON para ``MensajeWhatsApp.metadata_json``."""
    if not origen:
        return None
    try:
        return json.dumps(origen, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


@router.post("/webhook/meta")
async def webhook_meta(request: Request, session=Depends(get_session)):
    raw_body = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_meta_signature(raw_body, signature):
        # Un webhook rechazado era 100% silencioso: el bot dejaba de responder a
        # TODOS los números sin ninguna pista. Ahora queda en el log con el
        # emisor (si el payload se puede leer) para poder diagnosticarlo.
        logger.error(
            "Webhook Meta RECHAZADO por firma inválida (header %s, %d bytes de payload)",
            "presente" if signature else "ausente",
            len(raw_body),
        )
        return {"ok": False, "error": "Invalid signature"}

    import json as _json
    try:
        data = _json.loads(raw_body)
    except _json.JSONDecodeError:
        return {"ok": True}

    entry = data.get("entry", [])
    respuestas = []
    for e in entry:
        changes = e.get("changes", [])
        for c in changes:
            value = c.get("value", {})
            # Entrega real de los envíos anteriores (delivered/read/failed).
            # Va antes de ``messages``: un payload puede traer solo estados.
            _procesar_statuses(session, value)
            messages = value.get("messages", [])
            # Origen por número de usuario (referral/ad_id + número receptor).
            origenes = {o["telefono"]: o for o in _aislar_origen(value)}
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                msg_type = msg.get("type", "")
                # Un `from` vacío/ausente no permite responder ni identificar al
                # usuario: antes se procesaba igual y quemaba un envío (400 de
                # Meta) además de crear un usuario/tutela sin número.
                if not _numero_util(msg.get("from")):
                    logger.error(
                        "Webhook Meta: mensaje ignorado, 'from' vacío o ausente "
                        "(tipo=%s claves=%s)",
                        msg_type,
                        sorted(msg.keys())[:12],
                    )
                    continue
                telefono = msg["from"].replace("whatsapp:", "")
                msg_type = msg.get("type", "")
                datos_msg = _parsear_mensaje(msg)
                body_text = datos_msg["body_text"]
                num_media = datos_msg["num_media"]
                media_url = datos_msg["media_url"]
                es_audio = datos_msg["es_audio"]

                try:
                    logger.info(f"Webhook Meta: tipo={msg_type} de={telefono}")
                    respuesta = await procesar_mensaje(
                        session, telefono, body_text, num_media, media_url, es_audio,
                        origen=origenes.get(telefono),
                    )
                    if isinstance(respuesta, dict) and respuesta.get("respuestas"):
                        respuestas.extend(respuesta["respuestas"])
                except Exception as e:
                    logger.error(f"Error procesando mensaje {msg_type} de {telefono}: {e}", exc_info=True)
    return {"ok": True, "respuestas": respuestas} if respuestas else {"ok": True}


@router.post("/webhook/zapi")
async def webhook_zapi(request: Request, session=Depends(get_session)):
    if not _auth_webhook_legacy(request):
        return JSONResponse({"ok": False, "error": "No autorizado"}, status_code=401)
    data = await request.json()
    telefono = data.get("from", "").replace("55", "", 1) if data.get("from", "").startswith("55") else data.get("from", "")
    body = (data.get("text", data.get("message", {}).get("text", "")) or "").strip()
    num_media = 1 if data.get("mediaUrl") or data.get("message", {}).get("mediaUrl") else 0
    media_url = data.get("mediaUrl", data.get("message", {}).get("mediaUrl", ""))
    es_audio = bool(data.get("isAudio", data.get("message", {}).get("isAudio", False)))
    return await procesar_mensaje(session, telefono, body, num_media, media_url, es_audio)


# ═══════════════════════════════════════════════════════════════════
# FLUJO PRINCIPAL
# ═══════════════════════════════════════════════════════════════════

def _borrar_radicaciones(session, tutela_ids):
    rads = session.execute(select(Radicacion).where(Radicacion.tutela_id.in_(tutela_ids))).scalars().all()
    if not rads:
        return
    rad_ids = [r.id for r in rads]
    session.execute(delete(PasoRadicacion).where(PasoRadicacion.radicacion_id.in_(rad_ids)))
    for r in rads:
        session.delete(r)


def _liberar_tutelas(session, tutela_ids) -> None:
    """Deja borrables las tutelas: rompe todas las referencias que apuntan a ellas.

    ``tutelas.id`` está referenciado por cinco columnas y NINGUNA tiene
    ``ondelete``: cita_pendientes, radicaciones, pasos_radicacion (vía
    radicaciones), mensajes_whatsapp y envios_whatsapp. Las tres primeras se
    borran; las dos últimas se **desanclan con tutela_id = NULL**.

    Sin este paso, en PostgreSQL el ``DELETE`` de la tutela lanza
    ForeignKeyViolation, la transacción se revierte entera y el reinicio no
    ocurre: el sintoma es "salir no hace nada" y el usuario queda atrapado
    (bug reportado en producción).

    Desanclar en vez de borrar conserva el historial: los mensajes (con su
    ``envio_estado``) y los envíos con su wamid son lo único que explica por qué
    un número no avanzó, y el reporte de entrega depende de ellos.
    """
    if not tutela_ids:
        return
    session.execute(
        update(MensajeWhatsApp)
        .where(MensajeWhatsApp.tutela_id.in_(tutela_ids))
        .values(tutela_id=None)
    )
    session.execute(
        update(EnvioWhatsApp)
        .where(EnvioWhatsApp.tutela_id.in_(tutela_ids))
        .values(tutela_id=None)
    )
    session.execute(
        delete(CitaPendiente).where(CitaPendiente.tutela_id.in_(tutela_ids))
    )
    _borrar_radicaciones(session, tutela_ids)


# ═══════════════════════════════════════════════════════════════════
# ENTREGA DE LA RESPUESTA (diagnóstico "escribió y no le respondió")
# ═══════════════════════════════════════════════════════════════════
# Antes `_r`/`_b` ignoraban el resultado de `enviar_texto`/`enviar_botones`:
# con el número EXPIRED Meta rechazaba cada respuesta y la BD registraba una
# conversación aparentemente normal, imposible de distinguir de una exitosa.
# Ahora cada mensaje entrante guarda `envio_estado` y el panel cuenta los
# usuarios que escribieron pero no recibieron respuesta.

ENVIO_ENTREGADO = "entregado"
ENVIO_FALLIDO = "fallido"
ENVIO_SIN_RESPUESTA = "sin_respuesta"

_ENVIOS: ContextVar[dict | None] = ContextVar("tutela_envios", default=None)


def _acumular_envio(ok: bool) -> None:
    """Registra el resultado de un envío dentro del mensaje entrante en curso."""
    registro = _ENVIOS.get()
    if registro is None:
        return
    registro["n"] += 1
    if not ok:
        registro["ok"] = False


def _estado_envio(registro: dict) -> str:
    if registro.get("n", 0) == 0:
        return ENVIO_SIN_RESPUESTA
    return ENVIO_ENTREGADO if registro.get("ok", True) else ENVIO_FALLIDO


def _registrar_mensaje_en_curso(msg_orm) -> None:
    registro = _ENVIOS.get()
    if registro is not None:
        registro["msg"] = msg_orm


def _marcar_estado_envio(session, registro: dict) -> None:
    """Guarda ``envio_estado`` en el mensaje entrante (best-effort)."""
    msg_orm = registro.get("msg")
    if msg_orm is None or getattr(msg_orm, "id", None) is None:
        return
    try:
        session.query(MensajeWhatsApp).filter(MensajeWhatsApp.id == msg_orm.id).update(
            {MensajeWhatsApp.envio_estado: _estado_envio(registro)},
            synchronize_session=False,
        )
        session.commit()
    except Exception:  # noqa: BLE001 - el diagnóstico nunca debe romper el flujo
        logger.warning("No se pudo registrar el estado de envío", exc_info=True)
        with contextlib.suppress(Exception):
            session.rollback()


def _registrar_envios_wamids(session, telefono: str) -> None:
    """Deja una fila por cada mensaje saliente aceptado por Meta (best-effort).

    Solo así se pueden casar los ``statuses`` posteriores: sin guardar el
    ``wamid`` no hay forma de saber si la respuesta llegó al teléfono.
    """
    pendientes = wamids_envio()
    if not pendientes:
        return
    try:
        tutela_id = None
        if telefono:
            tutela_id = session.execute(
                select(Tutela.id)
                .join(User, Tutela.user_id == User.id)
                .where(User.telefono == telefono)
                .order_by(Tutela.id.desc())
                .limit(1)
            ).scalars().first()
        for item in pendientes:
            session.add(
                EnvioWhatsApp(
                    wamid=item["wamid"][:255],
                    from_number=(telefono or "")[:20],
                    tutela_id=tutela_id,
                )
            )
        session.commit()
    except Exception:  # noqa: BLE001 - el diagnóstico nunca debe romper el flujo
        logger.warning("No se pudieron registrar los envíos (wamids)", exc_info=True)
        with contextlib.suppress(Exception):
            session.rollback()


def _procesar_statuses(session, value: dict) -> None:
    """Aplica los estados de entrega que notifica Meta tras un envío aceptado.

    El POST a la Graph API devuelve 200 y eso no prueba entrega. Meta notifica
    ``sent``/``delivered``/``read``/``failed`` aquí: es el único dato que dice si
    el usuario recibió de verdad la respuesta del bot.
    """
    for st in value.get("statuses") or []:
        if not isinstance(st, dict):
            continue
        wamid = str(st.get("id") or "")[:255]
        estado = str(st.get("status") or "")[:20]
        if not wamid or not estado:
            continue

        errores = st.get("errors") or []
        codigo = None
        detalle = None
        if isinstance(errores, list) and errores and isinstance(errores[0], dict):
            codigo = errores[0].get("code")
            detalle = (
                errores[0].get("error_data", {}).get("details")
                if isinstance(errores[0].get("error_data"), dict)
                else None
            ) or errores[0].get("message")
            try:
                codigo = int(codigo)
            except (TypeError, ValueError):
                codigo = None

        nuevo = "leido" if estado == "read" else ("fallido" if estado == "failed" else "entregado")
        try:
            fila = session.query(EnvioWhatsApp).filter(EnvioWhatsApp.wamid == wamid).first()
            if fila is None:
                logger.info("Status de un envío no registrado: %s %s", wamid[:24], estado)
                continue
            # Nunca se retrocede el estado (Meta puede reenviar 'sent' tarde).
            orden = {"aceptado": 0, "entregado": 1, "leido": 2, "fallido": 3}
            if orden.get(nuevo, 0) >= orden.get(fila.estado or "aceptado", 0):
                fila.estado = nuevo
            if nuevo == "fallido":
                fila.error_code = codigo
                fila.error_detalle = (str(detalle)[:500] if detalle else None)
            session.commit()

            if nuevo == "fallido":
                logger.error(
                    "Meta NO entregó el mensaje to=%s code=%s motivo=%s (%s)",
                    (fila.from_number or "")[:6] + "***",
                    codigo,
                    META_ERRORES_CONOCIDOS.get(codigo, (detalle or "sin detalle")[:120]),
                    estado,
                )
        except Exception:  # noqa: BLE001 - nunca romper el webhook por un status
            logger.warning("No se pudo aplicar el status de Meta", exc_info=True)
            with contextlib.suppress(Exception):
                session.rollback()


def _registrar_envio(fn):
    """Envoltorio de ``procesar_mensaje``: mide si la respuesta llegó a enviarse.

    Se usa decorador para no reindentar los ~50 puntos de retorno del flujo: el
    cuerpo real queda intacto y solo se envuelve la llamada.
    """

    @functools.wraps(fn)
    async def wrapper(session, telefono, *args, **kwargs):
        token = _ENVIOS.set({"ok": True, "n": 0, "msg": None})
        reiniciar_wamids_envio()
        try:
            return await fn(session, telefono, *args, **kwargs)
        finally:
            registro = _ENVIOS.get()
            _ENVIOS.reset(token)
            if registro is not None:
                _marcar_estado_envio(session, registro)
            _registrar_envios_wamids(session, telefono)
            reiniciar_wamids_envio()

    return wrapper


# Sinónimos aceptados para abandonar y reiniciar el proceso.
_COMANDOS_SALIR = frozenset({
    "salir", "reiniciar", "reinicio", "empezar de nuevo",
    "nuevo proceso", "nueva tutela", "cancelar tutela", "cancelar",
    "dejar la tutela", "abandonar", "salir del proceso",
})


def _normalizar_texto_entrada(texto: str) -> str:
    """Minúsculas, sin acentos ni puntuación, para comparar comandos.

    Los usuarios escriben "Salir.", "SALIR", "salir " o "sálir"; sin esto el
    comando de escape se guarda como nombre/apellido y el usuario queda
    atrapado en el flujo sin salida.
    """
    texto = (texto or "").strip().lower()
    # Descomponer para quitar diacríticos: "sálir" -> "salir".
    texto = "".join(
        ch for ch in unicodedata.normalize("NFD", texto) if unicodedata.category(ch) != "Mn"
    )
    # Quitar puntuación y signos que WhatsApp añade al final.
    return "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in texto).strip()


def _es_comando_salir(body: str) -> bool:
    """True si el texto es un comando para abandonar y reiniciar el flujo.

    Solo si el mensaje es EXACTAMENTE el comando: "salir" dentro de una frase
    ("quiero salir de aquí") no debe borrar los datos de la persona.
    """
    return _normalizar_texto_entrada(body) in _COMANDOS_SALIR


def _reiniciar_flujo(session, user, telefono: str, respuestas: list[str]) -> None:
    """Borra lo del usuario y lo devuelve al inicio, con el aviso de privacidad.

    No se borra el histórico de mensajes (``MensajeWhatsApp``): solo se eliminan
    tutelas, citas pendientes y radicaciones. Así el usuario conserva su
    conversación y el panel no pierde el registro de que estuvo aquí.
    """
    tutela_ids = session.execute(
        select(Tutela.id).where(Tutela.user_id == user.id)
    ).scalars().all()
    # Desancla mensajes/envíos y borra citas/radicaciones ANTES del DELETE de la
    # tutela; si falta algo, PostgreSQL revierte todo y "salir" no hace nada.
    _liberar_tutelas(session, tutela_ids)
    for t in session.execute(select(Tutela).where(Tutela.user_id == user.id)).scalars():
        session.delete(t)

    user.estado = "nuevo"
    user.consentimiento = False
    user.consentimiento_version = None
    user.consentimiento_timestamp = None
    session.commit()

    _r(respuestas, telefono, "🔄 *Flujo reiniciado.*\n\nSe borraron los datos anteriores y empiezas de cero.")
    _r(respuestas, telefono, BIENVENIDA)
    _b(respuestas, telefono, aviso_privacidad(), [("acepto", "✅ Sí, acepto"), ("no", "❌ No acepto"), ("salir", "🚪 Salir")])


@_registrar_envio
async def procesar_mensaje(
    session, telefono: str, body: str, num_media: int, media_url: str, es_audio: bool,
    origen: dict | None = None,
) -> dict:
    respuestas: list[str] = []
    # Última barrera: sin dígitos no hay a quién responder. Cortar aquí evita
    # el 400 de Meta ("The parameter to is required") y que se guarden filas
    # de usuario/tutela con el teléfono vacío.
    if not _numero_util(telefono):
        logger.error("procesar_mensaje ignorado: teléfono vacío (body=%r)", (body or "")[:60])
        return {"ok": False, "error": "telefono vacio", "respuestas": respuestas}
    body = (body or "").strip()
    raw_body = body
    body = body.lower()

    msg_orm = MensajeWhatsApp(
        from_number=telefono,
        body=body,
        tipo_mensaje="audio" if es_audio else "texto",
        media_url=media_url,
        # Origen de la conversación (ad_id del anuncio + número receptor de
        # Meta): permite reconciliar pauta vs mensajes reales en el panel.
        metadata_json=_serializar_origen(origen),
    )
    session.add(msg_orm)
    session.commit()
    # Asocia este mensaje al registro de envíos en curso para poder marcar
    # al final si la respuesta se entregó o Meta la rechazó.
    _registrar_mensaje_en_curso(msg_orm)

    user = session.execute(select(User).where(User.telefono == telefono)).scalar_one_or_none()

    # ─── NUEVO USUARIO ──────────────────────────────────────────────
    if not user:
        user = User(telefono=telefono, estado="nuevo", consentimiento=False)
        session.add(user)
        session.commit()
        _r(respuestas, telefono, BIENVENIDA)
        _b(respuestas, telefono, aviso_privacidad(), [("acepto", "✅ Sí, acepto"), ("no", "❌ No acepto"), ("salir", "🚪 Salir")])
        return {"ok": True, "respuestas": respuestas}

    # ─── SALIR / REINICIAR — borra datos y empieza de cero como nuevo usuario ──
    # Va ANTES de cualquier lógica de estado a propósito: el comando de escape
    # debe funcionar aunque el bot esté pidiendo un dato. Sin esto, escribir
    # "salir" se guardaba como nombre/apellido y el usuario quedaba atrapado
    # sin forma de corregir.
    if _es_comando_salir(body):
        _reiniciar_flujo(session, user, telefono, respuestas)
        return {"ok": True, "respuestas": respuestas}

    # ─── ELIMINAR DATOS ──────────────────────────────────────────────
    if body in ("eliminar", "eliminar mis datos", "borrar", "borrar mis datos"):
        tutela_ids = session.execute(select(Tutela.id).where(Tutela.user_id == user.id)).scalars().all()
        # Desancla primero: sin esto el DELETE de la tutela revierte todo
        # (mismo bug que "salir") y el usuario nunca lograría borrar sus datos.
        _liberar_tutelas(session, tutela_ids)
        # Borrado efectivo (derecho de supresión, Ley 1581): aquí sí se va el
        # historial, incluidos los envíos, porque el usuario lo pidió.
        session.execute(delete(MensajeWhatsApp).where(MensajeWhatsApp.from_number == telefono))
        session.execute(delete(EnvioWhatsApp).where(EnvioWhatsApp.from_number == telefono))
        for t in session.execute(select(Tutela).where(Tutela.user_id == user.id)).scalars():
            session.delete(t)
        session.delete(user)
        session.commit()
        _r(respuestas, telefono, "🗑️ *Tus datos han sido eliminados.*\n\nSi necesitas ayuda en el futuro, escribe *Hola* y empezamos de nuevo.")
        return {"ok": True, "respuestas": respuestas}

    # ─── OPT-OUT (pausar mensajes) ──────────────────────────────────
    if body in ("detener", "pausar", "no me molesten", "parar", "stop", "cancelar suscripción", "no quiero más mensajes"):
        # Se marca en su propia columna: `estado` sigue siendo el del flujo, así
        # que sin esto no había forma de saber a quién NO mandar recordatorios.
        user.no_mensajes_proactivos = True
        session.commit()
        _r(respuestas, telefono, "⏸️ *Mensajes pausados.*\n\nSi necesitas ayuda en el futuro, escribe *Hola* para reanudar.")
        return {"ok": True, "respuestas": respuestas}

    # ─── CONSENTIMIENTO ──────────────────────────────────────────────
    if user.estado == "nuevo":
        if body in ("acepto", "sí", "si", "ok", "si acepto", "sí acepto"):
            now = datetime.datetime.now(datetime.UTC)
            user.consentimiento = True
            user.consentimiento_version = CONSENTIMIENTO_VERSION
            user.consentimiento_timestamp = now
            user.estado = "activo"
            session.commit()
            tutela = Tutela(user_id=user.id, tipo="salud", estado="recogiendo_datos")
            datos = {"tipo": "salud", "_step": 0}
            tutela.datos_json = json.dumps(datos)
            session.add(tutela)
            session.commit()
            campo, msg = DATOS_PERSONALES_STEPS[0]
            _r(respuestas, telefono, "✅ *Consentimiento registrado.*\n\nAhora necesito tus datos personales.")
            _r(respuestas, telefono, msg)
            # Aviso de salida: se dice aquí, al empezar a pedir datos, que en
            # cualquier momento se puede empezar de cero. Sin esto, quien se
            # equivoca en un campo no sabe cómo escapar del flujo.
            _r(respuestas, telefono, AVISO_SALIR)
            return {"ok": True, "respuestas": respuestas}
        elif body in ("no", "no acepto", "cancelar"):
            user.estado = "rechazado"
            session.commit()
            _r(respuestas, telefono, "Entendido. Sin tu autorización no podemos procesar tus datos. Si cambias de opinión, escribe *Hola* para empezar de nuevo. ¡Feliz día!")
            return {"ok": True, "respuestas": respuestas}
        _b(respuestas, telefono, aviso_privacidad(), [("acepto", "✅ Sí, acepto"), ("no", "❌ No acepto"), ("salir", "🚪 Salir")])
        return {"ok": True, "respuestas": respuestas}

    if user.estado == "rechazado":
        _r(respuestas, telefono, "Entendido. Sin tu autorización no podemos procesar tus datos. Si cambias de opinión, escribe *Hola* para empezar de nuevo. ¡Feliz día!")
        return {"ok": True, "respuestas": respuestas}

    # ─── HOLA / CONTINUAR (usuario existente) ────────────────────────────
    # "continuar mi tutela" es el botón del recordatorio: si lo pulsó, retoma
    # exactamente donde se quedó (misma rama que "hola").
    if body in ("hola", "menú", "menu", "inicio", "empezar", "continuar", "continuar mi tutela"):
        user.estado = "activo"
        # Volvió a escribir: si antes pidió "detener", se reactiva.
        user.no_mensajes_proactivos = False
        session.commit()
        tutela = session.execute(
            select(Tutela).where(
                Tutela.user_id == user.id,
Tutela.estado.in_(["recogiendo_datos", "narracion", "confirmar_audio", "revision_datos",
                               "preguntas_clinicas", "confirmar_datos_personales", "corrigiendo_datos_personales",
                               "pruebas_pendiente", "esperando_codigo_email",
                               "recibiendo_pruebas", "datos_listos", "pdf_generado",
                               "esperando_decision_radicacion",
                               "hazlo_tu_mismo", "confirmar_pago", "esperando_pago", "pago_por_confirmar",
                               "pago_confirmado", "pendiente_radicacion", "fallida", "completado"]),
            ).order_by(Tutela.created_at.desc()).limit(1)
        ).scalar_one_or_none()

        if tutela and tutela.estado != "completado":
            datos = json.loads(tutela.datos_json) if tutela.datos_json else {}

            if tutela.estado == "recogiendo_datos":
                step = datos.get("_step", 0)
                if step < len(DATOS_PERSONALES_STEPS):
                    _, msg = DATOS_PERSONALES_STEPS[step]
                    _r(respuestas, telefono, msg)
                else:
                    _mostrar_confirmacion_datos(telefono, respuestas, datos)
            elif tutela.estado == "narracion":
                _r(respuestas, telefono, NARRACION)
            elif tutela.estado == "revision_datos":
                await _mostrar_revision_datos(session, tutela, datos, telefono, respuestas)
            elif tutela.estado == "pruebas_pendiente":
                _b(respuestas, telefono, PRUEBAS_PREGUNTA, [("adjuntar", "📎 Adjuntar pruebas"), ("saltar", "⏭️ Sin soportes")])
            elif tutela.estado == "datos_listos":
                _b(respuestas, telefono, JURAMENTO_TEXTO, [("1", "✅ Sí, juro"), ("2", "❌ No")])
            else:
                _r(respuestas, telefono, "🤖 *TutelApp* — Continúa donde lo dejaste.")
            return {"ok": True, "respuestas": respuestas}

        tutela = Tutela(user_id=user.id, tipo="salud", estado="recogiendo_datos")
        datos = {"tipo": "salud", "_step": 0}
        tutela.datos_json = json.dumps(datos)
        session.add(tutela)
        session.commit()
        campo, msg = DATOS_PERSONALES_STEPS[0]
        _r(respuestas, telefono, "✍️ *Nueva tutela.*\n\nPrimero tus datos personales.")
        _r(respuestas, telefono, msg)
        return {"ok": True, "respuestas": respuestas}

    # ─── TUTELA ACTIVA ───────────────────────────────────────────────
    tutela = session.execute(
        select(Tutela).where(
            Tutela.user_id == user.id,
Tutela.estado.in_(["recogiendo_datos", "narracion", "confirmar_audio", "revision_datos",
                               "preguntas_clinicas", "confirmar_datos_personales", "corrigiendo_datos_personales",
                               "pruebas_pendiente", "esperando_codigo_email",
                               "recibiendo_pruebas", "datos_listos", "pdf_generado",
                               "esperando_decision_radicacion",
                               "hazlo_tu_mismo", "confirmar_pago", "esperando_pago", "pago_por_confirmar",
                               "pago_confirmado", "pendiente_radicacion", "fallida", "completado"]),
        ).order_by(Tutela.created_at.desc()).limit(1)
    ).scalar_one_or_none()

    if not tutela:
        tutela = Tutela(user_id=user.id, tipo="salud", estado="recogiendo_datos")
        datos = {"tipo": "salud", "_step": 0}
        tutela.datos_json = json.dumps(datos)
        session.add(tutela)
        session.commit()
        campo, msg = DATOS_PERSONALES_STEPS[0]
        _r(respuestas, telefono, "✍️ Empecemos con tus datos personales.")
        _r(respuestas, telefono, msg)
        return {"ok": True, "respuestas": respuestas}

    if not msg_orm.tutela_id:
        msg_orm.tutela_id = tutela.id
        session.commit()

    datos = json.loads(tutela.datos_json) if tutela.datos_json else {}

    # ─── CÓDIGO DE VERIFICACIÓN DE EMAIL ─────────────────────────────
    # El portal pide verificación solo cuando el correo no está registrado.
    # El código debe ir SIEMPRE a la tutela cuya Radicación espera el código,
    # aunque exista una tutela más nueva (huérfana de un flujo previo).
    codigo_limpio = body.strip().replace(" ", "")
    es_codigo = codigo_limpio.isdigit() and 4 <= len(codigo_limpio) <= 6
    tutela_con_codigo = None
    if es_codigo:
        tutela_con_codigo = session.execute(
            select(Tutela).where(
                Tutela.user_id == user.id,
                Tutela.id.in_(
                    select(Radicacion.tutela_id).where(
                        Radicacion.estado.in_(["esperando_codigo_email", "fallida"])
                    )
                ),
            ).order_by(Tutela.created_at.desc()).limit(1)
        ).scalar_one_or_none()
    if tutela_con_codigo is not None or tutela.estado == "esperando_codigo_email":
        if tutela_con_codigo is not None and tutela_con_codigo.id != tutela.id:
            tutela = tutela_con_codigo
            datos = json.loads(tutela.datos_json) if tutela.datos_json else {}
            msg_orm.tutela_id = tutela.id
            session.commit()
        if not es_codigo:
            _r(respuestas, telefono, "🔑 El código debe tener 4 a 6 dígitos. Revísalo en tu correo y envíamelo de nuevo.")
            return {"ok": True, "respuestas": respuestas}
        from app.services.radicacion_service import continuar_radicacion_con_codigo
        _r(respuestas, telefono, "⏳ *Código recibido.* Continuando con la radicación...")
        resultado = await continuar_radicacion_con_codigo(tutela.id, codigo_limpio)
        if resultado.get("ok"):
            _r(respuestas, telefono, "✅ *Código verificado.* Radicando tu tutela...")
        else:
            _r(respuestas, telefono, f"❌ *Error:* {resultado.get('error', 'No se pudo completar')}")
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   RECOGIENDO DATOS PERSONALES — paso a paso
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "recogiendo_datos":
        step = datos.get("_step", 0)

        # Si el usuario envía media en este estado, ignorar y pedir el campo
        if num_media > 0:
            _, msg = DATOS_PERSONALES_STEPS[min(step, len(DATOS_PERSONALES_STEPS) - 1)]
            _r(respuestas, telefono, f"Por favor responde con texto: {msg}")
            return {"ok": True, "respuestas": respuestas}

        if step < len(DATOS_PERSONALES_STEPS):
            campo, msg_campo = DATOS_PERSONALES_STEPS[step]
            valor, accion = procesar_campo_personal(datos, campo, raw_body or "")
            if accion == "reintento":
                datos["_step"] = step  # no avanzar: pedir el mismo campo
                tutela.datos_json = json.dumps(datos)
                session.commit()
                _r(respuestas, telefono, f"⚠️ {validar_campo_personal(campo, valor)}\n\n{msg_campo}")
                return {"ok": True, "respuestas": respuestas}
            datos.pop(f"_val_{campo}", None)
            datos[campo] = valor
            if campo in ("accionante_nombres", "accionante_apellidos"):
                _recomponer_nombre(datos)
            if accion == "aceptado":
                tutela.datos_json = json.dumps(datos)
                session.commit()
                _r(respuestas, telefono,
                   "⚠️ Guardé tu respuesta, pero parece inválida: "
                   f"{validar_campo_personal(campo, valor)} "
                   "Nuestro equipo la revisará antes de radicar.")
            step += 1
            datos["_step"] = step
            tutela.datos_json = json.dumps(datos)
            session.commit()

        if step < len(DATOS_PERSONALES_STEPS):
            _, msg = DATOS_PERSONALES_STEPS[step]
            _r(respuestas, telefono, msg)
        else:
            tutela.estado = "confirmar_datos_personales"
            session.commit()
            _r(respuestas, telefono, "✅ *Datos personales registrados.*")
            _mostrar_confirmacion_datos(telefono, respuestas, datos)
            return {"ok": True, "respuestas": respuestas}
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   CONFIRMAR DATOS PERSONALES — el cliente confirma o corrige
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "confirmar_datos_personales":
        if body in ("1", "si", "sí", "correcto", "correctos", "confirmar"):
            tutela.estado = "narracion"
            session.commit()
            _r(respuestas, telefono, "✅ *¡Datos confirmados!*\n\nAhora cuéntame tu caso.")
            _r(respuestas, telefono, NARRACION)
            return {"ok": True, "respuestas": respuestas}
        elif body in ("2", "corregir", "modificar", "no"):
            tutela.estado = "corrigiendo_datos_personales"
            session.commit()
            _r(respuestas, telefono,
               "✏️ *¿Qué dato quieres corregir?*\n\n"
               "Responde el número del dato:\n\n"
               + _menu_campos_personales())
            return {"ok": True, "respuestas": respuestas}
        _mostrar_confirmacion_datos(telefono, respuestas, datos)
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   CORRIGIENDO DATOS PERSONALES — elige campo y escribe nuevo valor
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "corrigiendo_datos_personales":
        campo = datos.get("_campo_corregir")
        if not campo:
            if body.isdigit() and 1 <= int(body) <= len(DATOS_PERSONALES_STEPS):
                idx = int(body) - 1
                campo, msg = DATOS_PERSONALES_STEPS[idx]
                datos["_campo_corregir"] = campo
                tutela.datos_json = json.dumps(datos)
                session.commit()
                _r(respuestas, telefono, msg)
            else:
                _r(respuestas, telefono,
                   "Escribe el número del dato que quieres corregir:\n\n"
                   + _menu_campos_personales())
        else:
            valor, accion = procesar_campo_personal(datos, campo, raw_body or "")
            if accion == "reintento":
                tutela.datos_json = json.dumps(datos)
                session.commit()
                _r(respuestas, telefono,
                   f"⚠️ {validar_campo_personal(campo, valor)}\n\n"
                   + _mensaje_campo_personal(campo))
                return {"ok": True, "respuestas": respuestas}
            datos.pop(f"_val_{campo}", None)
            datos[campo] = valor
            if campo in ("accionante_nombres", "accionante_apellidos"):
                _recomponer_nombre(datos)
            datos.pop("_campo_corregir", None)
            tutela.datos_json = json.dumps(datos)
            tutela.estado = "confirmar_datos_personales"
            session.commit()
            if accion == "aceptado":
                _r(respuestas, telefono,
                   f"⚠️ Guardé tu respuesta, pero parece inválida: "
                   f"{validar_campo_personal(campo, valor)}. La revisará el equipo.")
            _r(respuestas, telefono, "✅ *Dato actualizado.*")
            _mostrar_confirmacion_datos(telefono, respuestas, datos)
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   NARRACIÓN — recibir el relato del usuario
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "narracion":
        if es_audio and media_url:
            logger.info(f"Audio recibido de {telefono}: media_url={media_url[:40]} estado=narracion")
            ruta_audio = await _descargar_prueba(media_url)
            logger.info(f"Audio descargado: ruta={ruta_audio}")
            texto_audio = await transcribir_audio(ruta_audio) if ruta_audio else None
            logger.info(f"Transcripcion audio: {repr(texto_audio)[:200]}")
            if texto_audio:
                datos["_audio_temp"] = texto_audio
                tutela.estado = "confirmar_audio"
                tutela.datos_json = json.dumps(datos)
                session.commit()
                _r(respuestas, telefono, f'🎤 *Transcripción de tu audio:*\n\n"{texto_audio[:500]}"\n\n¿Es correcto?')
                _b(respuestas, telefono, "¿La transcripción es correcta?", [("1", "✅ Sí"), ("2", "✍️ No, escribir")])
                return {"ok": True, "respuestas": respuestas}
            _r(respuestas, telefono, "No pude procesar el audio. Escribe tu caso.")
            return {"ok": True, "respuestas": respuestas}

        try:
            datos_ia = await extraer_datos_caso(raw_body)
            aplicar_extraccion(datos, datos_ia)
        except Exception as e:
            logger.error(f"Error extrayendo datos caso: {e}")
            _r(respuestas, telefono, "Hubo un error procesando tu caso. Intenta de nuevo.")
            return {"ok": True, "respuestas": respuestas}

        tutela.datos_json = json.dumps(datos)
        tutela.estado = "revision_datos"
        session.commit()
        await _mostrar_revision_datos(session, tutela, datos, telefono, respuestas)
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   CONFIRMAR AUDIO
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "confirmar_audio":
        texto_audio = datos.get("_audio_temp", "")
        if body in ("1", "sí", "si", "correcto") or (body == "reintentar" and texto_audio):
            datos.pop("_audio_temp", None)
            try:
                datos_ia = await extraer_datos_caso(texto_audio)
            except Exception as e:
                logger.error(f"Error extrayendo datos tras audio: {e}")
                # Conservar el texto para reintentar sin regrabar el audio
                datos["_audio_temp"] = texto_audio
                tutela.datos_json = json.dumps(datos)
                session.commit()
                _r(respuestas, telefono, "Hubo un error procesando tu caso con la IA. Escribe *reintentar* en un momento.")
                return {"ok": True, "respuestas": respuestas}
            aplicar_extraccion(datos, datos_ia)
            tutela.datos_json = json.dumps(datos)
            tutela.estado = "revision_datos"
            session.commit()
            await _mostrar_revision_datos(session, tutela, datos, telefono, respuestas)
            return {"ok": True, "respuestas": respuestas}
        elif body in ("2", "no"):
            datos.pop("_audio_temp", None)
            tutela.datos_json = json.dumps(datos)
            session.commit()
            _r(respuestas, telefono, "✍️ *Escribe tu caso manualmente*\n\nCuéntame qué pasó con todos los detalles.")
            tutela.estado = "narracion"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        _b(respuestas, telefono, "¿La transcripción es correcta?", [("1", "✅ Sí"), ("2", "✍️ No, escribir")])
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   REVISION DATOS — cliente revisa extracción de IA
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "revision_datos":
        if body in ("1", "confirmar", "si", "sí", "correcto", "verdadero"):
            faltantes = campos_faltantes(datos)
            if faltantes:
                _r(respuestas, telefono, f"⚠️ *Faltan datos importantes:* {', '.join(faltantes)}")
                _r(respuestas, telefono, "¿Quieres agregar más detalles? Escribe tu caso de nuevo.")
                tutela.estado = "narracion"
                session.commit()
                _r(respuestas, telefono, NARRACION)
                return {"ok": True, "respuestas": respuestas}
            tutela.estado = "preguntas_clinicas"
            datos["_step_clinico"] = 1
            tutela.datos_json = json.dumps(datos)
            session.commit()
            _r(respuestas, telefono, "📋 *Para completar tu caso, responde estas preguntas:*")
            _r(respuestas, telefono, DATOS_CLINICOS_STEPS[0][1])
            return {"ok": True, "respuestas": respuestas}
        elif body in ("2", "corregir", "no", "editar"):
            _r(respuestas, telefono, "✍️ *Escribe tu caso de nuevo con más detalles o correcciones:*")
            _r(respuestas, telefono, NARRACION)
            tutela.estado = "narracion"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        await _mostrar_revision_datos(session, tutela, datos, telefono, respuestas)
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   PREGUNTAS CLINICAS — datos específicos del caso (afiliación, fechas)
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "preguntas_clinicas":
        step = datos.get("_step_clinico", 1)
        if step < len(DATOS_CLINICOS_STEPS):
            campo, _ = DATOS_CLINICOS_STEPS[step - 1]
            datos[campo] = raw_body or ""
        else:
            campo, _ = DATOS_CLINICOS_STEPS[-1]
            datos[campo] = raw_body or ""
        datos["_step_clinico"] = step + 1
        tutela.datos_json = json.dumps(datos)
        session.commit()
        if step < len(DATOS_CLINICOS_STEPS):
            _, msg = DATOS_CLINICOS_STEPS[step]
            _r(respuestas, telefono, msg)
        else:
            datos.pop("_step_clinico", None)
            tutela.datos_json = json.dumps(datos)
            tutela.estado = "pruebas_pendiente"
            session.commit()
            _b(respuestas, telefono, PRUEBAS_PREGUNTA, [("adjuntar", "📎 Adjuntar pruebas"), ("saltar", "⏭️ Sin soportes")])
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   PRUEBAS — preguntar si quiere adjuntar
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "pruebas_pendiente":
        if body in ("adjuntar", "adjuntar pruebas", "si adjuntar"):
            tutela.estado = "recibiendo_pruebas"
            session.commit()
            _r(respuestas, telefono, PRUEBAS_INSTRUCCION)
            _b(respuestas, telefono, "Envía tus soportes. Cuando termines, presiona el botón.", [("listo", "✅ No tengo más")])
            return {"ok": True, "respuestas": respuestas}
        elif body in ("saltar", "no", "continuar", "sin soportes", "no tengo"):
            await _mostrar_resumen_juramento(session, tutela, datos, telefono, respuestas)
            return {"ok": True, "respuestas": respuestas}
        _b(respuestas, telefono, PRUEBAS_PREGUNTA, [("adjuntar", "📎 Adjuntar pruebas"), ("saltar", "⏭️ Sin soportes")])
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   RECIBIENDO PRUEBAS — archivos adjuntos
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "recibiendo_pruebas":
        if body in ("listo", "seguir", "continuar", "terminé", "termine", "no tengo", "no tengo mas", "no tengo más", "generar"):
            await _mostrar_resumen_juramento(session, tutela, datos, telefono, respuestas)
            return {"ok": True, "respuestas": respuestas}
        if body in ("enviar_otro", "otro", "agregar", "si otro"):
            _r(respuestas, telefono, "📎 *Envía el siguiente soporte.*")
            _b(respuestas, telefono, "Cuando termines de enviar tus soportes, presiona el botón.", [("listo", "✅ No tengo más")])
            return {"ok": True, "respuestas": respuestas}

        if num_media > 0 and media_url:
            ruta_local = await _descargar_prueba(media_url)
            if ruta_local:
                datos.setdefault("pruebas_paths", []).append(ruta_local)
                # Los PDFs de soporte NO se interpretan: solo se guardan para
                # fusionarse intactos al final del PDF de la tutela. La vision
                # solo aplica a fotos (imagenes).
                if not ruta_local.lower().endswith(".pdf"):
                    try:
                        analisis = await analizar_imagen(ruta_local)
                    except Exception as e:
                        logger.error(f"Error analizando imagen: {e}")
                        analisis = ""
                else:
                    analisis = ""
                if analisis:
                    datos.setdefault("pruebas_analizadas", []).append(analisis)
                tutela.datos_json = json.dumps(datos)
                session.commit()
                num_soportes = len(datos.get("pruebas_paths", []))
                _r(respuestas, telefono, f"✅ *Soporte {num_soportes} recibido.*")
                _b(respuestas, telefono, "¿Tienes más soportes o generamos la tutela con los que hay?", [("enviar_otro", "📎 Enviar otro"), ("listo", "✅ No tengo más")])
                return {"ok": True, "respuestas": respuestas}
            _r(respuestas, telefono, _ultimo_error_descarga or "No pude descargar el archivo.")
            _b(respuestas, telefono, "¿Qué deseas hacer?", [("enviar_otro", "📎 Intentar otro"), ("listo", "✅ No tengo más")])
            return {"ok": True, "respuestas": respuestas}

        # Texto sin media: mostrar opciones para continuar
        _b(respuestas, telefono, "Presiona *No tengo más* cuando termines de enviar tus soportes.", [("listo", "✅ No tengo más")])
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   DATOS LISTOS — resumen + juramento
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "datos_listos":
        if body in ("1", "sí juro", "si juro", "juro"):
            await _generar_con_verificacion(session, tutela, datos, telefono, respuestas)
            return {"ok": True, "respuestas": respuestas}
        elif body in ("2", "no"):
            _r(respuestas, telefono, "Sin el juramento no podemos generar la tutela. Si cambias de opinión, responde *Juro*.")
            return {"ok": True, "respuestas": respuestas}
        _b(respuestas, telefono, JURAMENTO_TEXTO, [("1", "✅ Sí, juro"), ("2", "❌ No")])
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   POST-PDF — decisión: pagar o hazlo tú mismo
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "esperando_decision_radicacion":
        if body in ("1", "pagar", "radicar", "si radicar"):
            _enviar_link_pago(respuestas, telefono, tutela)
            tutela.estado = "esperando_pago"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        elif body in ("2", "no", "gratis", "hacer yo mismo", "hazlo yo mismo"):
            _r(respuestas, telefono, f"Entendido. Recibiste el PDF de tu tutela por este chat.\n\n"
                                     f"Si al intentar radicarla lo ves complejo, "
                                     f"pulsa el botón y lo hacemos por ti por *{texto_precio()}* "
                                     "sin tener que repetir tus datos.")
            _b(respuestas, telefono, "¿Qué prefieres hacer?", [("1", "💳 Procesen $29k"), ("2", "✍️ Lo hago yo")])
            tutela.estado = "hazlo_tu_mismo"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        _b(respuestas, telefono, POST_PDF_OPCIONES, [("1", "💳 Procesamiento $29k"), ("2", "✍️ Hazlo tú mismo")])
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   HAZLO TÚ MISMO — decidió radicar él, pero puede volver al pago
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "hazlo_tu_mismo":
        if body in ("1", "pagar", "radicar", "quiero que la radiquen", "quiero que lo radiquen",
                    "si radicar", "mejor pagar", "lo hacen ustedes", "quiero pagar", "no puedo",
                    "me parece complejo", "es complejo", "me ayudan", "radiquenla"):
            _enviar_link_pago(respuestas, telefono, tutela)
            tutela.estado = "esperando_pago"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        elif body in ("2", "no", "lo intento yo", "hacer yo mismo", "ya lo logre", "no necesito"):
            _r(respuestas, telefono, "Perfecto. Tu tutela quedó enviada en el PDF de este chat. "
                                     "Si cambias de opinión, pulsa el botón y te ayudamos.")
            tutela.estado = "completado"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        _b(respuestas, telefono,
           "📄 Recibiste el PDF de tu tutela. ¿Qué prefieres hacer?\n\n"
           f"1️⃣ *Que la procesen por ti* — {texto_precio()}\n"
           "2️⃣ *Seguir tú mismo*", [("1", "💳 Procesen $29k"), ("2", "✍️ Sigo yo")])
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   CONFIRMAR PAGO — fricción adicional
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "confirmar_pago":
        if body in ("confirmar_pago", "si pagar", "sí pagar", "pagar", "1"):
            link_pago = f"{settings.app_url}/pago/{tutela.id}"
            _r(respuestas, telefono,
               f"💰 *Procesamiento automático*\n\n"
               f"Para completar el pago de *{texto_precio()}*:\n\n"
               f"🔗 {link_pago}\n\n"
               f"⚠️ *Importante:* Procesamos tu tutela y te entregamos el "
               f"*número de seguimiento* en máximo *4 horas hábiles* (lun-vie 8am-5pm).")
            tutela.estado = "esperando_pago"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        elif body in ("2", "no", "gratis", "hacer yo mismo", "hazlo yo mismo"):
            _r(respuestas, telefono, "Entendido. Recibiste el PDF con tu tutela por este chat.\n\n"
                                     "Si al intentarlo lo ves complejo, pulsa el botón y lo hacemos "
                                     f"por ti por *{texto_precio()}* sin repetir datos.")
            _b(respuestas, telefono, "¿Qué prefieres hacer?", [("1", "💳 Procesen $29k"), ("2", "✍️ Lo hago yo")])
            tutela.estado = "hazlo_tu_mismo"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        _b(respuestas, telefono, CONFIRMAR_PAGO_TEXTO, [("confirmar_pago", "✅ Sí, pagar $29k"), ("2", "✍️ Hazlo tú mismo")])
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   ESPERANDO PAGO — el equipo humano confirma el pago desde el admin
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "esperando_pago":
        if body in ("pagado", "pago confirmado", "si", "ok", "1"):
            _r(respuestas, telefono,
               "✅ *¡Pago recibido!*\n\n"
               f"Hemos confirmado tu pago por {texto_precio()}. "
               "Ya iniciamos la radicación de tu tutela ante la Rama Judicial. "
               "En cuanto quede *radicada* te enviaremos el *número de radicado* por este chat.\n\n"
               "Gracias por confiar en nosotros.")
            tutela.estado = "pago_por_confirmar"
            session.commit()
            logger.info(f"Tutela {tutela.id}: usuario reporta pago, queda pago_por_confirmar")
            return {"ok": True, "respuestas": respuestas}
        _r(respuestas, telefono, "Estamos esperando la confirmación de tu pago. Te avisaremos por este chat.")
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   PAGO POR CONFIRMAR / PAGO CONFIRMADO — esperando radicación manual
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado in ("pago_por_confirmar", "pago_confirmado"):
        _r(respuestas, telefono,
           "✅ *Tu pago está confirmado.*\n\n"
           "Ya iniciamos la radicación de tu tutela ante la Rama Judicial. "
           "En cuanto quede *radicada* te enviaremos el *número de radicado* por este chat.")
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   PENDIENTE RADICACIÓN / FALLIDA — en proceso o con fallo
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado in ("pendiente_radicacion", "fallida"):
        _r(respuestas, telefono,
           "🔁 *Tu tutela está en proceso de radicación* ante la Rama Judicial.\n\n"
           "En cuanto quede *radicada*, te enviaremos el *número de radicado* "
           "y la notificación llegará también a tu correo. Gracias por la paciencia.")
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   COMPLETADO / POR DEFECTO
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "completado":
        _r(respuestas, telefono, "✅ *Tu trámite ha sido completado.*\n\nSi necesitas ayuda con otro trámite, escribe *Hola*.")
        return {"ok": True, "respuestas": respuestas}

    _r(respuestas, telefono, MENU_DEFAULT)
    return {"ok": True, "respuestas": respuestas}


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _r(respuestas: list[str], telefono: str, mensaje: str) -> None:
    ok = enviar_texto(telefono, mensaje)
    respuestas.append(mensaje)
    _acumular_envio(ok)
    return ok


def _enviar_link_pago(respuestas: list[str], telefono: str, tutela) -> None:
    """Envía el mensaje con el link de pago de Mercado Pago."""
    link_pago = f"{settings.app_url}/pago/{tutela.id}"
    _r(respuestas, telefono,
       f"💰 *Procesamiento automático*\n\n"
       f"Para completar el pago de *{texto_precio()}*:\n\n"
       f"🔗 {link_pago}\n\n"
       f"⚠️ *Importante:* Procesamos tu tutela y te entregamos el "
       f"*número de seguimiento* en máximo *4 horas hábiles* (lun-vie 8am-5pm).")


def _b(respuestas: list[str], telefono: str, texto: str, botones: list[tuple[str, str]]) -> None:
    ok = enviar_botones(telefono, texto, botones)
    respuestas.append(f"[BOTONES] {texto} | {botones}")
    _acumular_envio(ok)
    return ok


async def _mostrar_revision_datos(session, tutela, datos: dict, telefono: str, respuestas: list[str]) -> None:
    """Muestra al cliente los datos extraídos por la IA para confirmar o corregir."""
    preview = await generar_preview(datos)
    
    _r(respuestas, telefono, "📋 *Revisa los datos extraídos de tu caso:*\n\n" + preview)
    _b(respuestas, telefono, "¿Los datos son correctos?", [("1", "✅ Sí, confirmar"), ("2", "✏️ Corregir")])


async def _mostrar_resumen_juramento(session, tutela, datos: dict, telefono: str, respuestas: list[str]) -> None:
    hechos = datos.get("hechos", "")
    resumen = (
        "📋 *Resumen de tu tutela*\n\n"
        f"👤 *Nombre:* {datos.get('accionante_nombre', '_____')}\n"
        f"🆔 *Documento:* {datos.get('accionante_tipo_doc', 'CC')} {datos.get('accionante_cedula', '_____')}\n"
        f"📧 *Email:* {datos.get('accionante_email', '_____')}\n"
        f"🏙️ *Ciudad:* {datos.get('ciudad', '_____')}\n"
        f"🏛️ *Accionado:* {datos.get('accionado', '_____')}\n"
        f"📝 *Hechos:* {hechos[:300]}...\n"
    )
    _r(respuestas, telefono, resumen)
    tutela.estado = "datos_listos"
    session.commit()
    _b(respuestas, telefono, JURAMENTO_TEXTO, [("1", "✅ Sí, juro"), ("2", "❌ No")])


_PERSONALES_LABEL = {
    "accionante_nombres": "👤 Nombres",
    "accionante_apellidos": "👤 Apellidos",
    "accionante_tipo_doc": "🪪 Tipo documento",
    "accionante_cedula": "🆔 Documento",
    "accionante_telefono": "📱 Teléfono",
    "accionante_email": "📧 Correo",
    "ciudad": "🏙️ Ciudad",
    "accionante_direccion": "📍 Dirección",
    "departamento": "🗺️ Departamento",
    "accionado": "🏥 EPS",
}


def _resumen_datos_personales(datos: dict) -> str:
    lineas = []
    for campo, label in _PERSONALES_LABEL.items():
        valor = datos.get(campo, "")
        lineas.append(f"{label}: {valor or '_____'}")
    return "📋 *Tus datos personales:*\n\n" + "\n".join(lineas)


def _menu_campos_personales() -> str:
    lineas = []
    for i, (campo, _) in enumerate(DATOS_PERSONALES_STEPS, start=1):
        label = _PERSONALES_LABEL.get(campo, campo)
        lineas.append(f"{i}. {label}")
    return "\n".join(lineas)


def _mostrar_confirmacion_datos(telefono: str, respuestas: list[str], datos: dict) -> None:
    _r(respuestas, telefono, _resumen_datos_personales(datos))
    _b(respuestas, telefono, "¿Tus datos personales son correctos?", [("1", "✅ Sí, correctos"), ("2", "✏️ Corregir")])


# Hosts permitidos para descargar archivos adjuntos (soportes de WhatsApp).
# Meta sirve los medios desde su CDN; Twilio desde api.twilio.com. Cualquier
# otro host se rechaza (SSRF).
_HOSTS_MEDIA = {
    "graph.facebook.com",
    "lookaside.fbsbx.com",
    "cdn.fbsbx.com",
    "scontent.fbcdn.net",
    "api.twilio.com",
    "mms-gw.twilio.com",
}
MAX_PRUEBA_BYTES = 15 * 1024 * 1024  # 15 MB (Meta acepta documentos hasta 16 MB; dejamos margen)

# Motivo de la última prueba no descargada ("" si no aplica). Lo usa el webhook
# para avisar al usuario cuándo su archivo supera el límite de tamaño.
_ultimo_error_descarga = ""


def _host_permitido(url: str) -> bool:
    """Valida el esquema y el host de una URL de descarga de soportes.

    Solo HTTPS y solo hosts de Meta/Twilio conocidos. Devuelve False para
    esquemas no https y para cualquier host que no esté en la lista blanca.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in _HOSTS_MEDIA:
        return True
    # Permite subdominios de los CDN de Meta (ej. scontent-<region>.fbcdn.net)
    return host.endswith(".fbcdn.net") or host.endswith(".fbsbx.com")


def _sin_ip_privada(url: str) -> bool:
    """Rechaza URLs que resuelvan a IPs privadas/reservadas (SSRF interno).

    Comprueba IPs literales; para hostnames intenta resolver todo el conjunto
    de registros A y rechaza si alguno es loopback, link-local, privado o
    reservado. Si el hostname no resuelve (DNS falla), no bloquea: la lista
    blanca de hosts ya restringe a dominios de Meta/Twilio.
    """
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    try:
        ips = [i[4][0] for i in socket.getaddrinfo(host, None)]
    except socket.gaierror:
        return True  # no resuelve; el allowlist de hosts ya protege
    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if not addr.is_global:
            return False
    return True


def _permite_descargar(url: str) -> bool:
    """Chequeo combinado de seguridad antes de tocar la red."""
    return _host_permitido(url) and _sin_ip_privada(url)


async def _descargar_prueba(url: str) -> str | None:
    # Se limpia en cada llamada: guarda el motivo por el que una prueba NO pudo
    # descargarse, para que el webhook pueda dar un mensaje claro al usuario.
    global _ultimo_error_descarga
    _ultimo_error_descarga = ""
    if not url:
        return None
    try:
        # Meta envía un media id numérico; se resuelve primero, pero la URL
        # final firmada debe pasar el filtro de host. Para URLs directas se
        # valida aquí antes de cualquier petición.
        if not url.isdigit() and not _permite_descargar(url):
            logger.error(f"_descargar_prueba: URL rechazada por seguridad: {url[:80]}")
            return None
        mime_type = ""
        headers = {}
        auth = None

        # Meta media ID (solo números) → resolver URL y mime_type via Graph API
        if url.isdigit() and settings.meta_access_token:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(
                    f"https://graph.facebook.com/v22.0/{url}",
                    headers={"Authorization": f"Bearer {settings.meta_access_token}"},
                )
            if r.status_code == 200:
                data = r.json()
                url = data.get("url", url)
                mime_type = data.get("mime_type", "").lower()
            else:
                logger.error(f"Meta get media {url} falló: {r.status_code} {r.text[:150]}")
            # La URL firmada de Meta SÍ requiere el token de acceso
            headers = {"Authorization": f"Bearer {settings.meta_access_token}"}

        # Tras resolver el id de Meta, la URL firmada también debe pasar el filtro.
        if not _permite_descargar(url):
            logger.error(f"_descargar_prueba: URL firmada rechazada por seguridad: {url[:80]}")
            return None

        ext = _ext_desde_mime(mime_type, url)
        ruta = path_prueba(ext)
        logger.info(f"_descargar_prueba: mime={mime_type} ext={ext} ruta={ruta}")

        if "api.twilio.com" in url and settings.twilio_account_sid:
            auth = httpx.BasicAuth(settings.twilio_account_sid, settings.twilio_auth_token)
            headers = {}

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.get(url, headers=headers, auth=auth)
        logger.info(f"_descargar_prueba: download status={r.status_code} bytes={len(r.content)}")
        # Tras redirects, el host final también debe ser permitido.
        if not _permite_descargar(str(r.url)):
            logger.error(f"_descargar_prueba: redirect final rechazado: {str(r.url)[:80]}")
            return None
        if len(r.content) > MAX_PRUEBA_BYTES:
            pesos_mb = MAX_PRUEBA_BYTES // (1024 * 1024)
            _ultimo_error_descarga = (
                f"El archivo pesa más de {pesos_mb} MB, y WhatsApp no acepta "
                "documentos más grandes. Comprímelo o reduce su tamaño y envíalo de nuevo."
            )
            logger.error(f"_descargar_prueba: archivo demasiado grande: {len(r.content)} bytes")
            return None
        if r.status_code == 200:
            async with aiofiles.open(ruta, "wb") as f:
                await f.write(r.content)
            return ruta
    except Exception as e:
        logger.error(f"Error descargando prueba: {e}")
    return None


def _ext_desde_mime(mime_type: str, url: str = "") -> str:
    """Elige la extensión correcta según el mime_type de Meta (o la URL)."""
    if not mime_type:
        for e in (".png", ".jpeg", ".pdf", ".jpg", ".gif", ".webp", ".ogg", ".mp3", ".m4a", ".mp4"):
            if e in url.lower():
                return e
        return ".jpg"
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "application/pdf": ".pdf",
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/amr": ".amr",
        "audio/x-m4a": ".m4a",
        "video/mp4": ".mp4",
        "video/3gpp": ".3gp",
    }
    for m, e in mapping.items():
        if mime_type.startswith(m):
            return e
    if mime_type.startswith("audio/"):
        return ".ogg"
    if mime_type.startswith("image/"):
        return ".jpg"
    if mime_type.startswith("video/"):
        return ".mp4"
    return ".jpg"


def _citas_verificadas_para_prompt(session, vertical: str = "salud") -> list[dict]:
    """Citas legales verificadas (whitelist) para inyectar en el prompt de la IA.

    Así la argumentación jurídica siempre se apoya en normativa real
    (Constitución, leyes, decretos y jurisprudencia) verificada por el equipo.
    """
    filas = (
        session.execute(
            select(CitaLegal).where(CitaLegal.aplica_a == vertical, CitaLegal.vigente.is_(True))
        )
        .scalars()
        .all()
    )
    return [
        {
            "referencia": c.referencia,
            "texto_resumen": c.texto_resumen or "",
            "titulo_corto": c.titulo_corto or "",
        }
        for c in filas
    ]


async def _generar_con_verificacion(session, tutela, datos: dict, telefono: str, respuestas: list[str]) -> str | None:
    _r(respuestas, telefono, "⏳ *Generando tu tutela...* Esto puede tardar unos segundos.")

    # El PDF usa el texto de la IA (estructura I-XI) cuando la IA responde;
    # si falla, generar_pdf cae al modo plantilla construido desde `datos`.
    contenido_final: str | None = None
    try:
        texto = await generar_tutela(datos, citas=_citas_verificadas_para_prompt(session))
        if not texto:
            raise ValueError("la IA no devolvió texto")

        citas = await extraer_citas(texto)
        validas: list[dict] = []
        if citas:
            resultado = verificar_citas(citas, session)
            validas = resultado["validas"]
            if resultado["pendientes_revision"]:
                tutela.estado_verificacion = "verificada_con_pendientes"
                guardar_pendientes(tutela.id, resultado["pendientes_revision"], session)
                texto = limpiar_texto_para_pdf(texto, resultado["pendientes_revision"])
            else:
                tutela.estado_verificacion = "verificada"

        # Fundamentación jurídica garantizada: aunque la IA no haya desarrollado
        # los fundamentos, el escrito cita la normativa verificada de la whitelist.
        contenido_final = insertar_fundamentacion(texto, fundamentacion_juridica_extra(validas))
    except Exception as e:
        logger.error(f"IA/verificación falló (se genera PDF en modo plantilla): {e}")
        tutela.estado_verificacion = "pendiente_revision"

    try:
        ruta_pdf = generar_pdf(datos, contenido_final)
    except Exception as e:
        logger.error(f"Error generando PDF para tutela {tutela.id}: {e}")
        _r(respuestas, telefono, "Hubo un error técnico generando tu PDF. Escribe *juro* para reintentarlo.")
        return None
    tutela.pdf_path = ruta_pdf
    tutela.datos_json = json.dumps(datos)
    tutela.estado = "pdf_generado"
    session.commit()

    _r(respuestas, telefono, "✅ *¡Tutela generada!*")
    ok = enviar_documento(telefono, ruta_pdf, os.path.basename(ruta_pdf))
    _acumular_envio(ok)
    if not ok:
        _r(respuestas, telefono, "⚠️ No pude enviar el PDF. Intenta de nuevo.")
    _b(respuestas, telefono, POST_PDF_OPCIONES, [("1", "💳 Radicación $29k"), ("2", "✍️ Hazlo tú mismo")])
    tutela.estado = "esperando_decision_radicacion"
    session.commit()
    return ruta_pdf


# ═══════════════════════════════════════════════════════════════════
# TEXTOS
# ═══════════════════════════════════════════════════════════════════

CONSENTIMIENTO_VERSION = "v1.0"

BIENVENIDA = (
    "👋 *¡Hola! Soy el asistente de TutelApp.*\n\n"
    "Soy una herramienta tecnológica diseñada para ayudarte a redactar "
    "tu propia acción de tutela. Importante: No soy un abogado ni represento "
    "a la Rama Judicial. Mi función es facilitarte la creación del documento "
    "que tú mismo presentarás.\n\n"
    "Comencemos con la autorización de datos."
)

AVISO_SALIR = (
    "🚪 *¿Te equivocaste en algún dato?* Escribe *Salir* en cualquier momento "
    "y borramos lo capturado para empezar de cero."
)

def aviso_privacidad() -> str:
    """Aviso de tratamiento de datos con el link según el dominio configurado (app_url)."""
    return (
        "📄 *Aviso de Tratamiento de Datos*\n\n"
        "En TutelApp protegemos tu información. Para ayudarte con tu tutela, "
        "trataremos tus datos personales y de salud bajo la Ley 1581 de 2012.\n\n"
        "🔹 *Finalidad:* Crear y radicar técnicamente tu acción de tutela.\n"
        "🔹 *Datos Sensibles:* Al continuar, autorizas el procesamiento de tu caso médico "
        "únicamente para este trámite.\n"
        "🔹 *Tus Derechos:* Puedes actualizar o eliminar tus datos en cualquier momento "
        "escribiendo *Eliminar mis datos*, o *Salir* para empezar el proceso de cero.\n\n"
        f"Consulta nuestra política completa aquí: {settings.app_url}/privacidad\n\n"
        "¿Autorizas el tratamiento de tus datos para iniciar?"
    )

NARRACION = (
    "✍️ *Cuéntame tu caso de salud en detalle*\n\n"
    "Incluye:\n"
    "• Qué EPS te negó el servicio\n"
    "• Qué tratamiento, cita o medicamento te negaron\n"
    "• Fechas de las negaciones\n"
    "• Qué le pides al juez que ordene\n\n"
    "🎤 Puedes enviar un *audio* contando tu caso."
)

PRUEBAS_PREGUNTA = (
    "📎 ¿Tienes soportes o pruebas para adjuntar?\n\n"
    "Ej: fórmulas médicas, resultados, respuestas de la EPS, pantallazos."
)

PRUEBAS_INSTRUCCION = (
    "📸 *Envía tus soportes*\n\n"
    "Puedes enviar fotos o documentos. "
    "Las analizaré y las incluiré como pruebas en tu tutela."
)

JURAMENTO_TEXTO = (
    "⚖️ *Juramento*\n\n"
    "¿Afirmas bajo la gravedad de juramento que *no has interpuesto otra acción de tutela* "
    "por los mismos hechos y derechos ante ningún otro juez?"
)

POST_PDF_OPCIONES = (
    "📄 *PDF generado y enviado*\n\n"
    "Ahora tienes 2 opciones:\n\n"
    f"1️⃣ *Procesamiento automático* — *{texto_precio()}*\n"
    "   Procesamos tu tutela ante la Rama Judicial.\n"
    "   Resultado en máximo *4 horas hábiles*.\n"
    "   Te entregamos el número de seguimiento.\n\n"
    "2️⃣ *Hazlo tú mismo* — GRATIS"
)

CONFIRMAR_PAGO_TEXTO = (
    "💳 *Procesamiento automático*\n\n"
    f"Por *{texto_precio()}* procesamos tu tutela ante la Rama Judicial.\n"
    "Incluye:\n"
    "✅ Procesamiento en el portal oficial\n"
    "✅ Número de seguimiento y constancia\n"
    "✅ Resultado en máximo 4 horas hábiles\n\n"
    "¿Quieres continuar con el pago?"
)

MENU_DEFAULT = (
    "🤖 *Asistente TutelApp*\n\n"
    "Comandos:\n"
    "• *Hola* — iniciar o continuar\n"
    "• *Salir* — borrar datos y reiniciar el proceso\n"
    "• *Eliminar mis datos* — borrar tu información\n"
    "• *Detener* — pausar la conversación"
)

# Orden de recolección de datos personales: (campo, mensaje)
# El último paso (accionado/EPS) es la entidad que se usará como accionado
# en los datos de la tutela; por eso está protegido en CAMPOS_PERSONALES_GUARDADOS.
DATOS_PERSONALES_STEPS = [
    ("accionante_nombres", "👤 Escribe tus *nombres* (ej: María Fernanda):"),
    ("accionante_apellidos", "👤 Ahora tus *apellidos* (ej: Pérez Gómez):"),
    ("accionante_tipo_doc", "🪪 Tipo de documento (CC, CE, Pasaporte):"),
    ("accionante_cedula", "🆔 Número de documento (sin puntos):"),
    ("accionante_telefono", "📱 Teléfono celular:"),
    ("accionante_email", "📧 Correo electrónico (para notificaciones del juzgado):"),
    ("ciudad", "🏙️ ¿En qué ciudad vives?:"),
    ("accionante_direccion", "📍 Dirección de residencia (calle y número):"),
    ("departamento", "🗺️ Departamento (ej: Cundinamarca, Antioquia):"),
    ("accionado", "🏥 ¿Cuál es el *nombre de tu EPS*? (Ej: Nueva EPS, Sanitas, Salud Total):"),
]


def _mensaje_campo_personal(campo: str) -> str:
    """Retorna el mensaje con el que se pregunta un campo de datos personales."""
    for c, msg in DATOS_PERSONALES_STEPS:
        if c == campo:
            return msg
    return "Reintenta por favor."


def _recomponer_nombre(datos: dict) -> str:
    """Compone `accionante_nombre` (el nombre completo) desde los campos
    estructurados `accionante_nombres`/`accionante_apellidos`.

    Todo el código aguas abajo (PDF, prompt de la IA, resumen, `_separar_nombre`
    del bot) sigue leyendo `accionante_nombre`, así que no hay que tocarlo.
    """
    nombre = " ".join(
        p for p in (datos.get("accionante_nombres", ""), datos.get("accionante_apellidos", "")) if p
    ).strip()
    if nombre:
        datos["accionante_nombre"] = nombre
    return nombre

# Datos clínicos del caso que pregunta el bot (evita que la IA los invente): (campo, mensaje)
DATOS_CLINICOS_STEPS = [
    ("medicamentos_o_servicio", "💉 ¿Qué *tratamiento, medicamento o servicio* te negaron o no autorizaron?"),
    ("fecha_solicitud", "📅 ¿En qué *fecha* lo solicitaste? (ej: 10/01/2026)"),
    ("fecha_negativa", "🚫 ¿En qué *fecha* te negaron o no dieron respuesta? (ej: 15/01/2026 o *no recuerdo*)"),
]
