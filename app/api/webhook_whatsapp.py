import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.config import settings
from app.database import get_session
from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import MensajeWhatsApp
import httpx

from app.services.documento_service import generar_pdf
from app.services.ia_service import (
    CAMPOS_TUTELA,
    MENSAJES_CAMPOS,
    analizar_imagen,
    campos_faltantes,
    extraer_citas,
    extraer_datos,
    generar_tutela,
    transcribir_audio,
)
from app.services.radicacion_service import iniciar_radicacion
from app.services.verificacion_service import (
    guardar_pendientes,
    limpiar_texto_para_pdf,
    verificar_citas,
)
from app.services.whatsapp_service import enviar_documento, enviar_texto
from app.utils.file_utils import path_prueba

router = APIRouter()


@router.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request, session=Depends(get_session)):
    form = await request.form()
    telefono = form.get("From", "")
    body = (form.get("Body", "") or "").strip().lower()
    num_media = int(form.get("NumMedia", "0"))
    media_url = form.get("MediaUrl0")
    es_audio = "audio" in str(form.get("MediaContentType0", ""))
    return await procesar_mensaje(session, telefono, body, num_media, media_url, es_audio)


@router.get("/webhook/meta")
async def verificar_webhook_meta(request: Request):
    modo = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if modo == "subscribe" and token == settings.meta_verify_token:
        return int(challenge)
    return {"error": "Verification failed"}


@router.post("/webhook/meta")
async def webhook_meta(request: Request, session=Depends(get_session)):
    data = await request.json()
    entry = data.get("entry", [])
    for e in entry:
        changes = e.get("changes", [])
        for c in changes:
            value = c.get("value", {})
            messages = value.get("messages", [])
            for msg in messages:
                telefono = msg.get("from", "")
                msg_type = msg.get("type", "")
                body = ""
                num_media = 0
                media_url = ""
                es_audio = False

                if msg_type == "text":
                    body = msg.get("text", {}).get("body", "").strip().lower()
                elif msg_type in ("image", "document"):
                    num_media = 1
                    media_url = msg.get(msg_type, {}).get("link", "") or msg.get(msg_type, {}).get("id", "")
                    es_audio = False
                elif msg_type == "audio":
                    es_audio = True
                    media_url = msg.get("audio", {}).get("id", "")

                return await procesar_mensaje(session, telefono, body, num_media, media_url, es_audio)
    return {"ok": True}


@router.post("/webhook/zapi")
async def webhook_zapi(request: Request, session=Depends(get_session)):
    data = await request.json()
    telefono = data.get("phone", "")
    msg = data.get("message", {}) or {}
    body = (msg.get("text", "") or msg.get("caption", "") or "").strip().lower()
    num_media = 1 if msg.get("type") in ("image", "document", "audio", "video") else 0
    media_url = msg.get("media", "") or msg.get("link", "") or ""
    es_audio = msg.get("type") == "audio"
    return await procesar_mensaje(session, telefono, body, num_media, media_url, es_audio)


