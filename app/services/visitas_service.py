"""Registro de visitas a la página de aterrizaje para medir tráfico de pauta.

Nunca debe fallar ni ralentizar la carga de la landing: cualquier error se
loguea y se ignora.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from app.database import SessionLocal
from app.models.visita import VisitaLanding

logger = logging.getLogger(__name__)

BOGOTA_TZ = ZoneInfo("America/Bogota")

_CAMPOS_UTM = {
    "utm_source": "fuente",
    "utm_medium": "medio",
    "utm_campaign": "campania",
    "utm_term": "termino",
    "utm_content": "contenido",
}

_MESES_ES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]

# Nombres legibles de las fuentes de tráfico (utm_source y afines).
_NOMBRES_FUENTE = {
    "directo": "Directo",
    "": "Directo",
    "fb": "Facebook",
    "facebook": "Facebook",
    "ig": "Instagram",
    "instagram": "Instagram",
    "an": "Anuncios",
    "anuncios": "Anuncios",
    "google": "Google",
    "chatgpt.com": "ChatGPT",
    "tiktok": "TikTok",
    "meta": "Meta",
}

def nombre_fuente(fuente: str | None) -> str:
    """Traduce un código de fuente (utm_source) a un nombre legible.

    ``None``, vacío y ``directo`` mapean a "Directo". Fuentes desconocidas se
    devuelven tal cual para no inventar nombres que confundan el reporte.
    """
    if not fuente:
        return "Directo"
    return _NOMBRES_FUENTE.get(fuente, fuente)


def _en_bogota(dt) -> datetime:
    if dt is None:
        return datetime.now(BOGOTA_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BOGOTA_TZ)


def _clave_mes(dt) -> str:
    return f"{dt.year:04d}-{dt.month:02d}"


def rango_mes_utc(mes: str) -> tuple[datetime, datetime]:
    """Rango UTC [inicio, fin) del mes indicado (``YYYY-MM``) en hora de Bogotá.

    Un mes laboral colombiano va de las 00:00 del día 1 en Bogotá (UTC-5) a las
    00:00 del día 1 del mes siguiente: por eso en UTC el inicio es 05:00 del día 1.
    """
    year, month = mes.split("-")
    year, month = int(year), int(month)
    if not (1 <= month <= 12):
        raise ValueError(f"Mes inválido: {mes}")
    inicio = datetime(year, month, 1, 0, 0, 0, tzinfo=BOGOTA_TZ)
    if month == 12:
        fin = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=BOGOTA_TZ)
    else:
        fin = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=BOGOTA_TZ)
    return inicio.astimezone(timezone.utc).replace(tzinfo=None), fin.astimezone(timezone.utc).replace(tzinfo=None)


def _clave_semana(dt) -> str:
    iso = dt.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def _etiqueta_mes(clave: str) -> str:
    year, mes = clave.split("-")
    return f"{_MESES_ES[int(mes) - 1]} {year}"


def agrupar_por_periodo(visitas, periodo: str) -> list[dict]:
    """Agrupa visitas (lista de dicts con ``created_at``/``es_pauta``) por
    semana o mes laboral (hora de Bogotá), ordenado descendente de fecha.

    Devuelve ``[{clave, etiqueta, n, pauta}]``.
    """
    agrupado: dict[str, dict] = {}
    clave_fn = _clave_semana if periodo == "semana" else _clave_mes
    for v in visitas:
        dt = _en_bogota(v["created_at"])
        clave = clave_fn(dt)
        g = agrupado.setdefault(clave, {"clave": clave, "etiqueta": "", "n": 0, "pauta": 0})
        g["n"] += 1
        if v.get("es_pauta"):
            g["pauta"] += 1
    for clave, g in agrupado.items():
        g["etiqueta"] = (
            _etiqueta_semana(clave) if periodo == "semana" else _etiqueta_mes(clave)
        )
    return sorted(agrupado.values(), key=lambda x: x["clave"], reverse=True)


def _etiqueta_semana(clave: str) -> str:
    year, semana = clave.split("-W")
    iso = date(int(year), 1, 4)
    lunes_semana = iso - timedelta(days=iso.isoweekday() - 1) + timedelta(weeks=int(semana) - 1)
    return f"Semana del {lunes_semana.day} {_MESES_ES[lunes_semana.month - 1].lower()} {lunes_semana.year}"


def agrupar_tutelas_por_periodo(tutelas, periodo: str) -> list[dict]:
    """Agrupa tutelas (lista de dicts con ``created_at``/``estado``) por semana
    o mes (hora de Bogotá), ordenado descendente.

    Devuelve ``[{clave, etiqueta, tutelas, radicadas}]``.
    """
    agrupado: dict[str, dict] = {}
    clave_fn = _clave_semana if periodo == "semana" else _clave_mes
    for t in tutelas:
        dt = _en_bogota(t["created_at"])
        clave = clave_fn(dt)
        g = agrupado.setdefault(clave, {"clave": clave, "etiqueta": "", "tutelas": 0, "radicadas": 0})
        g["tutelas"] += 1
        if t.get("estado") in ("radicada", "completado"):
            g["radicadas"] += 1
    for clave, g in agrupado.items():
        g["etiqueta"] = (
            _etiqueta_semana(clave) if periodo == "semana" else _etiqueta_mes(clave)
        )
    return sorted(agrupado.values(), key=lambda x: x["clave"], reverse=True)


def registrar_visita_landing(query_string: str) -> None:
    """Registra una carga de la landing en la BD (ignorando errores)."""
    try:
        params = parse_qs(query_string or "", keep_blank_values=True)

        valores = {campo: "" for campo in _CAMPOS_UTM}
        for clave_utm, attr in _CAMPOS_UTM.items():
            raw = params.get(clave_utm, [""])[0]
            valores[attr] = raw[:200]

        # Un clic de anuncio de TikTok llega con ttclid (sin utm_*): cuenta
        # como pauta y, si no hay utm_source, identifica la fuente.
        ttclid = bool(params.get("ttclid"))
        if ttclid and not valores["fuente"]:
            valores["fuente"] = "tiktok"

        es_pauta = bool(params.get("fbclid")) or ttclid or any(
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