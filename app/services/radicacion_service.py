import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import select

from app.bot.navegador import RadicadorBot
from app.database import SessionLocal
from app.models.radicacion import PasoRadicacion, Radicacion
from app.models.tutela import Tutela
from app.services.whatsapp_service import enviar_texto, enviar_imagen

logger = logging.getLogger(__name__)

# Instancia global del bot (se reutiliza entre llamadas)
_bot: RadicadorBot | None = None

# Loop persistente dueño del navegador. TODO el trabajo de Playwright corre
# sobre este loop (hilo de fondo). Cuando `iniciar_radicacion` se pausa en la
# verificación de email, la coroutine se PARQUEA aquí: el loop sigue vivo, el
# navegador sigue vivo, y el webhook lo despierta con call_soon_threadsafe.
# Antes cada llamada corría `asyncio.run(iniciar_radicacion(...))`: al parquear
# el loop moría, Playwright quedaba 'vivo aparente' pero muerto, y el código
# del usuario llegaba a un navegador sin respuesta → 'continuando' para siempre.
_loop_hogar: asyncio.AbstractEventLoop | None = None
_loop_hogar_lock = threading.Lock()

# Parqueos: {tutela_id: (loop, evento, tarea)}. `continuar_radicacion_con_codigo`
# los usa para señalar al coroutine parkeado que el código ya llegó.
_parqueos: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Event, asyncio.Task]] = {}
# Códigos a la espera de que el coroutine despierto los consuma (write-before-signal).
_codigos_pendientes: dict[int, str] = {}
_parqueos_lock = threading.Lock()

# Si la radicación queda en 'continuando' más de este tiempo sin terminar,
# se considera colgada (navegador muerto/portal no respondió): el código de
# la Rama Judicial vence a los 10 min, así que el umbral debe ser menor para
# que el reenvío del usuario alcance a entrar dentro de la validez.
UMBRAL_CONTINUANDO_ESTANCADO = timedelta(minutes=5)

# Un parqueo 'esperando_codigo_email' más viejo que esto significa que el
# proceso se reinició (el coroutine en memoria se perdió) o el usuario no
# respondió a tiempo. El código vence a los 10 min: 12 min dan margen a que
# el timeout interno TIMEOUT_ESPERA_CODIGO (600s) sea quien lo abandone primero.
UMBRAL_ESPERANDO_CODIGO = timedelta(minutes=12)

# Tiempo (segundos) que el coroutine parkeado espera el código de email antes
# de abandonarse solo: cierra el navegador, marca 'fallida' y avisa.
TIMEOUT_ESPERA_CODIGO = 600

# Estados en los que el navegador de Playwright está físicamente trabajando
# sobre la radicación. No debe arrancar una segunda instancia (ni programarla)
# mientras esté en alguno de estos: el portal es sesión única por navegador.
ESTADOS_RADICACION_EN_CURSO = ("iniciando", "continuando", "completando_formulario", "resolviendo_captcha", "enviando")


def _correr_loop_hogar(loop: asyncio.AbstractEventLoop):
    """Mantiene vivo el loop del bot (hilo de fondo)."""
    try:
        asyncio.set_event_loop(loop)
        loop.run_forever()
    except Exception as e:  # noqa: BLE001 - el loop del bot nunca debe romper a quien lo invoca
        logger.error(f"El loop persistente del bot terminó: {e}")


def _get_loop_hogar() -> asyncio.AbstractEventLoop:
    """Devuelve (o crea) el loop persistente del navegador."""
    global _loop_hogar
    with _loop_hogar_lock:
        if _loop_hogar is None or _loop_hogar.is_closed():
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=_correr_loop_hogar,
                args=(loop,),
                name="loop-radicacion-bot",
                daemon=True,
            ).start()
            _loop_hogar = loop
            limite = time.monotonic() + 5
            while not loop.is_running():
                if time.monotonic() > limite:
                    raise RuntimeError("El loop persistente del bot no pudo arrancar")
                time.sleep(0.001)
    return _loop_hogar


