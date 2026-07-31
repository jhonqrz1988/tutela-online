import datetime
import hashlib
import hmac
import json
import logging

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from app.config import settings
from app.database import get_session
from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import MensajeWhatsApp
from app.services.documento_service import generar_pdf
from app.services.ia_service import (
    analizar_imagen,
    campos_faltantes,
    extraer_citas,
    extraer_datos_caso,
    generar_preview,
    generar_tutela,
    transcribir_audio,
)
from app.services.radicacion_service import iniciar_radicacion
from app.services.verificacion_service import (
    guardar_pendientes,
    limpiar_texto_para_pdf,
    verificar_citas,
)
from app.services.whatsapp_service import enviar_botones, enviar_documento, enviar_texto
from app.utils.file_utils import path_prueba

router = APIRouter()

logger = logging.getLogger(__name__)


@router.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request, session=Depends(get_session)):
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
    """Verifica la firma HMAC-SHA256 del webhook de Meta."""
    if not settings.meta_app_secret:
        return True  # Si no hay secret configurado, saltar verificación
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


@router.post("/webhook/meta")
async def webhook_meta(request: Request, session=Depends(get_session)):
    # Verificar firma de Meta
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_meta_signature(body, signature):
        return {"ok": False, "error": "Invalid signature"}
    
    data = await request.json()
    entry = data.get("entry", [])
    respuestas = []
    for e in entry:
        changes = e.get("changes", [])
        for c in changes:
            value = c.get("value", {})
            messages = value.get("messages", [])
            for msg in messages:
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
                    es_audio = False
                elif msg_type == "audio":
                    es_audio = True
                    media_url = msg.get("audio", {}).get("id", "")

                try:
                    respuesta = await procesar_mensaje(session, telefono, body_text, num_media, media_url, es_audio)
                    if respuesta.get("respuestas"):
                        respuestas.extend(respuesta["respuestas"])
                except Exception as e:
                    logger.error(f"Error procesando mensaje {msg_type} de {telefono}: {e}")
    return {"ok": True, "respuestas": respuestas} if respuestas else {"ok": True}


@router.post("/webhook/zapi")
async def webhook_zapi(request: Request, session=Depends(get_session)):
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

