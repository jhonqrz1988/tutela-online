import asyncio
import json
import logging
import threading
from datetime import datetime, timedelta

from sqlalchemy import select, update

from app.bot.navegador import RadicadorBot
from app.database import SessionLocal
from app.models.radicacion import PasoRadicacion, Radicacion
from app.models.tutela import Tutela
from app.services.whatsapp_service import enviar_texto, enviar_imagen

logger = logging.getLogger(__name__)

# Instancia global del bot (se reutiliza entre llamadas)
_bot: RadicadorBot | None = None

# Si la radicación queda en 'continuando' más de este tiempo sin terminar,
# se considera colgada (navegador muerto/portal no respondió): el código de
# la Rama Judicial vence a los 10 min, así que el umbral debe ser menor para
# que el reenvío del usuario alcance a entrar dentro de la validez.
UMBRAL_CONTINUANDO_ESTANCADO = timedelta(minutes=5)

# Estados en los que el navegador de Playwright está físicamente trabajando
# sobre la radicación. No debe arrancar una segunda instancia (ni programarla)
# mientras esté en alguno de estos: el portal es sesión única por navegador.
ESTADOS_RADICACION_EN_CURSO = ("iniciando", "continuando", "completando_formulario", "resolviendo_captcha", "enviando")


def _descolgar_continuando_estancado(session, tutela_id: int):
    """Si la radicación lleva demasiado tiempo en 'continuando', la declara
    colgada pasando a 'fallida' para que el claim del código vuelva a
    funcionar (reenvío del usuario o reintento desde el admin)."""
    corte = datetime.utcnow() - UMBRAL_CONTINUANDO_ESTANCADO
    actualizado = session.execute(
        update(Radicacion)
        .where(
            Radicacion.tutela_id == tutela_id,
            Radicacion.estado == "continuando",
            Radicacion.updated_at < corte,
        )
        .values(estado="fallida", ultimo_error="Radicación colgada en 'continuando' (código vencido o navegador sin respuesta)")
    )
    session.commit()
    return actualizado.rowcount, None


def _registrar_paso(session, rad_id: int, paso: str, estado: str, detalle: str = ""):
    """Persiste un paso del monitoreo de radicación."""
    try:
        session.add(PasoRadicacion(radicacion_id=rad_id, paso=paso, estado=estado, detalle=detalle or None))
        session.commit()
    except Exception as e:  # noqa: BLE001 - el monitoreo nunca debe romper el flujo
        logger.warning(f"No se pudo registrar paso {paso}: {e}")


def _get_bot() -> RadicadorBot:
    global _bot
    if _bot is None:
        _bot = RadicadorBot()
    return _bot


def programar_radicacion_inmediata(tutela_id: int) -> dict:
    """Arranca la radicación en segundo plano apenas se confirma el pago.

    El webhook de Mercado Pago no debe bloquearse esperando a Playwright:
    por eso la radicación corre en su propio hilo con event loop propio.
    No lanza una segunda instancia si ya hay una radicación en curso
    (iniciando/continuando/completando...): el portal es sesión única, abrir
    otro navegador a la vez duplicaría la solicitud en el portal.

    Retorna {"ok": True} si se programó, o {"ok": False} con la razón.
    """
    session = SessionLocal()
    try:
        rad = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela_id)
        ).scalar_one_or_none()
        if rad and rad.estado in ESTADOS_RADICACION_EN_CURSO:
            return {"ok": False, "error": "Ya hay una radicación en curso para esta tutela"}
    finally:
        session.close()

    def _correr():
        try:
            asyncio.run(iniciar_radicacion(tutela_id))
        except Exception as e:  # noqa: BLE001 - el hilo nunca debe romper el webhook
            logger.error(f"Error en radicación inmediata de tutela {tutela_id}: {e}")

    hilo = threading.Thread(
        target=_correr,
        name=f"radicacion-inmediata-{tutela_id}",
        daemon=True,
    )
    hilo.start()
    logger.info(f"Radicación inmediata programada para tutela {tutela_id}")
    return {"ok": True}


