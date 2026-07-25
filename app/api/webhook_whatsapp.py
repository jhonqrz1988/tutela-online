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
    extraer_datos,
    generar_tutela,
)
from app.services.radicacion_service import iniciar_radicacion
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


@router.post("/webhook/wati")
async def webhook_wati(request: Request, session=Depends(get_session)):
    data = await request.json()
    telefono = data.get("from", "")
    body = (data.get("text", "") or "").strip().lower()
    msg_type = data.get("type", "text")
    num_media = 1 if msg_type in ("image", "document", "audio", "video") else 0
    media_url = data.get("mediaUrl", "")
    es_audio = msg_type == "audio"
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
        user = User(telefono=telefono, estado="nuevo")
        session.add(user)
        session.commit()
        _r(respuestas, telefono, MENSAJE_BIENVENIDA)
        return {"ok": True, "respuestas": respuestas}

    if body in ("hola", "menú", "menu", "inicio"):
        _r(respuestas, telefono, MENSAJE_MENU)
        session.commit()
        return {"ok": True, "respuestas": respuestas}

    tutela = session.execute(
        select(Tutela).where(
            Tutela.user_id == user.id,
            Tutela.estado.in_(["borrador", "recogiendo_datos", "datos_listos",
                               "juramento_pendiente", "confirmada", "pdf_generado",
                               "esperando_confirmacion"]),
        ).order_by(Tutela.created_at.desc()).limit(1)
    ).scalar_one_or_none()

    if not tutela:
        tutela = Tutela(user_id=user.id, tipo="salud", estado="borrador")
        session.add(tutela)
        session.commit()

    if not msg_orm.tutela_id:
        msg_orm.tutela_id = tutela.id
        session.commit()

    datos = json.loads(tutela.datos_json) if tutela.datos_json else {}

    # ─── FLUJO PRINCIPAL ─────────────────────────────────────────

    # 1. SELECCIONAR TIPO
    if tutela.estado == "borrador":
        if body in ("1", "salud", "2", "fotomultas", "3", "derecho de petición", "derecho_peticion"):
            tipo_map = {"1": "salud", "2": "fotomultas", "3": "derecho_peticion",
                        "salud": "salud", "fotomultas": "fotomultas",
                        "derecho de petición": "derecho_peticion", "derecho_peticion": "derecho_peticion"}
            tutela.tipo = tipo_map.get(body, "salud")
            datos["tipo"] = tutela.tipo
            tutela.datos_json = json.dumps(datos)
            tutela.estado = "recogiendo_datos"
            datos["_step"] = "narracion"
            tutela.datos_json = json.dumps(datos)
            session.commit()
            _r(respuestas, telefono, "✍️ *Cuéntame tu caso en detalle*\n\n"
               "Incluye: qué pasó, fechas, contra qué entidad, tus datos personales si quieres.")
            return {"ok": True, "respuestas": respuestas}
        _r(respuestas, telefono, MENSAJE_MENU)
        session.commit()
        return {"ok": True, "respuestas": respuestas}

    # 2. RECOGER DATOS PASO A PASO
    if tutela.estado == "recogiendo_datos":
        paso = datos.get("_step", 0)
        pendientes = datos.get("_pendientes")

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
    if num_media > 0 and media_url:
        if tutela.estado == "confirmada":
            # Descargar y guardar el archivo localmente
            ruta_local = await _descargar_prueba(media_url)
            if ruta_local:
                datos.setdefault("pruebas_paths", []).append(ruta_local)
            else:
                datos.setdefault("pruebas_urls", []).append(media_url)

            # Analizar imagen con IA (visión)
            analisis = await analizar_imagen(media_url)
            if analisis:
                datos.setdefault("pruebas_analizadas", []).append(analisis)

            tutela.datos_json = json.dumps(datos)

            # Generar tutela y PDF
            contenido = await generar_tutela(datos) or datos.get("hechos", "")
            ruta_pdf = generar_pdf(datos, contenido)
            tutela.pdf_path = ruta_pdf
            tutela.estado = "pdf_generado"
            session.commit()

            _r(respuestas, telefono, "✅ *¡Tutela lista!*")
            enviar_documento(telefono, ruta_pdf, f"tutela_{tutela.id}.pdf")

            _r(respuestas, telefono,
               "📄 *PDF generado y enviado*\n\n"
               "¿Deseas radicar la tutela en la Rama Judicial?\n\n"
               "1️⃣ *Sí, radicar*\n2️⃣ *No, después*")
            tutela.estado = "esperando_confirmacion"
            session.commit()
            return {"ok": True, "respuestas": respuestas}

        if tutela.estado == "confirmada":
            _r(respuestas, telefono, MENSAJE_PRUEBAS)
            session.commit()
            return {"ok": True, "respuestas": respuestas}

    # 5. ESPERANDO CONFIRMACIÓN PARA RADICAR
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
    """Descarga un archivo de prueba (foto/documento) y lo guarda localmente."""
    try:
        ext = ".jpg"
        for e in (".png", ".jpeg", ".pdf", ".jpg", ".gif", ".webp"):
            if e in url.lower():
                ext = e
                break
        ruta = path_prueba(ext)
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(url)
        if r.status_code == 200:
            with open(ruta, "wb") as f:
                f.write(r.content)
            return ruta
    except Exception:
        pass
    return None


MENSAJE_BIENVENIDA = (
    "👋 *¡Hola! Soy tu asistente legal virtual.*\n\n"
    "Te ayudo a radicar una *Acción de Tutela* en Colombia "
    "directamente desde WhatsApp.\n\n"
    "Escribe *MENÚ* para empezar."
)

MENSAJE_MENU = (
    "📋 *¿Cuál es el motivo de tu tutela?*\n\n"
    "1️⃣ *Salud* (EPS negó tratamiento/cita/medicamento)\n"
    "2️⃣ *Fotomultas* (comparendos injustos)\n"
    "3️⃣ *Derecho de Petición* (no respondido)\n\n"
    "Responde con el *número* o el *nombre* del motivo."
)

MENSAJE_PRUEBAS = (
    "📸 *Envía tus soportes*\n\n"
    "Por favor envía en este chat:\n"
    "1️⃣ Foto de tu *cédula* por ambos lados\n"
    "2️⃣ Fotos de *fórmulas, resultados o soportes*\n\n"
    "El sistema analizará automáticamente los documentos."
)
