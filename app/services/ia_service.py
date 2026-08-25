import asyncio
import base64
import json
import logging

import aiofiles
import httpx
from anyio import Path
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# Base URL compatible con OpenAI del SDK nativo de Gemini (para transcribir audio)
_GEMINI_SDK = "google-genai"


def _get_client() -> AsyncOpenAI | None:
    api_key = settings.ai_api_key
    if not api_key:
        return None

    kwargs = {"api_key": api_key}
    if settings.ai_provider == "groq":
        kwargs["base_url"] = "https://api.groq.com/openai/v1"
    elif settings.ai_provider == "openai":
        kwargs["base_url"] = "https://api.openai.com/v1"
    elif settings.ai_provider == "gemini":
        # Endpoint compatible con OpenAI expuesto por Google para Gemini
        kwargs["base_url"] = "https://generativelanguage.googleapis.com/v1beta/openai/"

    return AsyncOpenAI(**kwargs)


CAMPOS_TUTELA = [
    "tipo", "accionante_nombre", "accionante_tipo_doc", "accionante_cedula",
    "accionante_telefono", "accionante_email", "ciudad", "departamento",
    "accionado", "accionado_tipo", "accionado_nit", "accionado_email",
    "hechos", "derechos_vulnerados", "peticion",
]

SISTEMA_EXTRACCION_CASO = """Eres un asistente legal colombiano especializado en acciones de tutela de salud.

Tarea: Extrae información estructurada del relato del usuario para generar una tutela completa.

Campos a extraer (JSON):
- accionado: nombre de la EPS, institución o persona (obligatorio)
- accionado_tipo: "natural" o "juridica" (obligatorio)
- accionado_nit: NIT de la entidad (si no se conoce: "")
- accionado_email: correo de notificación (si no se conoce: "")
- hechos: cronología detallada con FECHAS EXACTAS, gestiones previas (derechos de petición, quejas), y el problema concreto. Usa formato: "1. [fecha] - [acción]; 2. [fecha] - [acción]"
- derechos_vulnerados: lista de artículos específicos: Art. 11 CP, Art. 48 CP, Art. 49 CP, Art. 86 CP, Art. 2 CP
- peticion: solicitud concreta y específica al juez (ej: "ordenar a EPS X que autorice cita con medicina general en 48 horas")
- genero: "masculino" o "femenino" según el nombre del accionante

Reglas:
- Los datos personales ya fueron recolectados: NO los extraigas
- Usa fechas exactas cuando las menciones (ej: "15 de enero de 2026" → "15/01/2026")
- Si el usuario menciona "hace 3 días", NO inventes fechas: usa texto descriptivo
- Lista las gestiones previas: si pidió cita, si presentó queja, reclamó por correo, etc.
- Para derechos: menciona Art. 11 (vida), Art. 49 (salud), Art. 48 (seguro social), Art. 86 (tutela), Art. 2 (fines del Estado)
- Si el usuario no menciona la EPS específica, usa "la entidad"

Respuesta SOLO JSON, sin explicaciones."""

