import base64
import json

import httpx
from openai import AsyncOpenAI

from app.config import settings


def _get_client() -> AsyncOpenAI | None:
    api_key = settings.ai_api_key
    if not api_key:
        return None

    kwargs = {"api_key": api_key}
    if settings.ai_provider == "groq":
        kwargs["base_url"] = "https://api.groq.com/openai/v1"
    elif settings.ai_provider == "openai":
        kwargs["base_url"] = "https://api.openai.com/v1"

    return AsyncOpenAI(**kwargs)


CAMPOS_TUTELA = [
    "tipo", "accionante_nombre", "accionante_tipo_doc", "accionante_cedula",
    "accionante_telefono", "accionante_email", "ciudad", "departamento",
    "accionado", "accionado_tipo", "accionado_nit", "accionado_email",
    "hechos", "derechos_vulnerados", "peticion",
]

SISTEMA_EXTRACCION = """Eres un asistente legal colombiano experto en acciones de tutela.
Extrae los siguientes datos del relato del usuario en formato JSON.

Campos a extraer:
- tipo: tipo de tutela ("salud", "fotomultas", "derecho_peticion", "otro")
- accionante_nombre: nombre completo del accionante
- accionante_tipo_doc: tipo de documento ("CC", "CE", "NIT", "desconocido")
- accionante_cedula: número de documento (sin puntos ni dígito de verificación)
- accionante_telefono: teléfono celular
- accionante_email: correo electrónico
- ciudad: ciudad del accionante
- departamento: departamento del accionante
- accionado: nombre de la entidad o persona contra quien se tutela
- accionado_tipo: "natural" o "juridica"
- accionado_nit: NIT de la entidad (si se conoce, si no: "desconocido")
- accionado_email: correo de notificación de la entidad
- hechos: relato completo y detallado de los hechos
- derechos_vulnerados: lista de derechos fundamentales vulnerados
- peticion: qué solicita exactamente al juez
- genero: "masculino" o "femenino" según el nombre del accionante

Si algún campo no está en el texto, déjalo como string vacío "".
Responde SOLO el JSON, sin explicaciones."""

SISTEMA_TUTELA = """Eres un abogado constitucionalista colombiano especializado en acciones de tutela.
Redacta una tutela formal y profesional en formato legal colombiano que incluya:

1. ENCABEZADO: ciudad, fecha, "Señor JUEZ CONSTITUCIONAL DE ___ (REPARTO) - E.S.D."
2. ACCIONANTE: nombre, documento, datos de contacto
3. ACCIONADO: nombre de la entidad demandada
4. HECHOS: numerados, claros, cronológicos. ADAPTA el género de los pronombres al género del accionante (masculino: "afiliado", "diagnosticado", "paciente"; femenino: "afiliada", "diagnosticada", "paciente").
5. DERECHOS VULNERADOS: citar artículos específicos de la Constitución (Arts. 2, 11, 23, 48, 49, 86, etc.)
6. PETICIÓN: solicitud clara y precisa al juez
7. JURAMENTO: "Bajo la gravedad de juramento afirmo que no he promovido otra acción de tutela por los mismos hechos"
8. PRUEBAS: listar los documentos que se adjuntan
9. NOTIFICACIONES: correo del accionante y de la entidad
10. FIRMA: nombre y documento del accionante

Usa citas legales colombianas reales (Constitución Política, Decreto 2591 de 1991, jurisprudencia relevante)."""


async def transcribir_audio(ruta_audio: str) -> str | None:
    client = _get_client()
    if not client:
        return None
    with open(ruta_audio, "rb") as f:
        transcript = await client.audio.transcriptions.create(
            model=settings.ai_whisper_model,
            file=f,
            language="es",
        )
    return transcript.text


async def extraer_datos(texto: str) -> dict:
    client = _get_client()
    if not client:
        return {"hechos": texto}
    resp = await client.chat.completions.create(
        model=settings.ai_chat_model,
        messages=[
            {"role": "system", "content": SISTEMA_EXTRACCION},
            {"role": "user", "content": texto},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


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


async def analizar_imagen(url_imagen: str) -> str:
    """Analiza una imagen (prueba/documento) usando visión por IA."""
    client = _get_client()
    if not client:
        return ""
    try:
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
    except Exception:
        return ""


async def generar_tutela(datos: dict) -> str | None:
    client = _get_client()
    if not client:
        return None

    accionado = datos.get("accionado", "la entidad")
    accionante = datos.get("accionante_nombre", "el accionante")
    ciudad = datos.get("ciudad", "la ciudad")
    genero = datos.get("genero", "masculino")

    prompt = (
        f"Redacta una accion de tutela formal en formato legal colombiano.\n\n"
        f"GÉNERO DEL ACCIONANTE: {genero} (usa pronombres concordantes: {'él/le/lo/afiliado/diagnosticado/paciente' if genero == 'masculino' else 'ella/le/la/afiliada/diagnosticada/paciente'})\n\n"
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
        f"Incluye el juramento obligatorio, lista de pruebas documentales, notificaciones y firma."
    )

    resp = await client.chat.completions.create(
        model=settings.ai_chat_model,
        messages=[
            {"role": "system", "content": SISTEMA_TUTELA},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content
