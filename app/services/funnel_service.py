"""Embudo de conversión: dónde se cae la gente y por qué.

CONTEXTO. No había ninguna métrica de embudo. El panel solo daba
total/radicadas/pendientes/fallidas del mes activo, y el reporte de entrega
cuentaba respuestas de Meta. Ninguna de las dos respondía "¿en qué paso dejo de
convertirse la gente?", que es la pregunta que sí tiene una acción detrás.

CEJAS DE ESTE EMBUDO (importan al leer los números):

1. NO hay historial de estados. ``Tutela.estado`` guarda solo el estado actual,
   no la trayectoria. Por eso "alcanzó el paso N" se infiere del estado actual
   (la máquina es lineal: quien llegó a esperar el pago pasó por los datos
   personales), y no se puede afirmar cuántas veces pasó por un paso ni cuándo.
   Es una fotografía del presente, no una trajectory.

2. Los reinicios desaparecen. Cuando el usuario escribe "salir", ``_reiniciar_
   flujo`` borra la tutela. Queda el ``User`` y los mensajes con ``tutela_id``
   NULL, así que se pueden contar como "empezó y abandonó", pero NO se puede
   saber en qué paso estaba cuando lo hizo.

3. Los envíos a Meta no decidían nada aquí: es una foto de conteos, no de
   personas, así que el embudo cuenta personas distintas por etapa.
"""
from __future__ import annotations

from sqlalchemy import distinct, func, select

from app.models.clic import ClicWhatsApp
from app.models.tutela import Tutela
from app.models.user import User
from app.models.visita import VisitaLanding
from app.models.whatsapp import MensajeWhatsApp

# Escala del flujo en el orden en que la recorre el usuario. Los estados que no
# están aquí (radicacion en curso, fallidas) se cuentan aparte como "fuera".
FUNIL_ESCALERA: list[tuple[str, str, list[str]]] = [
    ("inicio", "Entró al bot", ["borrador"]),
    ("datos", "Mandó sus datos personales", ["recogiendo_datos"]),
    ("confirmacion_datos", "Confirmó sus datos", [
        "confirmar_datos_personales", "corrigiendo_datos_personales",
    ]),
    ("narracion", "Contó su caso", ["narracion"]),
    ("revision", "Revisó lo que entendió", ["confirmar_audio", "revision_datos"]),
    ("clinico", "Respondió los datos clínicos", ["preguntas_clinicas"]),
    ("pruebas", "Mandó las pruebas", ["pruebas_pendiente", "recibiendo_pruebas"]),
    ("listo", "Quedó lista para radicar", ["datos_listos", "pdf_generado"]),
    ("pago", "Pasó a la parte del pago", [
        "esperando_decision_radicacion", "hazlo_tu_mismo", "confirmar_pago",
        "esperando_pago", "pago_por_confirmar", "pago_confirmado",
        "pendiente_radicacion",
    ]),
    ("radicada", "Quedó radicada", ["radicada", "completado"]),
]

_ESTADO_A_ETAPA: dict[str, int] = {}
for _i, (_clave, _etiqueta, _estados) in enumerate(FUNIL_ESCALERA):
    for _e in _estados:
        _ESTADO_A_ETAPA[_e] = _i

ETIQUETAS_ESTADO: dict[str, str] = {
    "esperando_codigo_email": "Esperando el código del portal",
    "fallida": "Falló la radicación",
    "token_fallido": "Falló la radicación",
}


def etapa_de_estado(estado: str | None) -> int | None:
    """Índice de la etapa que representa este estado, o None si está fuera."""
    if not estado:
        return None
    return _ESTADO_A_ETAPA.get(estado)


