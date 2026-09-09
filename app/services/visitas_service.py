"""Registro de visitas a la página de aterrizaje para medir tráfico de pauta.

Nunca debe fallar ni ralentizar la carga de la landing: cualquier error se
loguea y se ignora.
"""
import logging
from urllib.parse import parse_qs

from app.database import SessionLocal
from app.models.visita import VisitaLanding

logger = logging.getLogger(__name__)

_CAMPOS_UTM = {
    "utm_source": "fuente",
    "utm_medium": "medio",
    "utm_campaign": "campania",
    "utm_term": "termino",
    "utm_content": "contenido",
}


def registrar_visita_landing(query_string: str) -> None:
    """Registra una carga de la landing en la BD (ignorando errores)."""
    try:
        params = parse_qs(query_string or "", keep_blank_values=True)

        valores = {campo: "" for campo in _CAMPOS_UTM}
        for clave_utm, attr in _CAMPOS_UTM.items():
            raw = params.get(clave_utm, [""])[0]
            valores[attr] = raw[:200]

        es_pauta = bool(params.get("fbclid")) or any(
            v for v in valores.values() if v
        )

        session = SessionLocal()
        try:
            session.add(VisitaLanding(
                fuente=valores["fuente"] or "directo",
                medio=valores["medio"] or None,
                campania=valores["campania"] or None,
                termino=valores["termino"] or None,
                contenido=valores["contenido"] or None,
                es_pauta=es_pauta,
            ))
            session.commit()
        finally:
            session.close()
    except Exception:
        logger.exception("No se pudo registrar la visita a la landing")