def despachar_radicacion(tutela_id: int, *, forzar: bool = False) -> dict:
    """Despacha la radicación al loop persistente del bot y retorna al instante.

    Todos los callers (admin, API, scheduler, pago) pasan por aquí en lugar de
    `asyncio.run(iniciar_radicacion(...))`: así la coroutine que espera el
    código de email vive en un loop que NO muere, y el navegador sobrevive a la
    pausa. Si ya hay un parqueo vivo para la tutela, no arranca una segunda
    instancia del navegador: responde 'ya_en_espera'.
    """
    with _parqueos_lock:
        if tutela_id in _parqueos:
            return {"ok": True, "ya_en_espera": True, "despachada": False}
    loop = _get_loop_hogar()
    envio = asyncio.run_coroutine_threadsafe(iniciar_radicacion(tutela_id, forzar=forzar), loop)
    envio.add_done_callback(lambda f: _registrar_resultado_despacho(tutela_id, f))
    return {"ok": True, "despachada": True}


def _registrar_resultado_despacho(tutela_id: int, envio):
    """Registra el resultado de una radicación despachada (nadie la espera)."""
    try:
        res = envio.result()
        if not res.get("ok"):
            logger.warning(f"Radicación de tutela {tutela_id} terminó con error: {res.get('error')}")
    except asyncio.CancelledError:
        pass
    except Exception as e:  # noqa: BLE001 - registro, nunca rompe flujo
        logger.error(f"Error en radicación de tutela {tutela_id}: {e}")


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
    """Arranca la radicación apenas se confirma el pago.

    El webhook de Mercado Pago no debe bloquearse esperando a Playwright: por
    eso se despacha al loop persistente del bot y se retorna al instante.
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

    logger.info(f"Radicación inmediata programada para tutela {tutela_id}")
    return despachar_radicacion(tutela_id)


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

        # Si requiere código de email → PARQUEAR hasta que el usuario lo envíe.
        # El coroutine espera un asyncio.Event sobre el loop persistente del
        # bot; `continuar_radicacion_con_codigo` lo despierta con
        # call_soon_threadsafe (sin tocar el navegador ni crear hilos).
        if resultado.get("requiere_codigo_email"):
            rad.estado = "esperando_codigo_email"
            tutela.estado = "esperando_codigo_email"
            session.commit()

            # Enviar WhatsApp al usuario
            try:
                if tutela.user and tutela.user.telefono:
                    enviar_texto(
                        tutela.user.telefono,
                        "Hola, para que nuestro equipo pueda finalizar tu trámite, "
                        "necesitamos que nos proporciones el dato que el portal oficial "
                        "te envió por correo electrónico. Por favor, escríbelo aquí abajo "
                        "para continuar. ¡Gracias por tu colaboración!"
                    )
            except Exception as e:  # noqa: BLE001 - el aviso nunca debe romper el parqueo
                logger.warning(f"No se pudo avisar el código a tutela {tutela_id}: {e}")

            logger.info(f"Radicación tutela {tutela_id}: esperando código de email")
            _registrar_paso(session, rad.id, "esperando_codigo_email", "ok")

            # Registrar el parqueo: el coroutine vive en el loop persistente,
            # su espera es asyncio puro (cancela limpiamente) y se abandona
            # solo si el código no llega en TIMEOUT_ESPERA_CODIGO.
            loop = asyncio.get_running_loop()
            evento = asyncio.Event()
            tarea = asyncio.current_task()
            with _parqueos_lock:
                _codigos_pendientes.pop(tutela_id, None)  # descartar códigos huérfanos previos
                _parqueos[tutela_id] = (loop, evento, tarea)

            try:
                await asyncio.wait_for(evento.wait(), timeout=TIMEOUT_ESPERA_CODIGO)
            except asyncio.TimeoutError:
                with _parqueos_lock:
                    if _parqueos.get(tutela_id, (None, None, None))[2] is tarea:
                        _parqueos.pop(tutela_id, None)
                    _codigos_pendientes.pop(tutela_id, None)
                await _fallar_y_avisar(
                    bot,
                    tutela,
                    rad,
                    session,
                    "Código de email no recibido dentro de la validez (10 min). Reintenta la radicación.",
                    "⚠️ El código de email expiró antes de que llegara. Envíanoslo de nuevo o reinicia la radicación para intentarlo otra vez.",
                )
                return {"ok": False, "error": "El código de email no llegó a tiempo. Reintenta la radicación."}
            except asyncio.CancelledError:
                with _parqueos_lock:
                    if _parqueos.get(tutela_id, (None, None, None))[2] is tarea:
                        _parqueos.pop(tutela_id, None)
                    _codigos_pendientes.pop(tutela_id, None)
                await _cerrar_bot(bot)
                logger.warning(f"Parqueo de tutela {tutela_id} cancelado; cierre del navegador")
                raise

            # Despertó: consumir el código señalado y liberar el parqueo.
            codigo = None
            with _parqueos_lock:
                if _parqueos.get(tutela_id, (None, None, None))[2] is tarea:
                    _parqueos.pop(tutela_id, None)
                codigo = _codigos_pendientes.pop(tutela_id, None)
            if codigo is None:
                await _fallar_y_avisar(
                    bot,
                    tutela,
                    rad,
                    session,
                    "Código de email no encontrado al despertar el parqueo",
                    "⚠️ No pudimos recuperar tu código. Reinicia la radicación para intentarlo de nuevo.",
                )
                return {"ok": False, "error": "Código no disponible. Reintenta la radicación."}

            rad.estado = "continuando"
            session.commit()

            # Ping: el navegador pudo quedar 'vivo aparente' pero sin responder
            # tras la espera; NO se escribe sobre una página muerta.
            try:
                vivo = bool(await asyncio.wait_for(bot.verificar_conexion(), timeout=10))
            except Exception:  # noqa: BLE001 - timeout o fallo del ping = no está vivo
                vivo = False
            if not vivo:
                await _fallar_y_avisar(
                    bot,
                    tutela,
                    rad,
                    session,
                    "Navegador sin responder al recibir el código. Reintenta la radicación.",
                    "⚠️ El navegador del portal no respondió al recibir tu código. Reinicia la radicación para intentarlo de nuevo.",
                )
                return {"ok": False, "error": "Navegador sin responder. Reintenta desde el panel admin."}

            # Ingresar código de verificación con timeout acotado.
            try:
                resultado = await asyncio.wait_for(bot.ingresar_codigo_email(codigo), timeout=120)
            except asyncio.TimeoutError:
                await _fallar_y_avisar(
                    bot,
                    tutela,
                    rad,
                    session,
                    "Timeout ingresando el código de email",
                    "⚠️ No pudimos aplicar tu código a tiempo. Reinicia la radicación para intentarlo de nuevo.",
                )
                return {"ok": False, "error": "No se pudo aplicar el código a tiempo. Intenta de nuevo."}

            if resultado is not None and not resultado.get("ok"):
                await _fallar_y_avisar(
                    bot,
                    tutela,
                    rad,
                    session,
                    resultado.get("error", "Error ingresando el código de email"),
                    "⚠️ No se pudo ingresar tu código en el portal. Reinicia la radicación para intentarlo de nuevo.",
                )
                return {"ok": False, "error": resultado.get("error", "No se pudo ingresar el código")}
            logger.info(f"Código de email ingresado para tutela {tutela_id}")

            # Completar pasos restantes (5-10) con timeout de seguridad.
            try:
                await asyncio.wait_for(
                    _completar_radicacion(bot, tutela, datos, rad, session), timeout=360
                )
            except asyncio.TimeoutError:
                await _fallar_y_avisar(
                    bot,
                    tutela,
                    rad,
                    session,
                    "Timeout completando la radicación",
                    "⚠️ La radicación se demoró demasiado. Reintenta desde el panel admin.",
                )
                return {"ok": False, "error": "La radicación se demoró demasiado. Reintenta desde el panel admin."}

            return {"ok": True, "completado": True}

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
    """Señala al coroutine parkeado que el código de email ya llegó.

    Llamado por webhook_whatsapp.py cuando el usuario envía el código.

    Es SOLO un señalador: escribe ``_codigos_pendientes[tutela_id]`` (ANTES de
    señalar) y ordena ``loop.call_soon_threadsafe(evento.set)``. No toca el
    navegador, no crea hilos y responde al instante: el webhook nunca se
    bloquea con Playwright. El despertar del coroutine parkeado (que sí vive en
    el loop persistente del bot) consume el código con ``pop`` único.

    Si no hay coroutine parkeado (proceso reiniciado o ya consumido) responde
    un error claro para que el admin reintente, y jamás deja códigos huérfanos
    que despertarían un parqueo nuevo con un código viejo.
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
        if not rad or rad.estado not in ("esperando_codigo_email", "fallida"):
            return {"ok": False, "error": "Esta tutela no está esperando código de email"}

        with _parqueos_lock:
            parqueo = _parqueos.get(tutela_id)
        if parqueo is None:
            if rad.estado == "esperando_codigo_email":
                return {"ok": False, "error": "El código ya se está procesando o el proceso se reinició. Espera un momento o reintenta desde el panel admin."}
            return {"ok": False, "error": "El proceso de radicación no está activo (se reinició el servidor). Usa el botón 'Ejecutar bot' en el panel admin."}

        loop, evento, _ = parqueo
        with _parqueos_lock:
            _codigos_pendientes[tutela_id] = codigo  # antes de señalar: el pop del despertar lo consume
        loop.call_soon_threadsafe(evento.set)
        logger.info(f"Código señalado para tutela {tutela_id}")
        return {"ok": True, "señalado": True}
    except Exception as e:
        logger.error(f"Error señalando código para tutela {tutela_id}: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        session.close()


async def _cerrar_bot(bot):
    """Cierra el navegador si es posible, sin que un fallo rompa el flujo."""
    cerrar = getattr(bot, "cerrar", None)
    if cerrar is None:
        return
    try:
        await cerrar()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No se pudo cerrar el navegador tras el error: {e}")


def _avisar_usuario(tutela, texto: str):
    """Notifica al usuario por WhatsApp sin que un fallo rompa el flujo."""
    try:
        if tutela.user and tutela.user.telefono:
            enviar_texto(tutela.user.telefono, texto)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No se pudo avisar al usuario de tutela {tutela.id}: {e}")


async def _fallar_y_avisar(bot, tutela, rad, session, motivo, aviso_usuario):
    """Cierra el navegador, marca la radicación 'fallida' y avisa al usuario."""
    await _cerrar_bot(bot)
    rad.estado = "fallida"
    rad.ultimo_error = motivo
    session.commit()
    _avisar_usuario(tutela, aviso_usuario)
    logger.warning(f"Radicación tutela {tutela.id} fallida: {motivo}")


def descolgar_radicaciones_estancadas(umbral=UMBRAL_CONTINUANDO_ESTANCADO) -> list[dict]:
    """Watchdog: marca como 'fallida' las radicaciones colgadas.

    - 'continuando' más tiempo que `umbral`: navegador de Playwright pegado
      (sin respetar el timeout) o portal sin responder.
    - 'esperando_codigo_email' más tiempo que UMBRAL_ESPERANDO_CODIGO: el
      coroutine parkeado esperando el código se perdió (proceso reiniciado) o
      el usuario no respondió; el código del portal vence a los 10 min.

    Retorna el detalle de las radicaciones descolgadas.
    """
    session = SessionLocal()
    try:
        descolgadas: list[dict] = []
        corte = datetime.utcnow() - umbral
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

        corte_codigo = datetime.utcnow() - UMBRAL_ESPERANDO_CODIGO
        esperando_vencido = session.execute(
            select(Radicacion).where(
                Radicacion.estado == "esperando_codigo_email",
                Radicacion.updated_at < corte_codigo,
            )
        ).scalars().all()
        for rad in esperando_vencido:
            rad.estado = "fallida"
            rad.ultimo_error = "Código de email vencido (watchdog)"
            session.commit()
            descolgadas.append({"radicacion_id": rad.id, "tutela_id": rad.tutela_id})
            logger.warning(f"Watchdog: radicación {rad.id} (tutela {rad.tutela_id}) esperando código vencido → fallida")
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
