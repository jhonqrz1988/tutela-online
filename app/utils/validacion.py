import re

# Reintentos antes de aceptar un valor inválido en la entrada de WhatsApp.
# UX: re-preguntar sin bloquear; el pre-vuelo de radicación es el filtro duro.
MAX_REINTENTOS_VALIDACION = 2

_CEDULA_RE = re.compile(r"^\d{6,10}$")
_TELEFONO_RE = re.compile(r"^\+?\d{7,13}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NOMBRE_RE = re.compile(
    r"^[A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:[ -][A-Za-zÁÉÍÓÚÑáéíóúñ]+){0,3}$"
)

_ERRORES = {
    "accionante_cedula": "Ese número de documento no parece válido (6 a 10 dígitos, sin puntos).",
    "accionante_telefono": "El teléfono debe tener de 7 a 13 dígitos (con o sin +57).",
    "accionante_email": "Ese correo no parece válido. Revisa el formato (ej: nombre@correo.com).",
    "accionante_nombres": "Escribe tus nombres con letras (ej: María Fernanda).",
    "accionante_apellidos": "Escribe tus apellidos con letras (ej: Pérez Gómez).",
}


def normalizar_campo(campo: str, valor: str) -> str:
    """Limpia el valor antes de guardarlo según el tipo de campo."""
    texto = str(valor)
    if campo == "accionante_cedula":
        return re.sub(r"[.\s]", "", texto)
    if campo == "accionante_telefono":
        return re.sub(r"\s", "", texto)
    if campo == "accionante_email":
        return texto.strip().lower()
    if campo in ("accionante_nombres", "accionante_apellidos"):
        return re.sub(r"\s+", " ", texto).strip()
    return texto.strip()


def validar_campo_personal(campo: str, valor: str) -> str | None:
    """Retorna el mensaje de error si el campo es inválido, o None si es válido.

    Solamente se validan los campos que el bot escribe tal cual en el portal
    (cédula, teléfono, email). Los demás (nombre, ciudad, EPS) se aceptan.
    """
    texto = str(valor).strip()
    if campo == "accionante_cedula":
        valido = bool(_CEDULA_RE.match(texto))
    elif campo == "accionante_telefono":
        valido = bool(_TELEFONO_RE.match(texto))
    elif campo == "accionante_email":
        valido = bool(_EMAIL_RE.match(texto))
    elif campo in ("accionante_nombres", "accionante_apellidos"):
        valido = len(texto) >= 3 and bool(_NOMBRE_RE.match(texto))
    else:
        return None
    return None if valido else _ERRORES[campo]


def procesar_campo_personal(datos: dict, campo: str, valor: str) -> tuple[str, str]:
    """Orquesta la entrada por paso en WhatsApp.

    Retorna `(valor_a_guardar, accion)`:
      - "ok": valor válido (normalizado) → guardar y avanzar.
      - "reintento": inválido y quedan intentos → re-preguntar, no avanzar.
      - "aceptado": inválido tras superar el tope → guardar con aviso; el
        pre-vuelo de radicación detectará el problema antes del bot.

    Lleva el contador en `datos["_val_<campo>"]` para no clavar al usuario.
    """
    valor_norm = normalizar_campo(campo, valor)
    if validar_campo_personal(campo, valor_norm) is None:
        datos.pop(f"_val_{campo}", None)
        return valor_norm, "ok"

    recientes = datos.get(f"_val_{campo}", 0) + 1
    datos[f"_val_{campo}"] = recientes
    if recientes <= MAX_REINTENTOS_VALIDACION:
        return valor, "reintento"
    return valor_norm, "aceptado"