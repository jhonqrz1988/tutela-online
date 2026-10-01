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

    # La fuga grande: números que escribieron pero nunca llegaron a tener una
    # tutela. Ojo con el error fácil aquí: el PRIMER mensaje entrante del
    # usuario llega antes de que exista la tutela (el bot la crea después), así
    # que "tiene algún mensaje con tutela_id NULL" es certainísimo de todos, no
    # solo de los que abandonaron. Lo que distingue a quien se fue es no tener
    # NINGUNA tutela viva.
    telefonos_que_escribieron = {
        tel for (tel,) in session.execute(
            select(distinct(MensajeWhatsApp.from_number))
            .where(MensajeWhatsApp.es_recibido.is_(True), *filtros_msg)
        ).all()
        if tel
    }
    telefonos_con_tutela = {
        tel for (tel,) in session.execute(
            select(distinct(User.telefono)).join(Tutela, Tutela.user_id == User.id)
        ).all()
        if tel
    }
    sin_arrancar = len(telefonos_que_escribieron - telefonos_con_tutela)

    # Perfil de los que escribieron y no arrancaron. Dice si bastó un mensaje y
    # se fueron (nunca entraron al flujo) o si sí conversaron (el bot no los jaló).
    perfil_sin_arrancar = {"con_1_mensaje": 0, "con_2_o_3": 0, "con_4_mas": 0,
                           "con_audio": 0, "mensajes_totales": 0}
    sin_arrancar_tels = telefonos_que_escribieron - telefonos_con_tutela
    if sin_arrancar_tels:
        conteo = session.execute(
            select(MensajeWhatsApp.from_number, func.count()).where(
                MensajeWhatsApp.es_recibido.is_(True),
                MensajeWhatsApp.from_number.in_(sin_arrancar_tels),
            ).group_by(MensajeWhatsApp.from_number)
        ).all()
        for _tel, n in conteo:
            perfil_sin_arrancar["mensajes_totales"] += n
            if n == 1:
                perfil_sin_arrancar["con_1_mensaje"] += 1
            elif n <= 3:
                perfil_sin_arrancar["con_2_o_3"] += 1
            else:
                perfil_sin_arrancar["con_4_mas"] += 1
        con_audio = session.execute(
            select(func.count(distinct(MensajeWhatsApp.from_number))).where(
                MensajeWhatsApp.es_recibido.is_(True),
                MensajeWhatsApp.from_number.in_(sin_arrancar_tels),
                MensajeWhatsApp.tipo_mensaje == "audio",
            )
        ).scalar() or 0
        perfil_sin_arrancar["con_audio"] = con_audio

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

    # Fuga entre etapas consecutivas del alcance. Se muestra aunque los números no
    # sean monótonos (escribir > consentimiento es normal: muchos escribe una vez
    # y se van antes de consentir).
    orden_alcance = [
        ("visitas_landing_humanas", "Visitó la landing"),
        ("clics_whatsapp", "Clic en el botón de WhatsApp"),
        ("numeros_que_escribieron", "Escribió al bot"),
        ("usuarios_con_consentimiento", "Aceptó y se identificó"),
        ("tutelas_creadas", "Inició una tutela"),
    ]
    fugas = []
    valores = {
        "visitas_landing_humanas": visitas,
        "clics_whatsapp": clics,
        "numeros_que_escribieron": escritores,
        "usuarios_con_consentimiento": usuarios,
        "tutelas_creadas": n_tutelas,
    }
    for (ca, la), (cb, lb) in zip(orden_alcance, orden_alcance[1:], strict=False):
        a, b = valores[ca], valores[cb]
        fugas.append({
            "de": la, "a": lb, "de_n": a, "a_n": b,
            "perdidos": max(0, a - b),
            "pct_pasa": round(100.0 * b / a, 1) if a else None,
        })

    return {
        "ok": True,
        "dias": dias,
        "alcance": {
            "visitas_landing_humanas": visitas,
            "clics_whatsapp": clics,
            "numeros_que_escribieron": escritores,
            "usuarios_con_consentimiento": usuarios,
            "tutelas_creadas": n_tutelas,
            "escribieron_sin_arrancar": sin_arrancar,
        },
        "perfil_sin_arrancar": perfil_sin_arrancar,
        "fugas": fugas,
        "etapas": etapas,
        "mayor_caida": mayor_caida,
        "por_estado": dict(sorted(por_estado.items(), key=lambda kv: -kv[1])),
        "etiquetas_estado": {
            e: ETIQUETAS_ESTADO[e] for e in por_estado if e in ETIQUETAS_ESTADO
        },
        "notas": [
            "No hay historial de estados: 'alcanzó el paso' se infiere del estado actual.",
            "Los que abandonaron no se pueden ubicar en un paso concreto.",
            "Visitas y clics son EVENTOS (cada recarga cuenta), no personas: por eso "
            "el porcentaje entre ellos no es una tasa de conversión de personas.",
            "tutelas_creadas cuenta tutelas, no personas: un usuario puede tener varias.",
        ],
    }