async def procesar_mensaje(
    session, telefono: str, body: str, num_media: int, media_url: str, es_audio: bool
) -> dict:
    respuestas: list[str] = []
    body = (body or "").strip()
    raw_body = body
    body = body.lower()

    msg_orm = MensajeWhatsApp(from_number=telefono, body=body, tipo_mensaje="audio" if es_audio else "texto", media_url=media_url)
    session.add(msg_orm)
    session.commit()

    user = session.execute(select(User).where(User.telefono == telefono)).scalar_one_or_none()

    # ─── NUEVO USUARIO ──────────────────────────────────────────────
    if not user:
        user = User(telefono=telefono, estado="nuevo", consentimiento=False)
        session.add(user)
        session.commit()
        _r(respuestas, telefono, BIENVENIDA)
        _b(respuestas, telefono, AVISO_PRIVACIDAD, [("acepto", "✅ Acepto"), ("no", "❌ No")])
        return {"ok": True, "respuestas": respuestas}

    # ─── ELIMINAR DATOS ──────────────────────────────────────────────
    if body in ("eliminar", "eliminar mis datos", "borrar", "borrar mis datos"):
        session.query(MensajeWhatsApp).where(MensajeWhatsApp.from_number == telefono).delete()
        for t in session.execute(select(Tutela).where(Tutela.user_id == user.id)).scalars():
            session.delete(t)
        session.delete(user)
        session.commit()
        _r(respuestas, telefono, "🗑️ *Tus datos han sido eliminados.*\n\nSi necesitas ayuda en el futuro, escribe *Hola* y empezamos de nuevo.")
        return {"ok": True, "respuestas": respuestas}

    # ─── CONSENTIMIENTO ──────────────────────────────────────────────
    if user.estado == "nuevo":
        if body in ("acepto", "sí", "si", "ok", "si acepto"):
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
            return {"ok": True, "respuestas": respuestas}
        elif body in ("no", "no acepto", "cancelar"):
            user.estado = "rechazado"
            session.commit()
            _r(respuestas, telefono, "❌ *Has cancelado.*\n\nSin tu autorización no podemos procesar la tutela. Si cambias de opinión, escribe *Hola*.")
            return {"ok": True, "respuestas": respuestas}
        _b(respuestas, telefono, AVISO_PRIVACIDAD, [("acepto", "✅ Acepto"), ("no", "❌ No")])
        return {"ok": True, "respuestas": respuestas}

    if user.estado == "rechazado":
        _r(respuestas, telefono, "❌ No puedes usar el servicio sin aceptar el tratamiento de datos.\n\nSi cambias de opinión, escribe *Hola*.")
        return {"ok": True, "respuestas": respuestas}

    # ─── HOLA DE USUARIO EXISTENTE ─────────────────────────────────────
    if body in ("hola", "menú", "menu", "inicio", "empezar"):
        user.estado = "activo"
        session.commit()
        tutela = session.execute(
            select(Tutela).where(
                Tutela.user_id == user.id,
                Tutela.estado.in_(["recogiendo_datos", "narracion", "confirmar_audio", "revision_datos",
                                   "pruebas_pendiente",
                                   "recibiendo_pruebas", "datos_listos", "pdf_generado",
                                   "esperando_decision_radicacion",
                                   "confirmar_pago", "esperando_pago", "completado"]),
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
                    _r(respuestas, telefono, NARRACION)
            elif tutela.estado == "narracion":
                _r(respuestas, telefono, NARRACION)
            elif tutela.estado == "revision_datos":
                await _mostrar_revision_datos(session, tutela, datos, telefono, respuestas)
            elif tutela.estado == "pruebas_pendiente":
                _b(respuestas, telefono, PRUEBAS_PREGUNTA, [("adjuntar", "📎 Adjuntar pruebas"), ("saltar", "⏭️ Sin soportes")])
            elif tutela.estado == "datos_listos":
                _b(respuestas, telefono, JURAMENTO_TEXTO, [("1", "✅ Sí, juro"), ("2", "❌ No")])
            else:
                _r(respuestas, telefono, "🤖 *Tutela Online* — Continúa donde lo dejaste.")
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
                               "pruebas_pendiente",
                               "recibiendo_pruebas", "datos_listos", "pdf_generado",
                               "esperando_decision_radicacion",
                               "confirmar_pago", "esperando_pago", "completado"]),
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
            campo, _ = DATOS_PERSONALES_STEPS[step]
            # Si salta al siguiente (escribe adelante), detectar por comas
            datos[campo] = raw_body or ""
            step += 1
            datos["_step"] = step
            tutela.datos_json = json.dumps(datos)
            session.commit()

        if step < len(DATOS_PERSONALES_STEPS):
            _, msg = DATOS_PERSONALES_STEPS[step]
            _r(respuestas, telefono, msg)
        else:
            tutela.estado = "narracion"
            session.commit()
            _r(respuestas, telefono, "✅ *Datos personales registrados.*")
            _r(respuestas, telefono, NARRACION)

        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   NARRACIÓN — recibir el relato del usuario
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "narracion":
        if es_audio and media_url:
            ruta_audio = await _descargar_prueba(media_url)
            texto_audio = await transcribir_audio(ruta_audio) if ruta_audio else None
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
            for k, v in datos_ia.items():
                if v and k not in ("tipo",):
                    datos[k] = v
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
        if body in ("1", "sí", "si", "correcto"):
            datos.pop("_audio_temp", None)
            datos_ia = await extraer_datos_caso(texto_audio)
            for k, v in datos_ia.items():
                if v and k not in ("tipo",):
                    datos[k] = v
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
            tutela.estado = "pruebas_pendiente"
            session.commit()
            _b(respuestas, telefono, PRUEBAS_PREGUNTA, [("adjuntar", "📎 Adjuntar pruebas"), ("saltar", "⏭️ Sin soportes")])
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
    #   PRUEBAS — preguntar si quiere adjuntar
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "pruebas_pendiente":
        if body in ("adjuntar", "adjuntar pruebas", "si adjuntar"):
            tutela.estado = "recibiendo_pruebas"
            session.commit()
            _r(respuestas, telefono, PRUEBAS_INSTRUCCION)
            _b(respuestas, telefono, "Cuando termines de enviar tus soportes, presiona el botón.", [("listo", "✅ Listo, seguir")])
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
        if body in ("listo", "seguir", "continuar", "terminé", "termine"):
            await _mostrar_resumen_juramento(session, tutela, datos, telefono, respuestas)
            return {"ok": True, "respuestas": respuestas}

        if num_media > 0 and media_url:
            ruta_local = await _descargar_prueba(media_url)
            if ruta_local:
                datos.setdefault("pruebas_paths", []).append(ruta_local)
                try:
                    analisis = await analizar_imagen(ruta_local)
                except Exception as e:
                    logger.error(f"Error analizando imagen: {e}")
                    analisis = ""
                if analisis:
                    datos.setdefault("pruebas_analizadas", []).append(analisis)
                tutela.datos_json = json.dumps(datos)
                session.commit()
                _r(respuestas, telefono, "✅ *Soporte recibido.*")
                _b(respuestas, telefono, "¿Más soportes o continuamos?", [("listo", "✅ Listo, seguir")])
                return {"ok": True, "respuestas": respuestas}
            _r(respuestas, telefono, "No pude descargar el archivo. Presiona *Listo* para continuar.")
            _b(respuestas, telefono, "¿Qué deseas hacer?", [("listo", "✅ Listo, seguir")])
            return {"ok": True, "respuestas": respuestas}

        # Texto sin media: mostrar solo botón para seguir
        _b(respuestas, telefono, "Presiona *Listo* cuando termines de enviar tus soportes.", [("listo", "✅ Listo, seguir")])
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
    #   POST-PDF — decisión: pagar o video gratis
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "esperando_decision_radicacion":
        if body in ("1", "pagar", "radicar", "si radicar"):
            _r(respuestas, telefono, CONFIRMAR_PAGO_TEXTO)
            _b(respuestas, telefono, "¿Confirmas que deseas radicar tu tutela por *$20.000 COP*?", [("confirmar_pago", "✅ Sí, pagar ahora"), ("2", "❌ No, mejor ver video")])
            tutela.estado = "confirmar_pago"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        elif body in ("2", "video", "gratis", "hacer yo mismo"):
            _r(respuestas, telefono, VIDEO_GUIA)
            tutela.estado = "completado"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        _b(respuestas, telefono, POST_PDF_OPCIONES, [("1", "💳 Radicación $20k"), ("2", "📹 Video gratis")])
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   CONFIRMAR PAGO — fricción adicional
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "confirmar_pago":
        if body in ("confirmar_pago", "si pagar", "sí pagar", "pagar", "1"):
            link_pago = f"{settings.app_url}/pago/{tutela.id}"
            _r(respuestas, telefono,
               f"💰 *Radicación automática*\n\n"
               f"Para completar el pago de *$20.000 COP*:\n\n"
               f"🔗 {link_pago}\n\n"
               f"Después de pagar, escribe *Pagado* para confirmar.\n\n"
               f"⚠️ *Importante:* La radicación toma máximo *4 horas hábiles* (lun-vie 8am-5pm).\n"
               f"Te enviaremos el número de radicado y la constancia oficial.")
            tutela.estado = "esperando_pago"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        elif body in ("2", "no", "video", "mejor video"):
            _r(respuestas, telefono, VIDEO_GUIA)
            tutela.estado = "completado"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        _b(respuestas, telefono, CONFIRMAR_PAGO_TEXTO, [("confirmar_pago", "✅ Sí, pagar $20k"), ("2", "🎥 Mejor ver video")])
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   ESPERANDO PAGO
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "esperando_pago":
        if body in ("pagado", "pago confirmado", "si", "ok", "1"):
            _r(respuestas, telefono, "✅ *¡Pago recibido!* Radicaremos tu tutela en máximo *4 horas hábiles*. Te notificaremos cuando esté lista.")
            resultado = await iniciar_radicacion(tutela.id)
            if resultado.get("ok"):
                _r(respuestas, telefono, f"✅ *¡Tutela radicada!*\nN° radicado: {resultado.get('num_radicado', 'N/A')}")
            else:
                _r(respuestas, telefono, f"⚠️ Error en radicación: {resultado.get('error', 'desconocido')}")
            tutela.estado = "completado"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        _r(respuestas, telefono, "Estamos esperando la confirmación de tu pago. Si ya pagaste, escribe *Pagado*.")
        return {"ok": True, "respuestas": respuestas}

    # ══════════════════════════════════════════════════════════════════
    #   COMPLETADO / POR DEFECTO
    # ══════════════════════════════════════════════════════════════════
    if tutela.estado == "completado":
        _r(respuestas, telefono, "✅ *Tutela completada.*\n\nSi necesitas ayuda con otra tutela, escribe *Hola*.")
        return {"ok": True, "respuestas": respuestas}

    _r(respuestas, telefono, MENU_DEFAULT)
    return {"ok": True, "respuestas": respuestas}


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _r(respuestas: list[str], telefono: str, mensaje: str) -> None:
    enviar_texto(telefono, mensaje)
    respuestas.append(mensaje)


