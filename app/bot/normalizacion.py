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


# ── EQUIVALENCIAS TIPO DOCUMENTO ──────────────────────────────────────────────
# La fuente de verdad del mapeo entre lo que pide el flujo de WhatsApp (y el
# bot guarda en `datos["accionante_tipo_doc"]`) y la lista desplegable exacta
# del portal de la Rama Judicial. Antes esto vivía en un ALIASES JS frágil y el
# readback comparaba contra el literal desnormalizado, por eso "siempre falla
# el tipo de documento". Ahora: sinónimo (lo que escribe un humano/IA) -> clave
# canónica (CC/CE/TI/PA/PEP/RAMV/SC/PPT) -> etiqueta exacta del dropdown.
#
# Orden de opciones confirmado en prod (html_tipo_doc del portal):
#   CÉDULA DE CIUDADANÍA, CÉDULA DE EXTRANJERÍA, TARJETA DE IDENTIDAD,
#   PASAPORTE, PERMISO ESPECIAL DE PERMANENCIA, PERMISO ESPECIAL DE PERMANENCIA
#   - RAMV, SALVO CONDUCTO, PERMISO POR PROTECCIÓN TEMPORAL
TIPO_DOCUMENTO_EQUIVALENCIAS: dict[str, dict] = {
    "CC": {
        "portal": "CÉDULA DE CIUDADANÍA",
        "sinonimos": (
            "cc", "c.c.", "cd", "ciudadania", "ciudadanía",
            "cédula de ciudadanía", "cedula de ciudadania", "cédula", "cedula",
        ),
    },
    "CE": {
        "portal": "CÉDULA DE EXTRANJERÍA",
        "sinonimos": (
            "ce", "c.e.", "extranjeria", "extranjería",
            "cédula de extranjería", "cedula de extranjeria",
        ),
    },
    "TI": {
        "portal": "TARJETA DE IDENTIDAD",
        "sinonimos": ("ti", "t.i.", "tarjeta", "tarjeta de identidad"),
    },
    "PA": {
        "portal": "PASAPORTE",
        "sinonimos": ("pa", "pasaporte", "passport", "pas"),
    },
    "PEP": {
        "portal": "PERMISO ESPECIAL DE PERMANENCIA",
        "sinonimos": ("pep", "permiso especial de permanencia", "permiso"),
    },
    "RAMV": {
        "portal": "PERMISO ESPECIAL DE PERMANENCIA - RAMV",
        "sinonimos": ("ramv", "ramv permiso", "permiso especial de permanencia ramv"),
    },
    "SC": {
        "portal": "SALVO CONDUCTO",
        "sinonimos": ("sc", "s.c.", "salvo conducto"),
    },
    "PPT": {
        "portal": "PERMISO POR PROTECCIÓN TEMPORAL",
        "sinonimos": ("ppt", "proteccion temporal", "protección temporal", "permiso por protección temporal"),
    },
}

_SINONIMOS_TIPO_DOC: dict[str, str] = {
    normalizar_texto(sin): clave
    for clave, info in TIPO_DOCUMENTO_EQUIVALENCIAS.items()
    for sin in info["sinonimos"]
}


def normalizar_tipo_doc(valor: str) -> str | None:
    """Resuelve lo que escribió un humano/IA a la clave canónica.

    '@accionante_tipo_doc': 'CC', 'Pasaporte', 'Cédula de Ciudadanía', 'PEP',
    'C.C.'... -> 'CC', 'PA', 'PEP'... None si no matchea nada.
    """
    clave_buscada = normalizar_texto(valor)
    for clave, info in TIPO_DOCUMENTO_EQUIVALENCIAS.items():
        if normalizar_texto(clave) == clave_buscada:
            return clave
        if normalizar_texto(info["portal"]) == clave_buscada:
            return clave
    return _SINONIMOS_TIPO_DOC.get(clave_buscada)


def etiqueta_portal_tipo_doc(valor: str) -> str:
    """Etiqueta EXACTA del dropdown del portal para un tipo de documento.

    Acepta la clave canónica o un sinónimo del flujo; si no matchea, devuelve
    la etiqueta de CC (es el tipo por defecto de toda tutela).
    """
    clave = normalizar_tipo_doc(valor) or "CC"
    return TIPO_DOCUMENTO_EQUIVALENCIAS[clave]["portal"]


def tipo_doc_equivale(leido: str, esperado: str) -> bool:
    """True si el texto que devolvió el portal equivale a lo que enviamos.

    Compara por clave canónica: 'CÉDULA DE CIUDADANÍA' (leído del portal) ==
    'CC' (lo que pedimos) -> True. También tolera que el portal devuelva el
    ID sin acentos o con variaciones.
    """
    clave_leido = normalizar_tipo_doc(leido)
    clave_esperado = normalizar_tipo_doc(esperado)
    if clave_esperado and clave_leido:
        return clave_leido == clave_esperado
    if clave_esperado and not clave_leido:
        return verificar_igual(etiqueta_portal_tipo_doc(esperado), leido, "texto")
    return verificar_igual(leido, esperado, "texto")