# Prompt de sistema anti-alucinación: estructura rígida I-XI y marcadores [FALTA: ...]
SISTEMA_TUTELA = """Eres un asistente especializado en redactar acciones de tutela conforme al
ordenamiento jurídico colombiano (Artículo 86 de la Constitución Política
y Decreto 2591 de 1991). Con la información que te entregue el usuario,
genera el escrito siguiendo esta estructura exacta, en este orden, con
numeración romana I a XI sin saltos:

I. ENCABEZADO: dirigido al juez competente (reparto), ciudad completa y
   fecha en español (día, mes en letras, año — nunca mezclar idiomas).
II. ACCIONANTE: nombres completos, cédula, dirección, teléfono, correo —
    solo con los datos que el usuario proporcionó.
III. ACCIONADO: entidad o persona, con los datos disponibles.
IV. HECHOS: narración cronológica, numerada, clara y verificable. Usa
    ÚNICAMENTE los hechos que el usuario relató. Si falta una fecha, un
    nombre o un dato clave, usa un marcador explícito como
    [FALTA: fecha de la negativa] en vez de inventarlo.
V. DERECHOS FUNDAMENTALES VULNERADOS: identifica el o los derechos
   concretos. Cada derecho debe ir con 1-2 frases que lo conecten
   directamente con los hechos narrados — nunca solo el artículo
   constitucional sin explicación.
VI. FUNDAMENTOS DE PROCEDIBILIDAD: explica por qué procede la tutela
    (subsidiariedad e inmediatez). Si el derecho vulnerado es la salud,
    menciona que es un derecho fundamental autónomo (Ley Estatutaria
    1751 de 2015), sin necesidad de demostrar conexidad con la vida.
VII. MEDIDA PROVISIONAL: si los hechos muestran urgencia (el accionante
     ya asumió gastos propios, hay riesgo de agravamiento, o se
     interrumpió un tratamiento en curso), solicita explícitamente una
     medida provisional mientras se decide el fondo.
VIII. PRETENSIONES: numeradas (PRIMERO, SEGUNDO...), concretas y
      ejecutables. Si el accionante ya pagó de su bolsillo algo que
      debía cubrir la entidad, incluye una pretensión de reintegro de
      esos gastos.
IX. PRUEBAS: lista solo lo que el usuario haya mencionado tener (fórmula
    médica, respuesta de la entidad, comprobantes de pago, capturas de
    pantalla). Si no mencionó pruebas, usa [FALTA: pruebas documentales].
X. JURAMENTO: "Manifiesto bajo la gravedad de juramento que no he
   interpuesto otra acción de tutela por los mismos hechos y derechos"
   (Art. 37, Decreto 2591 de 1991).
XI. NOTIFICACIONES: datos de contacto para recibir la respuesta, seguido
    de espacio para firma, nombre y número de cédula.

REGLAS ESTRICTAS:
- Nunca inventes hechos, fechas, nombres, cifras o direcciones de correo
  que el usuario no haya proporcionado exactamente. Usa [FALTA: ...] en
  vez de rellenar con supuestos.
- Si un dato parece inválido (ej. un correo con dominio inexistente),
  no lo corrijas por tu cuenta: repórtalo como [VERIFICAR: dato dudoso].
- Lenguaje formal jurídico pero comprensible, sin adornos innecesarios.
- No emitas opiniones sobre el resultado del caso ni cites jurisprudencia
  que no te haya sido dada como contexto verificado.
- La fecha del encabezado siempre en español, sin mezclar idiomas.
- Responde solo con el texto de la tutela en el formato anterior, sin
  explicaciones adicionales."""


def _transcribir_con_gemini_sync(ruta_audio: str) -> str | None:
    """Llamada síncrona al SDK nativo de Gemini para transcribir audio.
    Se ejecuta en un hilo aparte vía asyncio.to_thread."""
    try:
        from google import genai
        from google.genai import types

        ext = ruta_audio.rsplit(".", 1)[-1].lower() if "." in ruta_audio else "ogg"
        mime_map = {
            "ogg": "audio/ogg", "oga": "audio/ogg", "opus": "audio/ogg",
            "mp3": "audio/mpeg", "m4a": "audio/mp4", "mp4": "audio/mp4",
            "wav": "audio/wav", "webm": "audio/webm", "aac": "audio/aac",
            "amr": "audio/amr",
        }
        mime = mime_map.get(ext, "audio/ogg")

        cliente = genai.Client(api_key=settings.ai_api_key)
        with open(ruta_audio, "rb") as f:
            data = f.read()

        # Alias -latest siempre apunta al modelo flash vigente (los
        # versionados quedan obsoletos: p.ej. gemini-2.0-flash fue retirado).
        modelo = settings.ai_chat_model if "gemini" in settings.ai_chat_model.lower() else "gemini-flash-latest"
        resp = cliente.models.generate_content(
            model=modelo,
            contents=[
                "Transcribe este audio a texto en español colombiano, de forma "
                "literal, sin comentarios ni resúmenes adicionales.",
                types.Part.from_bytes(data=data, mime_type=mime),
            ],
        )
        return (resp.text or "").strip() or None
    except Exception as e:
        logger.error(f"Error transcribiendo audio con Gemini: {e}")
        return None