def _b(respuestas: list[str], telefono: str, texto: str, botones: list[tuple[str, str]]) -> None:
    enviar_botones(telefono, texto, botones)
    respuestas.append(f"[BOTONES] {texto} | {botones}")


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


async def _descargar_prueba(url: str) -> str | None:
    if not url:
        return None
    try:
        ext = ".jpg"
        for e in (".png", ".jpeg", ".pdf", ".jpg", ".gif", ".webp", ".ogg", ".mp3", ".mp4"):
            if e in url.lower():
                ext = e
                break
        ruta = path_prueba(ext)

        headers = {}
        auth = None

        # Meta media ID (solo números) → resolver URL via Graph API
        if url.isdigit() and settings.meta_access_token:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(
                    f"https://graph.facebook.com/v22.0/{url}",
                    headers={"Authorization": f"Bearer {settings.meta_access_token}"},
                )
            if r.status_code == 200:
                url = r.json().get("url", url)
            # La URL firmada de Meta NO necesita header de auth
            headers = {}

        if "api.twilio.com" in url and settings.twilio_account_sid:
            auth = httpx.BasicAuth(settings.twilio_account_sid, settings.twilio_auth_token)

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.get(url, headers=headers, auth=auth)
        if r.status_code == 200:
            with open(ruta, "wb") as f:
                f.write(r.content)
            return ruta
    except Exception as e:
        logger.error(f"Error descargando prueba: {e}")
    return None


