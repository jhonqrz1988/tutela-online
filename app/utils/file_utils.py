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


def path_prueba(ext: str = ".jpg") -> str:
    return _ruta("pruebas", ext)


def path_constancia() -> str:
    return _ruta("constancias", ".pdf")


def path_constancia_imagen(ext: str = ".png") -> str:
    return _ruta("constancias", ext)