async def procesar_mensaje(
    session, telefono: str, body: str, num_media: int, media_url: str, es_audio: bool
) -> dict:
    respuestas: list[str] = []

    msg_orm = MensajeWhatsApp(
        from_number=telefono,
        body=body,
        tipo_mensaje="audio" if es_audio else "texto",
        media_url=media_url,
    )
    session.add(msg_orm)
    session.commit()

    user = session.execute(select(User).where(User.telefono == telefono)).scalar_one_or_none()
    if not user:
        user = User(telefono=telefono, estado="nuevo", consentimiento=False)
        session.add(user)
        session.commit()
        _r(respuestas, telefono, MENSAJE_BIENVENIDA)
        return {"ok": True, "respuestas": respuestas}

    # ─── ELIMINAR DATOS ──────────────────────────────────────────
    if body in ("eliminar", "eliminar mis datos", "borrar", "borrar mis datos"):
        session.query(MensajeWhatsApp).where(MensajeWhatsApp.from_number == telefono).delete()
        for t in session.execute(select(Tutela).where(Tutela.user_id == user.id)).scalars():
            session.delete(t)
        session.delete(user)
        session.commit()
        _r(respuestas, telefono, "🗑️ *Tus datos han sido eliminados.*\n\nSi necesitas ayuda en el futuro, escribe *Hola* y empezamos de nuevo.")
        return {"ok": True, "respuestas": respuestas}

    # ─── FLUJO DE CONSENTIMIENTO ─────────────────────────────────
    if user.estado == "nuevo":
        if body in ("acepto", "sí", "si", "ok", "si acepto"):
            user.consentimiento = True
            user.estado = "activo"
            session.commit()
            # Crear tutela directo con tipo="salud"
            tutela = Tutela(user_id=user.id, tipo="salud", estado="recogiendo_datos")
            datos = {"tipo": "salud", "_step": "narracion"}
            tutela.datos_json = json.dumps(datos)
            session.add(tutela)
            session.commit()
            _r(respuestas, telefono, MENSAJE_NARRACION)
            return {"ok": True, "respuestas": respuestas}
        elif body in ("no", "no acepto", "cancelar"):
            user.estado = "rechazado"
            session.commit()
            _r(respuestas, telefono, "❌ *Has cancelado.*\n\nTus datos no serán guardados. Si cambias de opinión, escribe *Hola*.")
            return {"ok": True, "respuestas": respuestas}
        _r(respuestas, telefono, "✍️ Responde *Acepto* para continuar o *No* para cancelar.")
        return {"ok": True, "respuestas": respuestas}

    if user.estado == "rechazado" and body in ("hola", "menú", "menu", "inicio", "acepto"):
        user.consentimiento = True
        user.estado = "activo"
        session.commit()
        _r(respuestas, telefono, "✅ *Gracias por aceptar.*\n\nTus datos serán usados solo para tu tutela.")
        tutela = Tutela(user_id=user.id, tipo="salud", estado="recogiendo_datos")
        datos = {"tipo": "salud", "_step": "narracion"}
        tutela.datos_json = json.dumps(datos)
        session.add(tutela)
        session.commit()
        _r(respuestas, telefono, MENSAJE_NARRACION)
        return {"ok": True, "respuestas": respuestas}

    if user.estado == "rechazado":
        _r(respuestas, telefono, "❌ No puedes usar el servicio sin aceptar el tratamiento de datos.\n\nSi cambias de opinión, escribe *Acepto*.")
        return {"ok": True, "respuestas": respuestas}

    if body in ("hola", "menú", "menu", "inicio"):
        # Si ya tiene una tutela activa, regresar a ella
        _r(respuestas, telefono, MENSAJE_NARRACION)
        session.commit()
        return {"ok": True, "respuestas": respuestas}

    tutela = session.execute(
        select(Tutela).where(
            Tutela.user_id == user.id,
            Tutela.estado.in_(["borrador", "recogiendo_datos", "datos_listos",
                               "juramento_pendiente", "confirmada", "pdf_generado",
                               "esperando_confirmacion", "verificando_citas",
                               "esperando_decision_radicacion", "esperando_pago"]),
        ).order_by(Tutela.created_at.desc()).limit(1)
    ).scalar_one_or_none()

    if not tutela:
        tutela = Tutela(user_id=user.id, tipo="salud", estado="recogiendo_datos")
        datos = {"tipo": "salud", "_step": "narracion"}
        tutela.datos_json = json.dumps(datos)
        session.add(tutela)
        session.commit()

    if not msg_orm.tutela_id:
        msg_orm.tutela_id = tutela.id
        session.commit()

    datos = json.loads(tutela.datos_json) if tutela.datos_json else {}

    # ─── FLUJO PRINCIPAL ─────────────────────────────────────────

    # 1. SELECCIONAR TIPO (eliminado — solo salud)
    if tutela.estado == "borrador":
        tutela.tipo = "salud"
        datos["tipo"] = "salud"
        tutela.estado = "recogiendo_datos"
        datos["_step"] = "narracion"
        tutela.datos_json = json.dumps(datos)
        session.commit()
        _r(respuestas, telefono, MENSAJE_NARRACION)
        return {"ok": True, "respuestas": respuestas}

    # 2. RECOGER DATOS PASO A PASO
    if tutela.estado == "recogiendo_datos":
        paso = datos.get("_step", 0)
        pendientes = datos.get("_pendientes")

        # Confirmar transcripción de audio
        if paso == "audio_confirmar":
            texto_audio = datos.get("_audio_temp", "")
            if body in ("1", "sí", "si", "correcto", "si correcto"):
                del datos["_audio_temp"]
                # Procesar la transcripción confirmada
                datos_ia = await extraer_datos(texto_audio)
                for k, v in datos_ia.items():
                    if v and k not in ("tipo",):
                        datos[k] = v
                faltan = campos_faltantes(datos)
                if faltan:
                    datos["_step"] = "recogiendo"
                    datos["_pendientes"] = faltan
                    sig = faltan[0]
                    _r(respuestas, telefono, MENSAJES_CAMPOS.get(sig, f"Por favor, indica tu *{sig.replace('_', ' ')}*:"))
                else:
                    datos["_step"] = "completo"
                    _mostrar_resumen(respuestas, telefono, datos)
                tutela.datos_json = json.dumps(datos)
                session.commit()
                return {"ok": True, "respuestas": respuestas}
            elif body in ("2", "no"):
                del datos["_audio_temp"]
                datos["_step"] = "narracion"
                tutela.datos_json = json.dumps(datos)
                session.commit()
                _r(respuestas, telefono, "✍️ *Escribe tu caso manualmente*\n\nCuéntame qué pasó, incluye todos los detalles.")
                return {"ok": True, "respuestas": respuestas}
            _r(respuestas, telefono, "Responde *1* si es correcto o *2* para escribirlo manualmente.")
            session.commit()
            return {"ok": True, "respuestas": respuestas}

        # Si hay campos pendientes, preguntarlos uno por uno
        if pendientes:
            campo = pendientes[0]
            error_validacion = _validar_campo(campo, body)
            if error_validacion:
                _r(respuestas, telefono, f"⚠️ {error_validacion}\n\n{MENSAJES_CAMPOS.get(campo, 'Intenta de nuevo:')}")
                session.commit()
                return {"ok": True, "respuestas": respuestas}
            datos[campo] = body
            pendientes.pop(0)
            if pendientes:
                sig = pendientes[0]
                _r(respuestas, telefono, MENSAJES_CAMPOS.get(sig, f"Indica tu *{sig.replace('_', ' ')}*:"))
            else:
                del datos["_pendientes"]
                datos["_step"] = "completo"
                _mostrar_resumen(respuestas, telefono, datos)
            tutela.datos_json = json.dumps(datos)
            session.commit()
            return {"ok": True, "respuestas": respuestas}