async def _generar_con_verificacion(session, tutela, datos: dict, telefono: str, respuestas: list[str]) -> str | None:
    contenido = await generar_tutela(datos) or datos.get("hechos", "")

    citas = await extraer_citas(contenido)
    if citas:
        resultado = verificar_citas(citas, session)
        if resultado["pendientes_revision"]:
            tutela.estado_verificacion = "verificada_con_pendientes"
            guardar_pendientes(tutela.id, resultado["pendientes_revision"], session)
            contenido = limpiar_texto_para_pdf(contenido, resultado["pendientes_revision"])
        else:
            tutela.estado_verificacion = "verificada"

    ruta_pdf = generar_pdf(datos, None)
    tutela.pdf_path = ruta_pdf
    tutela.datos_json = json.dumps(datos)
    tutela.estado = "pdf_generado"
    session.commit()

    _r(respuestas, telefono, "✅ *¡Tutela generada!*")
    ok = enviar_documento(telefono, ruta_pdf, f"tutela_{tutela.id}.pdf")
    if not ok:
        _r(respuestas, telefono, "⚠️ No pude enviar el PDF. Intenta de nuevo.")
    _b(respuestas, telefono, POST_PDF_OPCIONES, [("1", "💳 Radicación $20k"), ("2", "📹 Video gratis")])
    tutela.estado = "esperando_decision_radicacion"
    session.commit()
    return ruta_pdf


