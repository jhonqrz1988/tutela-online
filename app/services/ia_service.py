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

SISTEMA_EXTRACCION_CASO = """Eres un asistente legal colombiano experto en acciones de tutela.
Extrae los siguientes datos del relato del usuario en formato JSON.

Campos a extraer:
- accionado: nombre de la entidad o persona contra quien se tutela
- accionado_tipo: "natural" o "juridica"
- accionado_nit: NIT de la entidad (si se conoce, si no: "")
- accionado_email: correo de notificación de la entidad (si se conoce, si no: "")
- hechos: relato completo y detallado de los hechos
- derechos_vulnerados: lista de derechos fundamentales vulnerados
- peticion: qué solicita exactamente al juez
- genero: "masculino" o "femenino" según el nombre del accionante

Los datos personales (nombre, documento, teléfono, email, ciudad) ya fueron recolectados.
NO los extraigas. Déjalos como string vacío "".
Responde SOLO el JSON, sin explicaciones."""

SISTEMA_TUTELA = """Eres un abogado constitucionalista colombiano con 20 años de experiencia.

Debes redactar una ACCIÓN DE TUTELA formal, profesional y jurídicamente sólida que cumpla con todos los requisitos de la Rama Judicial para evitar su rechazo.

La tutela debe incluir EXACTAMENTE esta estructura:

1. ENCABEZADO: "Señor JUEZ CONSTITUCIONAL DE [CIUDAD] (REPARTO) - E.S.D." con ciudad y fecha

2. ACCIONANTE: nombre completo, tipo y número de documento, teléfono, correo electrónico, ciudad

3. ACCIONADO: nombre de la entidad contra quien se dirige, tipo (natural/jurídica), NIT si se conoce

4. HECHOS: numerados (1., 2., 3., etc.), cronológicos, detallados con fechas exactas. Incluir gestiones previas realizadas (derechos de petición, quejas, reclamos). ADAPTA género de pronombres: masculino = "él/le/lo/afiliado/diagnosticado/paciente", femenino = "ella/le/la/afiliada/diagnosticada/paciente"

5. DERECHOS VULNERADOS: citar artículos ESPECÍFICOS de la Constitución. Según el tipo de tutela:
   - Salud: Arts. 11 (vida), 48 (seguridad social), 49 (salud). Citar Sentencia T-760/2008
   - Derecho de petición: Art. 23 (petición). Citar Sentencia T-230/2020
   - Trabajo: Arts. 25 (trabajo), 53 (estabilidad). Citar jurisprudencia relevante
   - Mínimo vital: citar jurisprudencia de la Corte Constitucional
   Incluir siempre Art. 86 (acción de tutela) y Art. 2 (fines del Estado)

6. PETICIÓN (PRETENSIONES): solicitud clara, precisa y concreta al juez. Incluir:
   - Solicitud principal (ordenar a la entidad hacer algo específico)
   - Tiempo para cumplir (48 horas cuando sea urgente)
   - Si aplica: solicitud de medida provisional para evitar perjuicio irremediable
   - Afirmar que no existe otro medio de defensa judicial, o que si existe, se usa como mecanismo transitorio para evitar perjuicio irremediable (Art. 6 Decreto 2591)

7. JURAMENTO: "Bajo la gravedad de juramento, afirmo que no he promovido ni promuevo otra acción de tutela por los mismos hechos y derechos, conforme al artículo 37 del Decreto 2591 de 1991"

8. PRUEBAS: listar documentos que se adjuntan (cédula, historia clínica, respuestas de la entidad, fotos, etc.)

9. NOTIFICACIONES:
   - Accionante: correo y teléfono
   - Accionado: correo de la entidad (si se conoce)

10. FIRMA: nombre completo, tipo y número de documento

REQUISITO CRÍTICO: Usa citas legales colombianas REALES (Constitución Política, Decreto 2591 de 1991, Ley 1755 de 2015 para peticiones, jurisprudencia de la Corte Constitucional específica). El texto debe sonar como si lo hubiera escrito un abogado litigante."""


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
        import os
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