# Procesar narración con IA
        if paso == "narracion":
            # Si es audio, transcribir y pedir confirmación
            if es_audio and media_url:
                ruta_audio = await _descargar_prueba(media_url)
                if ruta_audio:
                    texto_audio = await transcribir_audio(ruta_audio)
                else:
                    texto_audio = None
                if texto_audio:
                    datos["_audio_temp"] = texto_audio
                    datos["_step"] = "audio_confirmar"
                    tutela.datos_json = json.dumps(datos)
                    session.commit()
                    _r(respuestas, telefono,
                       "🎤 *Transcripción de tu audio:*\n\n"
                       f"\"{texto_audio[:500]}\"\n\n"
                       "¿Esto es correcto?\n\n"
                       "1️⃣ *Sí, correcto*\n2️⃣ *No, lo escribiré*")
                    return {"ok": True, "respuestas": respuestas}
            # Texto normal: procesar directo
            datos_ia = await extraer_datos(body)
            for k, v in datos_ia.items():
                if v and k not in ("tipo",):
                    datos[k] = v
            faltan = campos_faltantes(datos)
            if faltan:
                datos["_step"] = "recogiendo"
                datos["_pendientes"] = faltan
                sig = faltan[0]
                _r(respuestas, telefono, MENSAJES_CAMPOS.get(sig, f"Por favor, indica tu *{sig.replace('_', ' ')}*:"))
            else:
                datos["_step"] = "completo"
                _mostrar_resumen(respuestas, telefono, datos)
            tutela.datos_json = json.dumps(datos)
            session.commit()
            return {"ok": True, "respuestas": respuestas}

        # Si ya está completo: confirmar o mostrar resumen
        if datos.get("_step") == "completo" or not campos_faltantes(datos):
            datos["_step"] = "completo"
            if body in ("sí", "si", "yes", "ok", "correcto"):
                tutela.estado = "datos_listos"
                _r(respuestas, telefono, "⚖️ *JURAMENTO*\n\n"
                   "¿Afirmas bajo la gravedad de juramento que *no has interpuesto otra tutela* "
                   "por los mismos hechos ante otro juez?\n\n"
                   "1️⃣ *Sí, juro*\n2️⃣ *No*")
                tutela.datos_json = json.dumps(datos)
                session.commit()
                return {"ok": True, "respuestas": respuestas}
            _mostrar_resumen(respuestas, telefono, datos)
            session.commit()
            return {"ok": True, "respuestas": respuestas}

        _r(respuestas, telefono, MENSAJE_MENU)
        session.commit()
        return {"ok": True, "respuestas": respuestas}

    # 3. JURAMENTO (ya se mostró el resumen, solo espera "Sí, juro")
    if tutela.estado == "datos_listos":
        if body in ("1", "sí, juro", "si, juro", "juro", "sí juro", "si juro"):
            _r(respuestas, telefono, MENSAJE_PRUEBAS)
            tutela.estado = "confirmada"
            datos["juramento"] = "prestado"
            tutela.datos_json = json.dumps(datos)
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        _r(respuestas, telefono, "⚖️ Para continuar, debes confirmar bajo juramento:\n\n"
           "¿Afirmas que *no has presentado otra tutela* por los mismos hechos?\n\n"
           "1️⃣ *Sí, juro*\n2️⃣ *No*")
        session.commit()
        return {"ok": True, "respuestas": respuestas}

    if tutela.estado == "juramento_pendiente":
        if body in ("1", "sí, juro", "si, juro", "juro", "sí", "si"):
            _r(respuestas, telefono, MENSAJE_PRUEBAS)
            tutela.estado = "confirmada"
            datos["juramento"] = "prestado"
            tutela.datos_json = json.dumps(datos)
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        _r(respuestas, telefono, "Sin el juramento no podemos radicar la tutela. ¿Confirmas que *no has presentado otra tutela* por los mismos hechos?\n\n1️⃣ *Sí, juro*\n2️⃣ *No*")
        session.commit()
        return {"ok": True, "respuestas": respuestas}

    # 4. RECIBIR PRUEBAS (fotos/documentos)
    if tutela.estado == "confirmada":
        # Si no envía archivos y escribe "continuar", saltar las pruebas
        if num_media == 0 and body in ("continuar", "no tengo", "no", "saltar", "omitir"):
            await _generar_con_verificacion(session, tutela, datos, telefono, respuestas)
            return {"ok": True, "respuestas": respuestas}

        if num_media > 0 and media_url:
            ruta_local = await _descargar_prueba(media_url)
            if ruta_local:
                datos.setdefault("pruebas_paths", []).append(ruta_local)
            else:
                datos.setdefault("pruebas_urls", []).append(media_url)

            analisis = await analizar_imagen(media_url)
            if analisis:
                datos.setdefault("pruebas_analizadas", []).append(analisis)

            tutela.datos_json = json.dumps(datos)

            await _generar_con_verificacion(session, tutela, datos, telefono, respuestas)
            return {"ok": True, "respuestas": respuestas}

        if tutela.estado == "confirmada":
            _r(respuestas, telefono, MENSAJE_PRUEBAS)
            session.commit()
            return {"ok": True, "respuestas": respuestas}

    # 5. DESPUES DEL PDF: DECISION RADICACION
    if tutela.estado == "esperando_decision_radicacion":
        if body in ("1", "pagar", "pago", "si radicar"):
            link_pago = f"{settings.app_url}/pago/{tutela.id}"
            _r(respuestas, telefono,
               f"💰 *Radicacion automatica*\n\n"
               f"Haz clic en el link para pagar *$20.000 COP* via Nequi:\n\n"
               f"{link_pago}\n\n"
               f"Una vez confirmado el pago, radicaremos tu tutela "
               f"en maximo *4 horas habiles* (lun-vie 8am-5pm).\n\n"
               f"Te notificaremos cuando este radicada con el numero "
               f"de radicado y la constancia.")
            tutela.estado = "esperando_pago"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        elif body in ("2", "video", "gratis", "hacer yo mismo"):
            _r(respuestas, telefono, MENSAJE_VIDEO_GUIA)
            tutela.estado = "pdf_generado"
            session.commit()
            return {"ok": True, "respuestas": respuestas}
        _r(respuestas, telefono, MENSAJE_POST_PDF)
        session.commit()
        return {"ok": True, "respuestas": respuestas}

    # 5b. ESPERANDO PAGO (simulado)
    if tutela.estado == "esperando_pago":
        if body in ("pagado", "pago confirmado", "si", "ok", "1"):
            _r(respuestas, telefono, "✅ *Pago recibido!*\n\n"
               "Radicaremos tu tutela en maximo *4 horas habiles* "
               "(lun-vie 8am-5pm). Te avisaremos cuando este lista.")
            resultado = await iniciar_radicacion(tutela.id)
            if resultado.get("ok"):
                _r(respuestas, telefono,
                   f"✅ *¡Tutela radicada!*\nN° radicado: {resultado.get('num_radicado', 'N/A')}")
            else:
                _r(respuestas, telefono,
                   f"⚠️ Error en radicación: {resultado.get('error', 'desconocido')}")
            return {"ok": True, "respuestas": respuestas}
        _r(respuestas, telefono, "Espera a que confirmemos tu pago y radicaremos tu tutela.")
        session.commit()
        return {"ok": True, "respuestas": respuestas}

    # 6. ESPERANDO CONFIRMACIÓN (legacy - para tutelas viejas)
    if tutela.estado == "esperando_confirmacion":
        if body in ("1", "sí", "si", "radicar"):
            _r(respuestas, telefono, "⏳ *Radicando tu tutela...*")
            session.commit()
            resultado = await iniciar_radicacion(tutela.id)
            if resultado.get("ok"):
                _r(respuestas, telefono,
                   f"✅ *¡Tutela radicada!*\nN° radicado: {resultado.get('num_radicado', 'N/A')}")
            else:
                _r(respuestas, telefono,
                   f"⚠️ Error en radicación: {resultado.get('error', 'desconocido')}")
            return {"ok": True, "respuestas": respuestas}
        else:
            _r(respuestas, telefono, "La tutela quedó guardada. Cuando quieras radicarla, responde *Radicar*")
            return {"ok": True, "respuestas": respuestas}

    # 6. MENSAJE POR DEFECTO
    if tutela.estado in ("confirmada", "pdf_generado"):
        _r(respuestas, telefono, MENSAJE_PRUEBAS)
        session.commit()
        return {"ok": True, "respuestas": respuestas}

    _r(respuestas, telefono, MENSAJE_MENU)
    session.commit()
    return {"ok": True, "respuestas": respuestas}


