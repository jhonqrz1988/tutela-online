import os
import uuid

from app.config import settings


def unique_filename(extension: str) -> str:
    return f"{uuid.uuid4().hex}{extension}"


def _ruta(subdir: str, extension: str) -> str:
    """Construye la ruta dentro de settings.storage_dir/<subdir> creando el dir."""
    base = settings.storage_dir or "storage"
    directorio = os.path.join(base, subdir)
    os.makedirs(directorio, exist_ok=True)
    return os.path.join(directorio, unique_filename(extension))


def path_tutela_pdf() -> str:
    return _ruta("tutelas", ".pdf")


def path_tutela_pdf_nombre(stem: str) -> str:
    """Ruta de la tutela con nombre legible (p.ej. '{cedula}_tutela') y sin
    colisiones: si el archivo ya existe se añade _2, _3... al stem.

    El PDF generado se sube como DEMANDA al portal: un nombre con la cédula
    del cliente permite identificarlo de un vistazo (pedido en producción).
    """
    base = settings.storage_dir or "storage"
    directorio = os.path.join(base, "tutelas")
    os.makedirs(directorio, exist_ok=True)
    stem_limpio = "".join(c for c in str(stem).strip() if c.isalnum() or c in "_-") or "tutela"
    ruta = os.path.join(directorio, f"{stem_limpio}.pdf")
    sufijo = 2
    while os.path.exists(ruta):
        ruta = os.path.join(directorio, f"{stem_limpio}_{sufijo}.pdf")
        sufijo += 1
    return ruta


def path_prueba(ext: str = ".jpg") -> str:
    return _ruta("pruebas", ext)


def path_constancia() -> str:
    return _ruta("constancias", ".pdf")


def path_constancia_imagen(ext: str = ".png") -> str:
    return _ruta("constancias", ext)
