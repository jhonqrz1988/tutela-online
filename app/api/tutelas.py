from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.admin import require_admin
from app.database import get_session
from app.models.tutela import Tutela
from app.schemas.tutela import DatosTutela
from app.services.documento_service import generar_pdf
from app.services.radicacion_service import iniciar_radicacion

router = APIRouter(prefix="/api/v1/tutelas", dependencies=[Depends(require_admin)])


@router.post("")
def crear_tutela(datos: DatosTutela, session=Depends(get_session)):
    tutela = Tutela(
        user_id=1,
        tipo=datos.tipo,
        estado="borrador",
        datos_json=datos.model_dump_json(),
    )
    session.add(tutela)
    session.commit()
    session.refresh(tutela)
    return {"id": tutela.id, "estado": tutela.estado}


@router.get("/{tutela_id}")
def obtener_tutela(tutela_id: int, session=Depends(get_session)):
    tutela = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not tutela:
        raise HTTPException(status_code=404, detail="No encontrada")
    return {
        "id": tutela.id,
        "tipo": tutela.tipo,
        "estado": tutela.estado,
        "pdf_path": tutela.pdf_path,
    }


@router.post("/{tutela_id}/generar-pdf")
def generar_pdf_tutela(tutela_id: int, session=Depends(get_session)):
    import json

    tutela = session.execute(select(Tutela).where(Tutela.id == tutela_id)).scalar_one_or_none()
    if not tutela:
        raise HTTPException(status_code=404, detail="No encontrada")

    datos = json.loads(tutela.datos_json or "{}")
    contenido = datos.get("hechos", "")
    ruta = generar_pdf(datos, contenido)
    tutela.pdf_path = ruta
    tutela.estado = "pdf_generado"
    session.commit()
    return {"pdf_path": ruta}


@router.post("/{tutela_id}/radicar")
def radicar_tutela(tutela_id: int):
    import asyncio
    resultado = asyncio.run(iniciar_radicacion(tutela_id))
    return resultado
