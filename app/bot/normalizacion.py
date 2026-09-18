"""Normalizador de la capa Playwright (solo del bot).

Única fuente de verdad para COMPARAR lo que guardamos en ``datos`` contra lo
que el portal realmente muestra tras escribir: mismas reglas al guardar (entrada
WhatsApp, en app/utils/validacion.py) y al verificar (llenar -> leer -> comparar).

El portal puede normalizar el dato distinto a como lo enviamos (quitar o poner
espacios, puntos, tildes, mayúsculas): comparar texto crudo produce falsos
fallos. Este normalizador hace la comparación tolerante y determinista.
"""

import re
import unicodedata


def quitar_acentos(texto: str) -> str:
    """Elimina tildes/diacríticos (NFD sin combining marks). 'Ramírez'->'Ramirez'."""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", texto or "")
        if unicodedata.category(ch) != "Mn"
    )


def normalizar_texto(texto: str) -> str:
    """Nombres, direcciones, ciudades, EPS, tipo_doc: sin acentos,
    minúsculas y whitespace colapsado."""
    t = quitar_acentos(texto)
    return re.sub(r"\s+", " ", t).strip().lower()


def normalizar_numero(texto: str) -> str:
    """Cédula y NIT: solo dígitos (quita espacios, puntos, guiones)."""
    return re.sub(r"[^\d]", "", str(texto or ""))


def normalizar_telefono(texto: str) -> str:
    """Teléfono: solo dígitos (quita '+57', guiones, espacios)."""
    return re.sub(r"[^\d]", "", str(texto or ""))


def _digitos_sin_prefijo_pais(digitos: str) -> str:
    """Quita el prefijo '+57' (Colombia) solo si el número quedó largo:
    '573012345678' -> '3012345678'. Si no empieza con 57 se deja igual."""
    if digitos.startswith("57") and len(digitos) >= 11:
        return digitos[2:]
    return digitos


def normalizar_email(texto: str) -> str:
    """Correo: mayúsculas a minúsculas y espacios externos."""
    return str(texto or "").strip().lower()


_MODO_NORMALIZACION = {
    "accionante_cedula": "numero",
    "accionante_telefono": "telefono",
    "accionante_email": "email",
    "accionado_nit": "numero",
    "accionado_telefono": "telefono",
    "accionado_email": "email",
}


def normalizar_campo(campo: str, valor: str) -> str:
    """Aplica el modo de normalización según el campo del formulario."""
    modo = _MODO_NORMALIZACION.get(campo, "texto")
    if modo == "numero":
        return normalizar_numero(valor)
    if modo == "telefono":
        return normalizar_telefono(valor)
    if modo == "email":
        return normalizar_email(valor)
    return normalizar_texto(valor)


def verificar_igual(esperado: str, recibido: str, modo: str = "texto") -> bool:
    """True si el valor recibido del portal equivale al esperado (tolerante).

    ``modo``: "texto" | "numero" | "telefono" | "email".
    """
    if modo == "numero":
        return normalizar_numero(esperado) == normalizar_numero(recibido)
    if modo == "telefono":
        a = normalizar_telefono(esperado)
        b = normalizar_telefono(recibido)
        return a == b or _digitos_sin_prefijo_pais(a) == _digitos_sin_prefijo_pais(b)
    if modo == "email":
        return normalizar_email(esperado) == normalizar_email(recibido)
    return normalizar_texto(esperado) == normalizar_texto(recibido)