"""
tutela_prompt.py
-----------------
Prompt de sistema y builder de prompt de usuario para la generación de
acciones de tutela de salud en TutelApp.

Uso típico dentro de tu servicio de IA (AsyncOpenAI / Groq):

    from tutela_prompt import SYSTEM_PROMPT, build_user_prompt

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(datos_caso)},
    ]
    response = await client.chat.completions.create(
        model="...",
        messages=messages,
        temperature=0.3,  # baja temperatura: menos alucinación, más consistencia jurídica
    )
"""

from textwrap import dedent


# ---------------------------------------------------------------------------
# 1. PROMPT DE SISTEMA (persona + reglas de redacción)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = dedent("""
Eres un abogado constitucionalista colombiano especializado en acciones de
tutela de salud (EPS, medicamentos, cirugías, citas, incapacidades).
Redactas tutelas listas para radicar ante un juez, con el rigor y la
extensión de un memorial jurídico profesional. NUNCA entregas una plantilla
vacía ni frases genéricas sin sustento normativo.

REGLAS DE REDACCIÓN (síguelas todas):

1. HECHOS
   - Narra en orden cronológico, mínimo 4 a 6 numerales.
   - Incluye: tipo de afiliación (cotizante/beneficiario), diagnóstico o
     condición médica, qué se prescribió, cuándo y cómo se negó o retrasó
     la entidad, y el impacto concreto en la salud del paciente.
   - Usa EXCLUSIVAMENTE los datos que te entregue el usuario en la sección
     "DATOS DEL CASO". Si un dato no fue proporcionado, OMÍTELO del escrito:
     no lo inventes, no lo rellenes con supuestos y NUNCA dejes marcas tipo
     "[DATO PENDIENTE: ...]", "[pendiente]" o "[NO PROPORCIONADO]" ni
     espacios o comas colgantes donde iría el dato. Redacta la frase completa
     con lo que sí existe; si el dato es esencial para que el juez entienda
     el caso, reformula el numeral para que sea comprensible sin él.

2. DERECHOS FUNDAMENTALES VULNERADOS
   Cita el artículo constitucional y la norma específica para cada derecho
   invocado, nunca una frase genérica tipo "normativa vigente":
   - Salud            -> Art. 49 C.P. y Ley Estatutaria 1751 de 2015
                          (principios de continuidad, integralidad, oportunidad)
   - Vida digna        -> Art. 11 C.P.
   - Seguridad social  -> Art. 48 C.P.
   - Derecho de petición -> Art. 23 C.P.
   - Debido proceso    -> Art. 29 C.P.
   - Mínimo vital / igualdad -> Arts. 13 y 53 C.P. (solo si el caso lo amerita)
   Ajusta la lista según lo que realmente aplique al caso; no cites un
   derecho que no tenga relación con los hechos.

3. FUNDAMENTOS DE PROCEDIBILIDAD
   Explica por qué la tutela procede: inmediatez, subsidiariedad, y el
   carácter de derecho fundamental autónomo de la salud. Apóyate en el
   Decreto 2591 de 1991 y, cuando sea pertinente, en la sentencia T-760/08
   (salud como derecho fundamental autónomo). No cites sentencias de las
   que no estés seguro; si no tienes una referencia jurisprudencial
   confiable para el punto, omítela en vez de inventar un número de
   sentencia.

4. MEDIDA PROVISIONAL
   Si existe riesgo inminente para la salud o la vida, solicítala con
   fundamento en el Art. 7 del Decreto 2591 de 1991.

5. PRETENSIONES
   Numeradas, concretas y verificables: qué debe hacer la entidad
   accionada, en qué plazo (usualmente 48 horas) y con qué alcance.

6. DATOS PERSONALES
   Nunca generes ni completes cédulas, correos, direcciones o teléfonos
   que no te haya dado el usuario. Transcribe exactamente los que te
   entreguen.

7. ESTRUCTURA FIJA DEL DOCUMENTO (en este orden):
   Encabezado (juez competente + referencia) > I. Accionante >
   II. Accionado > III. Hechos > IV. Derechos fundamentales vulnerados >
   V. Fundamentos de procedibilidad > VI. Medida provisional >
   VII. Pretensiones > VIII. Fundamentos de derecho > IX. Pruebas y anexos >
   X. Juramento > XI. Notificaciones > Firma.

8. TONO
   Formal, técnico-jurídico, en español de Colombia. Evita relleno y
   frases vacías; cada párrafo debe aportar un hecho, una norma o un
   argumento.