# ═══════════════════════════════════════════════════════════════════
# TEXTOS
# ═══════════════════════════════════════════════════════════════════

CONSENTIMIENTO_VERSION = "v1.0"

BIENVENIDA = (
    "👋 *Hola! Soy el asistente de Tutelas Online AI.*\n\n"
    "Te ayudo a crear y radicar acciones de tutela en Colombia "
    "de forma rápida y sencilla.\n\n"
    "Comencemos con la autorización de datos."
)

AVISO_PRIVACIDAD = (
    "📄 *Aviso de Tratamiento de Datos Personales*\n\n"
    "De acuerdo con la Ley 1581 de 2012 y el Decreto 1377 de 2013, "
    "te informamos que:\n\n"
    "🔹 *Responsable:* Tutelas Online AI\n"
    "🔹 *Finalidad:* Gestionar, crear y radicar tu acción de tutela "
    "ante la Rama Judicial\n"
    "🔹 *Datos recolectados:* Nombre, documento, teléfono, correo, "
    "ciudad, historia clínica y demás información relevante para tu tutela\n"
    "🔹 *Derechos del titular:* Acceder, actualizar, rectificar y "
    "solicitar la eliminación de tus datos en cualquier momento "
    "escribiendo *Eliminar mis datos*\n"
    "🔹 *Política completa:* "
    "https://tutela-online-production.up.railway.app/privacidad\n\n"
    "Al aceptar, autorizas el tratamiento de tus datos personales "
    "para los fines descritos."
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
    "1️⃣ *Radicación automática* — *$20.000 COP*\n"
    "   Radicamos por ti ante la Rama Judicial.\n"
    "   Entrega en máximo *4 horas hábiles*.\n"
    "   Recibes número de radicado y constancia oficial.\n\n"
    "2️⃣ *Hazlo tú mismo* — GRATIS\n"
    "   Te enviamos un video explicativo."
)

CONFIRMAR_PAGO_TEXTO = (
    "💳 *Radicación automática*\n\n"
    "Por *$20.000 COP* radicamos tu tutela ante la Rama Judicial.\n"
    "Incluye:\n"
    "✅ Radicación en el portal oficial\n"
    "✅ Seguimiento del proceso\n"
    "✅ Número de radicado y constancia\n"
    "✅ Entrega en máximo 4 horas hábiles\n\n"
    "¿Quieres continuar con el pago?"
)

VIDEO_GUIA = (
    "🎥 *Video guía para radicar tu tutela*\n\n"
    "Mira este video paso a paso:\n"
    "https://youtu.be/ejemplo-tutela-rama-judicial\n\n"
    "📌 *Importante:*\n"
    "• Ten a mano el PDF de la tutela\n"
    "• Tus documentos personales\n"
    "• Correo electrónico\n\n"
    "Si en cualquier momento te parece complicado, "
    "recuerda que por solo *$20.000 COP* nosotros lo hacemos por ti "
    "en máximo 4 horas hábiles.\n"
    "Solo responde *Pagar* para iniciar el proceso."
)

MENU_DEFAULT = (
    "🤖 *Asistente Tutelas Online*\n\n"
    "Comandos:\n"
    "• *Hola* — iniciar o continuar\n"
    "• *Eliminar mis datos* — borrar tu información"
)

# Orden de recolección de datos personales: (campo, mensaje)
DATOS_PERSONALES_STEPS = [
    ("accionante_nombre", "👤 Escribe tu *nombre completo*:"),
    ("accionante_tipo_doc", "🪪 Tipo de documento (CC, CE, Pasaporte):"),
    ("accionante_cedula", "🆔 Número de documento (sin puntos):"),
    ("accionante_telefono", "📱 Teléfono celular:"),
    ("accionante_email", "📧 Correo electrónico (para notificaciones del juzgado):"),
    ("ciudad", "🏙️ ¿En qué ciudad vives?:"),
]
