import base64
import json
import logging
import os

import httpx
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)


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

SISTEMA_TUTELA = """Eres un abogado constitucionalista colombiano con 20 años de experiencia en acciones de tutela de salud.

Genera una TUTELA LEGAL COMPLETA, PROFESSIONAL y JURISDICCIONAL siguiendo EXACTAMENTE la estructura de la Corte Constitucional y la Rama Judicial Colombiana.

ESTRUCTURA REQUERIDA:

1. ENCABEZADO: "Señor JUEZ CONSTITUCIONAL DE [CIUDAD] (REPARTO) - E.S.D." con fecha exacta (día de mes de año)

2. ACCIONANTE: Nombre completo, tipo y número de documento, teléfono, correo, ciudad

3. ACCIONADO: Nombre de la entidad (EPS, alcaldía, etc.), tipo (jurídica/natural), NIT si aplica

4. HECHOS: Numerados (1., 2., 3.), cronológicos, con FECHAS EXACTAS. Menciona gestiones previas (quejas, reclamos, derechos de petición). Género concordante según accionante.

5. DERECHOS VULNERADOS: Citar artículos específicos:
   - Art. 11 C.P. (derecho a la vida)
   - Art. 48 C.P. (seguridad social)
   - Art. 49 C.P. (derecho a la salud)
   - Art. 86 C.P. (acción de tutela)
   - Art. 2 C.P. (fines del Estado)
   - Sentencia T-760/2008 (salud)

6. PETICIÓN: Solicitud clara, precisa, concreta. Ej: "Que se autorice cita con medicina general en 48 horas". Si es urgente, menciona "medida provisional" y "irremediable".

7. JURAMENTO: "Bajo la gravedad de juramento, afirmo que no he promovido ni promuevo otra acción de tutela por los mismos hechos y derechos, conforme al artículo 37 del Decreto 2591 de 1991."

8. PRUEBAS: Listar documentos adjuntos (cédula, historia clínica, resultados, etc.)

9. NOTIFICACIONES: Correo y teléfono del accionante y del accionado

10. FIRMA: Nombre completo, tipo y número de documento

REQUISITO CRÍTICO: Usa citas legales REALES y verificadas. El texto debe sonar como lo escribiría un abogado litigante colombiano. NO uses lenguaje genérico. Sé específico en cada hecho y petición."""

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


async def transcribir_audio(ruta_audio: str) -> str | None:
    client = _get_client()
    if not client:
        return None
    try:
        with open(ruta_audio, "rb") as f:
            transcript = await client.audio.transcriptions.create(
                model=settings.ai_whisper_model,
                file=f,
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
        if os.path.isfile(url_imagen):
            with open(url_imagen, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode()
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
    pronombres = "él/le/lo/afiliado/diagnosticado/paciente" if genero == "masculino" else "ella/le/la/afiliada/diagnosticada/paciente"

    prompt = (
        f"Redacta una accion de tutela formal en formato legal colombiano.\n\n"
        f"GÉNERO DEL ACCIONANTE: {genero} (usa pronombres concordantes: {pronombres})\n\n"
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