# ─── HELPERS ──────────────────────────────────────────────────────

def _r(respuestas: list[str], telefono: str, mensaje: str) -> None:
    enviar_texto(telefono, mensaje)
    respuestas.append(mensaje)


def _siguiente_campo(datos: dict) -> str | None:
    """Retorna el siguiente campo obligatorio que falta."""
    for c in CAMPOS_TUTELA:
        if c in ("tipo", "_step", "_pendientes", "derechos_vulnerados", "peticion"):
            continue
        if not datos.get(c):
            return c
    return None


def _mostrar_resumen(respuestas: list[str], telefono: str, datos: dict) -> None:
    if datos.get("_step") == "completo" or not campos_faltantes(datos):
        datos["_step"] = "completo"
        resumen = (
            "📋 *Resumen de tu tutela*\n\n"
            f"👤 *Accionante:* {datos.get('accionante_nombre', '_____')}\n"
            f"🆔 *Documento:* {datos.get('accionante_tipo_doc', 'CC')} {datos.get('accionante_cedula', '_____')}\n"
            f"📧 *Email:* {datos.get('accionante_email', '_____')}\n"
            f"🏙️ *Ciudad:* {datos.get('ciudad', '_____')}\n"
            f"🏛️ *Accionado:* {datos.get('accionado', '_____')}\n\n"
            f"📝 *Hechos:* {datos.get('hechos', '_____')[:200]}...\n\n"
            f"¿Es correcto? Responde *SÍ* para continuar o corrige escribiendo el dato incorrecto."
        )
        _r(respuestas, telefono, resumen)