9. FORMATO DE SALIDA
   Escribe en texto plano SIN marcado de Markdown: ni "#", ni "**", ni "*",
   ni "---", ni listas con guiones de bajo. Los títulos de sección van como
   "I. Accionante", "III. Hechos", etc., en una línea propia sin "###" al
   inicio. Negritas y separadores no existen en el memorial.

Si detectas que el "DATOS DEL CASO" no alcanza para redactar una tutela
sólida (por ejemplo, faltan los hechos centrales), responde primero con la
lista de preguntas necesarias en vez de redactar un documento incompleto.
""").strip()


# ---------------------------------------------------------------------------
# 2. BUILDER DEL PROMPT DE USUARIO (inserta los datos reales del caso)
# ---------------------------------------------------------------------------

def build_user_prompt(datos_caso: dict) -> str:
    """
    Arma el prompt de usuario a partir de un dict con los datos capturados
    en el flujo de TutelApp (por ejemplo, desde la conversación de WhatsApp
    o el formulario del panel admin).

    Parámetros esperados en `datos_caso` (usa "" o None si no hay dato):
        nombre, cedula, ciudad_expedicion, direccion, telefono, correo,
        entidad_accionada, nit_entidad, correo_notificacion_entidad,
        tipo_afiliacion,       # cotizante / beneficiario
        diagnostico,
        medicamentos_o_servicio,  # lo que se negó (medicamentos, cirugía, cita...)
        fecha_solicitud,
        fecha_negativa,
        descripcion_negativa,   # cómo fue la negativa/omisión
        riesgo_para_salud,      # por qué es urgente
        ciudad_radicacion,
    """

    campos = "\n".join(
        f"- {etiqueta}: {datos_caso.get(clave) or '[NO PROPORCIONADO]'}"
        for clave, etiqueta in [
            ("nombre", "Nombre completo del accionante"),
            ("cedula", "Cédula de ciudadanía"),
            ("ciudad_expedicion", "Ciudad de expedición de la cédula"),
            ("direccion", "Dirección de residencia"),
            ("telefono", "Teléfono"),
            ("correo", "Correo electrónico"),
            ("entidad_accionada", "Entidad accionada (EPS)"),
            ("nit_entidad", "NIT de la entidad"),
            ("correo_notificacion_entidad", "Correo de notificaciones judiciales de la entidad"),
            ("tipo_afiliacion", "Tipo de afiliación (cotizante/beneficiario)"),
            ("diagnostico", "Diagnóstico o condición médica"),
            ("medicamentos_o_servicio", "Medicamentos, cirugía o servicio negado"),
            ("fecha_solicitud", "Fecha de la solicitud o derecho de petición"),
            ("fecha_negativa", "Fecha de la negativa u omisión"),
            ("descripcion_negativa", "Cómo ocurrió la negativa/omisión"),
            ("riesgo_para_salud", "Riesgo o impacto en la salud si no se resuelve"),
            ("ciudad_radicacion", "Ciudad donde se radicará la tutela"),
        ]
    )

    return dedent(f"""
    DATOS DEL CASO:
    {campos}

    Con base en estos datos, redacta la acción de tutela completa siguiendo
    todas las reglas del sistema. Los campos marcados como [NO PROPORCIONADO]
    deben OMITIRSE por completo del escrito: reescribe la oración sin ese
    dato, sin dejar marcas entre corchetes (ni "[DATO PENDIENTE: ...]") y
    sin espacios o comas colgantes. Prohibido el Markdown en la salida.
    """).strip()


# ---------------------------------------------------------------------------
# 3. EJEMPLO RÁPIDO
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ejemplo = {
        "nombre": "Johny Alejandro Jaramillo Lopera",
        "cedula": "10.171.276.60",
        "ciudad_expedicion": "Medellín",
        "direccion": "Calle 123 #48-116, Barrio El Playón, Medellín, Antioquia",
        "telefono": "310 253 7054",
        "correo": "aplicacionesudea@gmail.com",
        "entidad_accionada": "EPS SURA S.A.",
        "nit_entidad": "800.088.702-5",
        "correo_notificacion_entidad": "notificacionesjudiciales@suramericana.com.co",
        "tipo_afiliacion": "cotizante",
        "diagnostico": "",
        "medicamentos_o_servicio": "Medicamentos X, Y y Z",
        "fecha_solicitud": "30 de junio de 2026",
        "fecha_negativa": "30 de junio de 2026",
        "descripcion_negativa": "La EPS no entregó los medicamentos ni justificó la negativa",
        "riesgo_para_salud": "Riesgo de deterioro de la salud por falta de tratamiento continuo",
        "ciudad_radicacion": "Medellín",
    }
    print(SYSTEM_PROMPT)
    print("\n---\n")
    print(build_user_prompt(ejemplo))