async def iniciar_radicacion(
    tutela_id: int,
    token_usuario: str | None = None,
    forzar: bool = False,
) -> dict:
    """Inicia la radicación de una tutela en el portal de Rama Judicial.

    Flujo:
    1. Abre Playwright → navega al portal
    2. Llena el formulario (pasos 1-4)
    3. Si el portal pide código de email → pausa, envía WhatsApp al usuario
    4. Retorna estado pendiente para que el webhook espere el código

    Args:
        forzar: Si True, ignora restricción de horario hábil (para admin manual).
    """
    session = SessionLocal()
    try:
        tutela = session.execute(
            select(Tutela).where(Tutela.id == tutela_id)
        ).scalar_one_or_none()
        if not tutela:
            return {"ok": False, "error": "Tutela no encontrada"}

        # Verificar horario hábil (solo si no está forzado)
        if not forzar:
            from app.tasks.jobs import es_horario_habil
            if not es_horario_habil():
                return {"ok": False, "error": "Fuera de horario hábil (8am-12pm, 2pm-4pm). Use forzar=True desde admin."}

        datos = json.loads(tutela.datos_json or "{}")

        # Crear/actualizar registro de radicación
        rad = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela_id)
        ).scalar_one_or_none()
        if not rad:
            rad = Radicacion(tutela_id=tutela.id, estado="iniciando")
            session.add(rad)
        else:
            rad.estado = "iniciando"
        session.commit()

        bot = _get_bot()
        bot.on_paso = lambda paso, estado, detalle="": _registrar_paso(session, rad.id, paso, estado, detalle)

        # Paso 1: Iniciar navegador y navegar al portal
        await bot.iniciar()
        _registrar_paso(session, rad.id, "iniciar_bot", "ok")
        await bot.navegar_portal()
        _registrar_paso(session, rad.id, "navegar_portal", "ok")

        # Paso 2: Llenar formulario (pasos 1-4, hasta verificación email)
        resultado = await bot.llenar_formulario(datos)

        if not resultado.get("ok"):
            rad.estado = "fallida"
            rad.ultimo_error = resultado.get("error", "Error desconocido en llenado")
            _registrar_paso(session, rad.id, "llenar_formulario", "error", rad.ultimo_error)
            session.commit()
            await bot.cerrar()
            return {"ok": False, "error": resultado.get("error")}

        _registrar_paso(session, rad.id, "llenar_formulario", "ok")

        # Si requiere código de email → pausar y notificar al usuario
        if resultado.get("requiere_codigo_email"):
            rad.estado = "esperando_codigo_email"
            tutela.estado = "esperando_codigo_email"
            session.commit()

            # Guardar referencia al bot en la BD (para retomar después)
            datos["radicacion_bot_active"] = True
            tutela.datos_json = json.dumps(datos)
            session.commit()

            # Enviar WhatsApp al usuario
            if tutela.user and tutela.user.telefono:
                enviar_texto(
                    tutela.user.telefono,
                    "Hola, para que nuestro equipo pueda finalizar tu trámite, "
                    "necesitamos que nos proporciones el dato que el portal oficial "
                    "te envió por correo electrónico. Por favor, escríbelo aquí abajo "
                    "para continuar. ¡Gracias por tu colaboración!"
                )

            logger.info(f"Radicación tutela {tutela_id}: esperando código de email")
            _registrar_paso(session, rad.id, "esperando_codigo_email", "ok")
            return {"ok": True, "esperando_codigo": True, "radicacion_id": rad.id}

        # Si no requiere código → continuar con pasos 5-10
        await _completar_radicacion(bot, tutela, datos, rad, session)

        return {"ok": True, "completado": True}

    except Exception as e:
        logger.error(f"Error iniciando radicación tutela {tutela_id}: {e}")
        try:
            rad = session.execute(
                select(Radicacion).where(Radicacion.tutela_id == tutela_id)
            ).scalar_one_or_none()
            if rad:
                rad.estado = "fallida"
                rad.ultimo_error = str(e)[:500]
                _registrar_paso(session, rad.id, "iniciar_radicacion", "error", str(e)[:500])
                session.commit()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    finally:
        session.close()


