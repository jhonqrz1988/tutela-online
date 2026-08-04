import os
import uuid


def unique_filename(extension: str) -> str:
    return f"{uuid.uuid4().hex}{extension}"


def path_tutela_pdf() -> str:
    os.makedirs("storage/tutelas", exist_ok=True)
    return f"storage/tutelas/{unique_filename('.pdf')}"


def path_prueba(ext: str = ".jpg") -> str:
    os.makedirs("storage/pruebas", exist_ok=True)
    return f"storage/pruebas/{unique_filename(ext)}"


def path_constancia() -> str:
    os.makedirs("storage/constancias", exist_ok=True)
    return f"storage/constancias/{unique_filename('.pdf')}"


def path_constancia_imagen(ext: str = ".png") -> str:
    os.makedirs("storage/constancias", exist_ok=True)
    return f"storage/constancias/{unique_filename(ext)}"