def _validar_campo(campo: str, valor: str) -> str | None:
    """Valida un campo. Retorna mensaje de error si es inválido, None si es válido."""
    if campo == "accionante_cedula":
        if not valor.isdigit():
            return "La cédula debe contener solo números (sin puntos ni letras)."
    if campo == "accionante_telefono":
        limpio = valor.replace("+", "").replace(" ", "").replace("-", "")
        if not limpio.isdigit() or len(limpio) < 10:
            return "El celular debe tener al menos 10 dígitos numéricos (ej: 3123456789)."
    if campo == "accionante_email":
        if "@" not in valor or "." not in valor:
            return "El correo debe tener un formato válido (ej: nombre@correo.com)."
    return None


async def _descargar_prueba(url: str) -> str | None:
    """Descarga un archivo de prueba (foto/documento/audio) y lo guarda localmente."""
    try:
        ext = ".jpg"
        for e in (".png", ".jpeg", ".pdf", ".jpg", ".gif", ".webp", ".ogg", ".mp3", ".mp4"):
            if e in url.lower():
                ext = e
                break
        ruta = path_prueba(ext)
        async with httpx.AsyncClient(timeout=30) as c:
            kwargs = {}
            if "api.twilio.com" in url and settings.twilio_account_sid:
                kwargs["auth"] = httpx.BasicAuth(settings.twilio_account_sid, settings.twilio_auth_token)
            r = await c.get(url, **kwargs)
        if r.status_code == 200:
            with open(ruta, "wb") as f:
                f.write(r.content)
            return ruta
    except Exception:
        pass
    return None