def funnel(session, dias: int | None = None) -> dict:
    """Embudo completo. ``dias=None`` es histórico total."""
    filtros_visita, filtros_clic, filtros_msg, filtros_tutela = [], [], [], []

    if dias:
        import datetime as _dt

        desde = _dt.datetime.now(_dt.UTC).replace(tzinfo=None) - _dt.timedelta(days=dias)
        filtros_visita = [VisitaLanding.created_at >= desde]
        filtros_clic = [ClicWhatsApp.created_at >= desde]
        filtros_msg = [MensajeWhatsApp.created_at >= desde]
        filtros_tutela = [Tutela.created_at >= desde]

    visitas = session.execute(
        select(func.count()).select_from(VisitaLanding)
        .where(VisitaLanding.es_bot.is_(False), *filtros_visita)
    ).scalar() or 0
    clics = session.execute(
        select(func.count()).select_from(ClicWhatsApp).where(*filtros_clic)
    ).scalar() or 0

    # Números que escribieron de verdad (es_recibido=True), no nuestros envíos.
    escritores = session.execute(
        select(func.count(distinct(MensajeWhatsApp.from_number)))
        .where(MensajeWhatsApp.es_recibido.is_(True), *filtros_msg)
    ).scalar() or 0

    # Usuario con consentimiento = llegó a identificarse en el bot.
    usuarios = session.execute(
        select(func.count(distinct(User.id))).where(
            User.consentimiento.is_(True), User.telefono.is_not(None),
        )
    ).scalar() or 0

    tutelas = session.execute(
        select(Tutela).where(*filtros_tutela)
    ).scalars().all()

    # Huella del abandono: escribió, no tiene tutela viva, y sus mensajes
    # quedaron sin tutela (tutela_id NULL). Son los que entraron y se fueron
    # (salir / reinicio / el flow se borró).
    huerfanos = 0
    filas_msg = session.execute(
        select(distinct(MensajeWhatsApp.from_number), MensajeWhatsApp.tutela_id)
        .where(MensajeWhatsApp.es_recibido.is_(True), *filtros_msg)
    ).all()
    telefonos_huerfanos = {
        tel for tel, tid in filas_msg if tid is None
    }
    if telefonos_huerfanos:
        huerfanos = session.execute(
            select(func.count(distinct(User.id)))
            .where(User.telefono.in_(telefonos_huerfanos))
        ).scalar() or 0

    n_tutelas = len(tutelas)
    etapas = []
    for i, (clave, etiqueta, _estados) in enumerate(FUNIL_ESCALERA):
        # "Alcanzó esta etapa" = su estado actual está en este peldaño o superior.
        n = sum(1 for t in tutelas if (etapa_de_estado(t.estado) or -1) >= i)
        etapas.append({
            "clave": clave,
            "etiqueta": etiqueta,
            "n": n,
            "pct_del_inicio": round(100.0 * n / n_tutelas, 1) if n_tutelas else 0.0,
        })

    por_estado: dict[str, int] = {}
    for t in tutelas:
        por_estado[t.estado or "sin_estado"] = por_estado.get(t.estado or "sin_estado", 0) + 1

    # La fuga más grande es entre dos peldaños: se calcula acá.
    mayor_caida = None
    for a, b in zip(etapas, etapas[1:], strict=False):
        perdida = a["n"] - b["n"]
        if b["n"] > 0 and (mayor_caida is None or perdida > mayor_caida["perdidos"]):
            mayor_caida = {
                "de": a["etiqueta"], "a": b["etiqueta"],
                "perdidos": perdida,
                "pct": round(100.0 * perdida / a["n"], 1) if a["n"] else 0.0,
            }

    return {
        "ok": True,
        "dias": dias,
        "alcance": {
            "visitas_landing_humanas": visitas,
            "clics_whatsapp": clics,
            "numeros_que_escribieron": escritores,
            "usuarios_con_consentimiento": usuarios,
            "tutelas_creadas": n_tutelas,
            "entraron_y_salieron_sin_tutela": huerfanos,
        },
        "etapas": etapas,
        "mayor_caida": mayor_caida,
        "por_estado": dict(sorted(por_estado.items(), key=lambda kv: -kv[1])),
        "etiquetas_estado": {
            e: ETIQUETAS_ESTADO[e] for e in por_estado if e in ETIQUETAS_ESTADO
        },
        "notas": [
            "No hay historial de estados: 'alcanzó el paso' se infiere del estado actual.",
            "Los que abandonaron no se pueden ubicar en un paso concreto.",
        ],
    }
