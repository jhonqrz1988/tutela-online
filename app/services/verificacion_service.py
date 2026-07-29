import re

from sqlalchemy.orm import Session

from app.models.cita_legal import CitaLegal, CitaPendiente

TEXTO_GENERICO_SALUD = "la normativa vigente sobre proteccion del derecho a la salud"


def normalizar_referencia(texto: str) -> str:
    texto = texto.lower()
    texto = re.sub(r"sentencia\s*", "", texto)
    texto = re.sub(r"art[íi]culo\s*", "art. ", texto)
    texto = re.sub(r"l[aá] ley\s*", "ley ", texto)
    texto = re.sub(r"decreto\s*", "decreto ", texto)
    texto = re.sub(r"resoluci[oó]n\s*", "resolucion ", texto)
    texto = re.sub(r"acuerdo\s*", "acuerdo ", texto)
    texto = re.sub(r"constituci[oó]n pol[íi]tica", "constitucion politica", texto)
    texto = re.sub(r"c\.p\.", "constitucion politica", texto)
    texto = re.sub(r"[\s\-\.]+", " ", texto)
    texto = texto.strip()
    return texto


def verificar_citas(citas_extraidas: list[dict], db_session: Session, vertical: str = "salud") -> dict:
    citas_validas = []
    citas_a_revisar = []

    whitelist = {
        c.referencia_normalizada: c
        for c in db_session.query(CitaLegal)
        .filter(CitaLegal.aplica_a == vertical, CitaLegal.vigente == True)
        .all()
    }

    for cita in citas_extraidas:
        ref_norm = normalizar_referencia(cita.get("referencia_textual", ""))
        match = whitelist.get(ref_norm)

        if match:
            citas_validas.append({
                "referencia": match.referencia,
                "url_fuente": match.url_fuente,
                "contexto": cita.get("contexto", ""),
            })
        else:
            citas_a_revisar.append(cita)

    return {"validas": citas_validas, "pendientes_revision": citas_a_revisar}


def limpiar_texto_para_pdf(texto_tutela: str, citas_a_revisar: list[dict]) -> str:
    texto_limpio = texto_tutela
    for cita in citas_a_revisar:
        ref = cita.get("referencia_textual", "")
        if ref:
            texto_limpio = texto_limpio.replace(ref, TEXTO_GENERICO_SALUD)
    return texto_limpio


def guardar_pendientes(tutela_id: int, citas_a_revisar: list[dict], db_session: Session):
    for cita in citas_a_revisar:
        db_session.add(CitaPendiente(
            tutela_id=tutela_id,
            referencia_textual=cita.get("referencia_textual", ""),
            contexto=cita.get("contexto", ""),
        ))
    db_session.commit()