async def continuar_radicacion_con_codigo(tutela_id: int, codigo: str) -> dict:
    """Retoma la radicación después de recibir el código de verificación de email.

    Llamado por webhook_whatsapp.py cuando el usuario envía el código.
    """
    session = SessionLocal()
    try:
        tutela = session.execute(
            select(Tutela).where(Tutela.id == tutela_id)
        ).scalar_one_or_none()
        if not tutela:
            return {"ok": False, "error": "Tutela no encontrada"}

        rad = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela_id)
        ).scalar_one_or_none()
        if not rad or rad.estado not in ("esperando_codigo_email", "fallida", "continuando"):
            return {"ok": False, "error": "Esta tutela no está esperando código de email"}

        # Si quedó 'continuando' colgada más del umbral (código vencido a los
        # 10 min o navegador sin respuesta), se desestanca a 'fallida': el
        # reenvío del código vuelve a reclamar la radicación en lugar de
        # responder "ya se está procesando" para siempre.
        _descolgar_continuando_estancado(session, tutela_id)
        rad = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela_id)
        ).scalar_one_or_none()

        # Claim atómico: solo un intento (de los mensajes que llegan a la vez)
        # procesa el código; el resto responde sin tocar el navegador.
        claimed = session.execute(
            update(Radicacion)
            .where(Radicacion.id == rad.id, Radicacion.estado.in_(["esperando_codigo_email", "fallida"]))
            .values(estado="continuando")
        )
        session.commit()
        if claimed.rowcount != 1:
            return {"ok": False, "error": "El código ya se está procesando. Espera un momento."}

        datos = json.loads(tutela.datos_json or "{}")
        bot = _get_bot()
        if bot.page is None or bot.page.is_closed():
            rad.estado = "fallida"
            rad.ultimo_error = "Navegador no disponible (posible reinicio del servidor)"
            session.commit()
            return {"ok": False, "error": "El navegador no está disponible. Reintenta desde el panel admin."}

        # Ingresar código de verificación (con timeout para no colgar el webhook)
        try:
            resultado = await asyncio.wait_for(bot.ingresar_codigo_email(codigo), timeout=120)
        except asyncio.TimeoutError:
            rad.estado = "fallida"
            rad.ultimo_error = "Timeout ingresando el código de email"
            session.commit()
            logger.error(f"Timeout ingresando código de email para tutela {tutela_id}")
            return {"ok": False, "error": "No se pudo aplicar el código a tiempo. Intenta de nuevo."}

        if resultado is not None and not resultado.get("ok"):
            rad.estado = "fallida"
            rad.ultimo_error = resultado.get("error", "Error ingresando el código de email")
            session.commit()
            logger.error(f"Error ingresando código de email para tutela {tutela_id}: {resultado.get('error')}")
            return {"ok": False, "error": resultado.get("error", "No se pudo ingresar el código")}
        logger.info(f"Código de email ingresado para tutela {tutela_id}")

        # Completar pasos restantes (5-10) con timeout de seguridad
        try:
            await asyncio.wait_for(
                _completar_radicacion(bot, tutela, datos, rad, session), timeout=360
            )
        except asyncio.TimeoutError:
            rad.estado = "fallida"
            rad.ultimo_error = "Timeout completando la radicación"
            session.commit()
            logger.error(f"Timeout completando radicación tutela {tutela_id}")
            return {"ok": False, "error": "La radicación se demoró demasiado. Reintenta desde el panel admin."}

        return {"ok": True}

    except Exception as e:
        logger.error(f"Error continuando radicación tutela {tutela_id}: {e}")
        try:
            rad = session.execute(
                select(Radicacion).where(Radicacion.tutela_id == tutela_id)
            ).scalar_one_or_none()
            if rad:
                rad.estado = "fallida"
                rad.ultimo_error = str(e)[:500]
                session.commit()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    finally:
        session.close()


