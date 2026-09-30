"""Registro de visitas a la página de aterrizaje para medir tráfico de pauta.

Nunca debe fallar ni ralentizar la carga de la landing: cualquier error se
loguea y se ignora.
"""
import json
import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models.clic import ClicWhatsApp
from app.models.visita import VisitaLanding
from app.models.whatsapp import MensajeWhatsApp

logger = logging.getLogger(__name__)

BOGOTA_TZ = ZoneInfo("America/Bogota")

_CAMPOS_UTM = {
    "utm_source": "fuente",
    "utm_medium": "medio",
    "utm_campaign": "campania",
    "utm_term": "termino",
    "utm_content": "contenido",
}

# Subcadenas (en minúsculas) típicas de crawlers que NO generan tráfico humano
# real: previews de redes sociales, buscadores, monitores de uptime, scripts.
_UA_BOTS = (
    "facebookexternalhit",
    "facebot",
    "googlebot",
    "bingbot",
    "yandexbot",
    "baiduspider",
    "duckduckbot",
    "twitterbot",
    "linkedinbot",
    "telegrambot",
    "slackbot",
    "discordbot",
    "pinterest",
    "snapchat",
    "uptimerobot",
    "pingdom",
    "statuscake",
    "site24x7",
    "python-requests",
    "curl",
    "wget",
    "okhttp",
    "go-http-client",
    "java/1.",
    "headlesschrome",
    "phantomjs",
    "playwright",
    "puppeteer",
    "ahrefsbot",
    "semrushbot",
    "mj12bot",
    "spider",
    "crawler",
    "preview",
    "meta-externalagent",
)

_MAX_UA_LEN = 500

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


def contar_conversaciones(session, inicio: datetime, fin: datetime) -> int:
    """Número de teléfonos distintos que escribieron al bot en ``[inicio, fin)``.

    Es la métrica de "conversación real" del embudo (visita -> clic -> mensaje).
    Un usuario que manda varios mensajes cuenta como una sola conversación.
    """
    return session.execute(
        select(func.count(func.distinct(MensajeWhatsApp.from_number))).where(
            MensajeWhatsApp.created_at >= inicio,
            MensajeWhatsApp.created_at < fin,
        )
    ).scalar() or 0


def visitas_clasificadas(session, inicio: datetime, fin: datetime) -> int:
    """Visitas humanas medidas CON el filtro de bots: ``es_bot=False`` y con
    ``user_agent`` capturado (no None ni vacío)."""
    return session.execute(
        select(func.count()).select_from(VisitaLanding).where(
            VisitaLanding.created_at >= inicio,
            VisitaLanding.created_at < fin,
            VisitaLanding.es_bot.is_(False),
            VisitaLanding.user_agent.isnot(None),
            VisitaLanding.user_agent != "",
        )
    ).scalar() or 0


def visitas_legacy(session, inicio: datetime, fin: datetime) -> int:
    """Visitas anteriores al filtro de bots: ``user_agent`` NULL.

    Esas visitas no se clasificaron como humano/bot (quedaron todas como humanas
    por defecto) y por eso inflan el "Total visitas"; se reportan aparte.
    """
    return session.execute(
        select(func.count()).select_from(VisitaLanding).where(
            VisitaLanding.created_at >= inicio,
            VisitaLanding.created_at < fin,
            VisitaLanding.user_agent.is_(None),
        )
    ).scalar() or 0


def conversaciones_sin_respuesta(session, inicio: datetime, fin: datetime) -> int:
    """Teléfonos distintos que escribieron pero NO recibieron respuesta del bot.

    Son dos fallos distintos, ambos contados aquí:
      - ``fallido``: el bot respondió pero Meta rechazó el envío (número
        ``EXPIRED``, plantilla/rechazo, WABA en mal estado).
      - ``sin_respuesta``: el flujo no produjo ninguna respuesta (p. ej. un
        tipo de mensaje que el bot no entendió).

    Es el indicador que explica "hay usuarios donde el flujo no inicia": si este
    número crece, el problema es de entrega de Meta, no del parsing ni del
    dispositivo del usuario.
    """
    return session.execute(
        select(func.count(func.distinct(MensajeWhatsApp.from_number))).where(
            MensajeWhatsApp.created_at >= inicio,
            MensajeWhatsApp.created_at < fin,
            MensajeWhatsApp.envio_estado.in_(["fallido", "sin_respuesta"]),
        )
    ).scalar() or 0