async def transcribir_audio(ruta_audio: str) -> str | None:
    """Transcribe audio. Con provider=gemini usa el SDK nativo (multimodal);
    con openai/groq usa Whisper."""
    if not settings.ai_api_key:
        return None

    if settings.ai_provider == "gemini":
        return await asyncio.to_thread(_transcribir_con_gemini_sync, ruta_audio)

    client = _get_client()
    if not client:
        return None
    try:
        # Leer los bytes de forma asíncrona y pasarlos al SDK; el cliente
        # async no acepta file objects de aiofiles (solo bytes/PathLike).
        nombre = Path(ruta_audio).name
        async with aiofiles.open(ruta_audio, "rb") as f:
            contenido = await f.read()
        transcript = await client.audio.transcriptions.create(
            model=settings.ai_whisper_model,
            file=(nombre, contenido),
            language="es",
        )
        return transcript.text
    except Exception as e:
        logger.error(f"Error transcribiendo audio: {e}")
        return None


async def extraer_datos_caso(texto: str) -> dict:
    client = _get_client()
    if not client:
        return {}
    resp = await client.chat.completions.create(
        model=settings.ai_chat_model,
        messages=[
            {"role": "system", "content": SISTEMA_EXTRACCION_CASO},
            {"role": "user", "content": texto},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


async def analizar_imagen(url_imagen: str) -> str:
    """Analiza una imagen (prueba/documento) usando visión por IA."""
    client = _get_client()
    if not client:
        return ""
    try:
        if await Path(url_imagen).is_file():
            async with aiofiles.open(url_imagen, "rb") as f:
                img_b64 = base64.b64encode(await f.read()).decode()
        else:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(url_imagen)
            img_b64 = base64.b64encode(r.content).decode()
        resp = await client.chat.completions.create(
            model=settings.ai_chat_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Analiza esta imagen de un documento legal colombiano. "
                                    "Extrae: tipo de documento, número de documento, nombres, "
                                    "fechas, entidad emisora, y cualquier información relevante. "
                                    "Resume en texto plano.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_b64}",
                                "detail": "high",
                            },
                        },
                    ],
                },
            ],
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        logger.error(f"Error en analizar_imagen: {e}")
        return ""


def campos_faltantes(datos: dict) -> list[str]:
    """Retorna lista de campos obligatorios que faltan en datos."""
    obligatorios = [
        "accionante_nombre", "accionante_cedula",
        "accionante_telefono", "accionante_email",
        "ciudad", "accionado", "hechos",
    ]
    return [c for c in obligatorios if not datos.get(c)]


MENSAJES_CAMPOS = {
    "accionante_nombre": "👤 Escribe tu *nombre completo*:",
    "accionante_cedula": "🆔 Escribe tu *número de cédula* (sin puntos):",
    "accionante_telefono": "📱 Escribe tu *teléfono celular*:",
    "accionante_email": "📧 Escribe tu *correo electrónico* (allí recibirás notificaciones del juzgado):",
    "ciudad": "🏙️ ¿En qué *ciudad* ocurrieron los hechos?",
    "departamento": "🗺️ ¿En qué *departamento*?",
    "accionado": "🏛️ ¿Contra qué *entidad o persona* va dirigida la tutela? (Ej: EPS Sanitas, Alcaldía de Medellín)",
    "accionado_tipo": "¿La entidad es *persona natural* o *jurídica*? (Responde: natural / jurídica)",
    "accionado_nit": "🔢 ¿Conoces el *NIT* de la entidad? Si no, escribe *no sé*:",
    "accionado_email": "📧 ¿Cuál es el *correo electrónico* de la entidad para notificaciones? (Si no sabes, escribe *no sé*):",
    "hechos": "✍️ Cuéntame en detalle qué pasó, desde el inicio, con fechas y lugares:",
    "peticion": "¿Qué le pides exactamente al juez que ordene?",
}