async def _generar_con_verificacion(session, tutela, datos: dict, telefono: str, respuestas: list[str]) -> str | None:
    """Genera tutela con verificacion de citas legales, luego crea PDF."""
    contenido = await generar_tutela(datos) or datos.get("hechos", "")

    # Extraer y verificar citas
    citas = await extraer_citas(contenido)
    if citas:
        resultado = verificar_citas(citas, session)
        if resultado["pendientes_revision"]:
            tutela.estado_verificacion = "verificada_con_pendientes"
            guardar_pendientes(tutela.id, resultado["pendientes_revision"], session)
            contenido = limpiar_texto_para_pdf(contenido, resultado["pendientes_revision"])
        else:
            tutela.estado_verificacion = "verificada"

    ruta_pdf = generar_pdf(datos, contenido)
    tutela.pdf_path = ruta_pdf
    tutela.estado = "pdf_generado"
    tutela.datos_json = json.dumps(datos)
    session.commit()

    _r(respuestas, telefono, "✅ *¡Tutela lista!*")
    enviar_documento(telefono, ruta_pdf, f"tutela_{tutela.id}.pdf")
    _r(respuestas, telefono, MENSAJE_POST_PDF)
    tutela.estado = "esperando_decision_radicacion"
    session.commit()
    return ruta_pdf