def _origen_de_mensaje(metadata_json: str | None) -> dict:
    """Parsea ``MensajeWhatsApp.metadata_json`` (origen del anuncio de Meta)."""
    if not metadata_json:
        return {}
    try:
        datos = json.loads(metadata_json)
    except (TypeError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def _mensajes_con_origen(session, inicio: datetime, fin: datetime):
    """Mensajes del rango indicados que traen metadata de origen."""
    return session.execute(
        select(MensajeWhatsApp.from_number, MensajeWhatsApp.metadata_json).where(
            MensajeWhatsApp.created_at >= inicio,
            MensajeWhatsApp.created_at < fin,
            MensajeWhatsApp.metadata_json.isnot(None),
        )
    ).all()


def conversaciones_por_anuncio(session, inicio: datetime, fin: datetime) -> list[dict]:
    """Conversaciones (teléfonos distintos) agrupadas por ``ad_id`` del anuncio.

    Es la lectura que reconcilia "conversaciones que reporta Meta" con lo que
    realmente llegó al bot: si Meta dice N pero aquí no aparece el ``ad_id``,
    el usuario abrió el chat pero nunca escribió. Ordenado de mayor a menor.
    """
    por_anuncio: dict[str, dict] = {}
    for numero, metadata_json in _mensajes_con_origen(session, inicio, fin):
        ad_id = _origen_de_mensaje(metadata_json).get("ad_id")
        if not ad_id:
            continue
        grupo = por_anuncio.setdefault(
            ad_id, {"ad_id": ad_id, "conversaciones": set(), "headline": "", "mensajes": 0}
        )
        grupo["conversaciones"].add(numero)
        grupo["mensajes"] += 1
        if not grupo["headline"]:
            grupo["headline"] = _origen_de_mensaje(metadata_json).get("headline") or ""
    return sorted(
        (
            {
                "ad_id": g["ad_id"],
                "conversaciones": len(g["conversaciones"]),
                "mensajes": g["mensajes"],
                "headline": g["headline"],
            }
            for g in por_anuncio.values()
        ),
        key=lambda r: (-r["conversaciones"], r["ad_id"]),
    )


def numeros_receptores(session, inicio: datetime, fin: datetime) -> list[dict]:
    """Números del negocio por los que entraron mensajes, con sus conversaciones.

    Si aparece un ``phone_number_id`` distinto de ``META_PHONE_NUMBER_ID``, hay
    tráfico entrando por otro número (anuncio o WABA mal configurado) donde el
    bot responde desde el número configurado y el usuario ve otro remitente.
    """
    por_numero: dict[str, set] = {}
    for numero, metadata_json in _mensajes_con_origen(session, inicio, fin):
        phone_number_id = _origen_de_mensaje(metadata_json).get("phone_number_id")
        if not phone_number_id:
            continue
        por_numero.setdefault(phone_number_id, set()).add(numero)
    return sorted(
        ({"phone_number_id": k, "conversaciones": len(v)} for k, v in por_numero.items()),
        key=lambda r: (-r["conversaciones"], r["phone_number_id"]),
    )


def es_bot(user_agent: str | None) -> bool:
    """True si el User-Agent parece de un crawler/bot y no un humano real.

    Los navegadores integrados de redes sociales (FBAV, Instagram, WhatsApp
    in-app) NO deben marcarse: son personas navegando.
    """
    ua = (user_agent or "").lower()
    if not ua:
        return False
    return any(marca in ua for marca in _UA_BOTS)


def parsear_query(query_string: str) -> dict:
    """Extrae los campos UTM + pauta/fuente de la query string de la landing.

    Devuelve ``{fuente, medio, campania, termino, contenido, es_pauta}``. Un
    clic de anuncio de TikTok llega con ``ttclid`` (sin utm_source): cuenta como
    pauta y, si no hay fuente, identifica el origen.
    """
    params = parse_qs(query_string or "", keep_blank_values=True)

    valores = {campo: "" for campo in _CAMPOS_UTM}
    for clave_utm, attr in _CAMPOS_UTM.items():
        raw = params.get(clave_utm, [""])[0]
        valores[attr] = raw[:200]

    ttclid = bool(params.get("ttclid"))
    if ttclid and not valores["fuente"]:
        valores["fuente"] = "tiktok"

    es_pauta = bool(params.get("fbclid")) or ttclid or any(
        v for v in valores.values() if v
    )
    valores["es_pauta"] = es_pauta
    return valores


def registrar_visita_landing(query_string: str, user_agent: str = "") -> None:
    """Registra una carga de la landing en la BD (ignorando errores).

    Captura el User-Agent solo para marcar ``es_bot``; los crawlers y previews
    se guardan marcados para que el dashboard pueda filtrar tráfico no humano.
    """
    try:
        valores = parsear_query(query_string)
        bot = es_bot(user_agent)

        session = SessionLocal()
        try:
            session.add(VisitaLanding(
                fuente=valores["fuente"] or "directo",
                medio=valores["medio"] or None,
                campania=valores["campania"] or None,
                termino=valores["termino"] or None,
                contenido=valores["contenido"] or None,
                es_pauta=valores["es_pauta"],
                es_bot=bot,
                user_agent=(user_agent or "")[:_MAX_UA_LEN],
            ))
            session.commit()
        finally:
            session.close()
    except Exception:
        logger.exception("No se pudo registrar la visita a la landing")


def registrar_clic_whatsapp(query_string: str, ubicacion: str, user_agent: str = "") -> None:
    """Registra un clic en un botón/enlace wa.me (server-side, ignorando errores).

    Solo se registran clics de User-Agents humanos: los robots/previews no
    generan clics medibles hacia WhatsApp.
    """
    try:
        if es_bot(user_agent):
            return
        valores = parsear_query(query_string)

        session = SessionLocal()
        try:
            session.add(ClicWhatsApp(
                fuente=valores["fuente"] or "directo",
                medio=valores["medio"] or None,
                campania=valores["campania"] or None,
                es_pauta=valores["es_pauta"],
                ubicacion=(ubicacion or "")[:100],
                user_agent=(user_agent or "")[:_MAX_UA_LEN],
            ))
            session.commit()
        finally:
            session.close()
    except Exception:
        logger.exception("No se pudo registrar el clic a WhatsApp")