async def generar_tutela(datos: dict) -> str | None:
    client = _get_client()
    if not client:
        return None

    accionado = datos.get("accionado", "la entidad")
    accionante = datos.get("accionante_nombre", "el accionante")
    ciudad = datos.get("ciudad", "la ciudad")
    genero = datos.get("genero", "masculino")

    prompt = (
        f"Redacta una acción de tutela formal en formato legal colombiano.\n\n"
        f"GÉNERO DEL ACCIONANTE: {genero} (usa pronombres concordantes)\n\n"
        f"DATOS DEL ACCIONANTE:\n"
        f"Nombre: {accionante}\n"
        f"Documento: {datos.get('accionante_tipo_doc', 'CC')} {datos.get('accionante_cedula', '')}\n"
        f"Teléfono: {datos.get('accionante_telefono', '')}\n"
        f"Email: {datos.get('accionante_email', '')}\n"
        f"Ciudad: {ciudad}, {datos.get('departamento', '')}\n\n"
        f"ACCIONADO:\n"
        f"Nombre: {accionado}\n"
        f"Tipo: {datos.get('accionado_tipo', 'jurídica')}\n"
        f"NIT: {datos.get('accionado_nit', '')}\n"
        f"Email notificación: {datos.get('accionado_email', '')}\n\n"
        f"HECHOS:\n{datos.get('hechos', '')}\n\n"
        f"DERECHOS VULNERADOS: {', '.join(datos.get('derechos_vulnerados', []))}\n\n"
        f"PETICIÓN:\n{datos.get('peticion', '')}\n\n"
        "Recuerda: si falta algún dato (fecha, dirección, NIT, correo), usa el "
        "marcador [FALTA: descripción del dato] en lugar de inventarlo."
    )

    resp = await client.chat.completions.create(
        model=settings.ai_chat_model,
        messages=[
            {"role": "system", "content": SISTEMA_TUTELA},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content


SISTEMA_PREVIEW_TUTELA = """Eres un asistente legal que genera resúmenes claros de acciones de tutela.

Basado en los datos proporcionados, genera un RESUMEN CLIENTE-FRIENDLY que incluya:

1. RESUMEN DE DATOS: Lista los datos recolectados
2. HECHOS EXTRAÍDOS: Cronología clara con fechas
3. DERECHOS IDENTIFICADOS: Artículos de aplicación
4. PETICIÓN PROPUESTA: Qué se solicita al juez en lenguaje claro
5. PRÓXIMOS PASOS: Qué hará el cliente después

Si faltan datos importantes, indica qué falta. Si los hechos son muy breves, sugiere más detalles.

Formato: Texto claro, sin formato legal complejo. Usa viñetas y numeración para legibilidad.

Datos: {datos_json}"""


async def generar_preview(datos: dict) -> str:
    """Genera un resumen amigable para que el cliente revise antes de PDF final."""
    client = _get_client()
    if not client:
        return "No se puede generar preview sin conexión a IA"

    resp = await client.chat.completions.create(
        model=settings.ai_chat_model,
        messages=[
            {"role": "system", "content": SISTEMA_PREVIEW_TUTELA.format(datos_json=json.dumps(datos, ensure_ascii=False, indent=2))},
            {"role": "user", "content": "Genera el preview"},
        ],
    )
    return resp.choices[0].message.content


SISTEMA_EXTRACCION_CITAS = """Eres un extractor de referencias legales. Tu unica tarea es identificar
TODAS las citas a normas, articulos, decretos, leyes o sentencias que
aparecen en el texto de abajo.

No evaluues si son correctas. No agregues citas que no esten en el texto.
No parafrasees el texto legal, solo extrae la referencia tal como aparece.

Responde UNICAMENTE en JSON, con este formato exacto:

{
  "citas": [
    {
      "referencia_textual": "texto exacto como aparece en el documento",
      "tipo": "constitucion" | "decreto" | "ley" | "sentencia" | "otro",
      "contexto": "la frase completa donde aparece la cita"
    }
  ]
}

Si no hay citas, responde {"citas": []}.
"""


async def extraer_citas(texto_tutela: str) -> list[dict]:
    client = _get_client()
    if not client:
        return []
    resp = await client.chat.completions.create(
        model=settings.ai_chat_model,
        messages=[
            {"role": "system", "content": SISTEMA_EXTRACCION_CITAS},
            {"role": "user", "content": texto_tutela},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    data = json.loads(resp.choices[0].message.content)
    return data.get("citas", [])