MENSAJE_BIENVENIDA = (
    "👋 *Hola, soy el asistente de Tutelas Online AI!*\n\n"
    "Por ahora te ayudo especificamente con casos de *salud*:\n"
    "negacion de tratamientos, citas medicas o medicamentos por tu EPS.\n\n"
    "📢 *Aviso de privacidad:*\n"
    "Para ayudarte necesito tratar tus datos personales y de salud "
    "(nombre, cedula, historia clinica, diagnosticos). "
    "Estos datos se usan solo para generar y radicar tu tutela. "
    "Puedes solicitar su eliminacion en cualquier momento "
    "escribiendo *Eliminar mis datos*.\n\n"
    "✍️ Responde *Acepto* para continuar o *No* para cancelar.\n\n"
    "🎤 Tambien puedes enviar *audios* contando tu caso."
)

MENSAJE_NARRACION = (
    "✍️ *Cuentame tu caso de salud en detalle*\n\n"
    "Incluye:\n"
    "- Que EPS te nego el servicio\n"
    "- Que tratamiento, cita o medicamento te negaron\n"
    "- Fechas de las negaciones\n"
    "- Tus datos personales (nombre, cedula, ciudad)\n\n"
    "🎤 Puedes enviar un *audio* contando tu caso.\n"
    "🗑️ *Eliminar mis datos* — borra tu info del sistema"
)

MENSAJE_PRUEBAS = (
    "📸 *Envía tus soportes (opcional)*\n\n"
    "Si tienes fotos de *fórmulas, resultados médicos, respuestas de la EPS, "
    "comparendos, pantallazos u otros documentos* que apoyen tu caso, "
    "envíalas ahora.\n\n"
    "El sistema las analizará y las incluirá como pruebas.\n\n"
    "Si no tienes soportes, escribe *Continuar* para generar la tutela."
)

MENSAJE_POST_PDF = (
    "📄 *PDF generado y enviado*\n\n"
    "Ahora tienes 2 opciones:\n\n"
    "1️⃣ *Radicacion automatica* — *$20.000 COP*\n"
    "   Nosotros radicamos por ti ante la Rama Judicial.\n"
    "   Entrega en maximo *4 horas habiles* (lun-vie 8am-5pm).\n"
    "   Recibes numero de radicado y constancia oficial.\n\n"
    "2️⃣ *Hazlo tu mismo* — GRATIS\n"
    "   Te enviamos un video explicativo.\n"
    "   Debes ingresar los datos manualmente en el portal.\n\n"
    "Responde *1* para pagar y radicar, o *2* para el video gratis."
)

MENSAJE_VIDEO_GUIA = (
    "🎥 *Video guia para radicar tu tutela*\n\n"
    "Mira este video paso a paso:\n"
    "https://youtu.be/ejemplo-tutela-rama-judicial\n\n"
    "📌 *Importante:*\n"
    "Debes tener a mano:\n"
    "- El PDF de la tutela que te enviamos\n"
    "- Tus documentos personales\n"
    "- Correo electronico\n\n"
    "Si en algun momento te parece complicado, "
    "recuerda que por solo *$20.000 COP* nosotros lo hacemos por ti "
    "en maximo 4 horas habiles.\n"
    "Solo responde *Pagar* para iniciar el proceso."
)
