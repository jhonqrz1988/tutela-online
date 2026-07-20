from pydantic import BaseModel


class DatosTutela(BaseModel):
    tipo: str = "salud"
    accionante_nombre: str = ""
    accionante_cedula: str = ""
    accionante_direccion: str = ""
    accionante_telefono: str = ""
    accionante_email: str = ""
    accionado: str = ""
    derechos_vulnerados: list[str] = []
    hechos: str = ""
    ciudad: str = ""
    peticion: str = ""


class TutelaResponse(BaseModel):
    id: int
    user_id: int
    tipo: str
    estado: str
    datos_json: str | None
    pdf_path: str | None
    created_at: str