def descolgar_radicaciones_estancadas(umbral=UMBRAL_CONTINUANDO_ESTANCADO) -> list[dict]:
    """Watchdog: marca como 'fallida' las radicaciones colgadas en 'continuando'.

    Si el navegador de Playwright se queda pegado (sin respetar el timeout),
    la radicación queda en 'continuando' para siempre. Este barrido la pasa a
    'fallida' para que el admin pueda reintentarla o el usuario reenviar el
    código. Retorna el detalle de las radicaciones descolgadas.
    """
    corte = datetime.utcnow() - umbral
    descolgadas: list[dict] = []
    session = SessionLocal()
    try:
        estancadas = session.execute(
            select(Radicacion).where(
                Radicacion.estado == "continuando",
                Radicacion.updated_at < corte,
            )
        ).scalars().all()
        for rad in estancadas:
            rad.estado = "fallida"
            rad.ultimo_error = "Radicación colgada en 'continuando' (watchdog)"
            session.commit()
            descolgadas.append({"radicacion_id": rad.id, "tutela_id": rad.tutela_id})
            logger.warning(f"Watchdog: radicación {rad.id} (tutela {rad.tutela_id}) colgada en 'continuando' → fallida")
        return descolgadas
    finally:
        session.close()


async def _completar_radicacion(bot, tutela, datos, rad, session):
    """Completa los pasos 5-10 de la radicación y notifica al usuario."""
    try:
        # Pasos 5-8: accionado, derechos, archivos, juramento
        rad.estado = "completando_formulario"
        session.commit()
        resultado = await bot.completar_post_codigo(datos, tutela.pdf_path)

        if not resultado.get("ok"):
            rad.estado = "fallida"
            rad.ultimo_error = resultado.get("error", "Error completando formulario")
            _registrar_paso(session, rad.id, "completar_formulario", "error", rad.ultimo_error)
            session.commit()
            await bot.cerrar()
            return

        _registrar_paso(session, rad.id, "completar_formulario", "ok")

        # Paso 9: Resolver reCAPTCHA
        rad.estado = "resolviendo_captcha"
        session.commit()
        captcha_ok = await bot.resolver_recaptcha()

        if not captcha_ok:
            rad.estado = "fallida"
            rad.ultimo_error = "No se pudo resolver el reCAPTCHA"
            rad.intentos = (rad.intentos or 0) + 1
            _registrar_paso(session, rad.id, "resolver_captcha", "error", rad.ultimo_error)
            session.commit()
            await bot.cerrar()
            return

        _registrar_paso(session, rad.id, "resolver_captcha", "ok")
        logger.info(f"reCAPTCHA resuelto para tutela {tutela.id}")

        # Paso 10: Enviar y descargar constancia
        rad.estado = "enviando"
        session.commit()
        resultado_envio = await bot.enviar_y_descargar()

        if resultado_envio.get("error"):
            rad.estado = "fallida"
            rad.ultimo_error = resultado_envio["error"]
            rad.intentos = (rad.intentos or 0) + 1
            _registrar_paso(session, rad.id, "enviar_y_descargar", "error", rad.ultimo_error)
            session.commit()
            await bot.cerrar()
            return

        _registrar_paso(session, rad.id, "enviar_y_descargar", "ok")

        # Extraer número de radicado
        num_radicado = resultado_envio.get("num_radicado", "")
        rad.num_radicado = num_radicado
        rad.constancia_path = resultado_envio.get("path")
        rad.estado = "radicada"
        rad.intentos = (rad.intentos or 0) + 1
        _registrar_paso(session, rad.id, "radicada", "ok", num_radicado)
        session.commit()

        # Screenshot de confirmación
        screenshot_path = await bot.tomar_screenshot(f"constancia_{tutela.id}")

        # Actualizar tutela
        tutela.estado = "radicada"
        session.commit()

        # Notificar al usuario por WhatsApp
        if tutela.user and tutela.user.telefono:
            enviar_texto(
                tutela.user.telefono,
                f"✅ *Tu solicitud ha sido procesada exitosamente.*\n\n"
                f"Número de seguimiento: *{num_radicado}*\n\n"
                "Puedes consultar las actualizaciones directamente en este chat."
            )
            # Enviar screenshot de la constancia
            if screenshot_path and screenshot_path.exists():
                enviar_imagen(tutela.user.telefono, str(screenshot_path))

        logger.info(f"Radicación tutela {tutela.id} completada. Radicado: {num_radicado}")

    except Exception as e:
        logger.error(f"Error en _completar_radicacion tutela {tutela.id}: {e}")
        rad.estado = "fallida"
        rad.ultimo_error = str(e)[:500]
        _registrar_paso(session, rad.id, "completar_radicacion", "error", str(e)[:500])
        session.commit()
    finally:
        await bot.cerrar()
