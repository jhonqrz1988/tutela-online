import json
import logging

from sqlalchemy import select

from app.bot.navegador import RadicadorBot
from app.database import SessionLocal
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela
from app.services.whatsapp_service import enviar_texto, enviar_imagen

logger = logging.getLogger(__name__)

# Instancia global del bot (se reutiliza entre llamadas)
_bot: RadicadorBot | None = None


def _get_bot() -> RadicadorBot:
    global _bot
    if _bot is None:
        _bot = RadicadorBot()
    return _bot


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

        # Paso 1: Iniciar navegador y navegar al portal
        await bot.iniciar()
        await bot.navegar_portal()

        # Paso 2: Llenar formulario (pasos 1-4, hasta verificación email)
        resultado = await bot.llenar_formulario(datos)

        if not resultado.get("ok"):
            rad.estado = "fallida"
            rad.ultimo_error = resultado.get("error", "Error desconocido en llenado")
            session.commit()
            await bot.cerrar()
            return {"ok": False, "error": resultado.get("error")}

        # Si requiere código de email → pausar y notificar al usuario
        if resultado.get("requiere_codigo_email"):
            rad.estado = "esperando_codigo_email"
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
        if not rad or rad.estado != "esperando_codigo_email":
            return {"ok": False, "error": "Esta tutela no está esperando código de email"}

        datos = json.loads(tutela.datos_json or "{}")
        bot = _get_bot()

        # Ingresar código de verificación
        await bot.ingresar_codigo_email(codigo)
        logger.info(f"Código de email ingresado para tutela {tutela_id}")

        # Completar pasos restantes (5-10)
        await _completar_radicacion(bot, tutela, datos, rad, session)

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
            session.commit()
            await bot.cerrar()
            return

        # Paso 9: Resolver reCAPTCHA
        rad.estado = "resolviendo_captcha"
        session.commit()
        captcha_ok = await bot.resolver_recaptcha()

        if not captcha_ok:
            rad.estado = "fallida"
            rad.ultimo_error = "No se pudo resolver el reCAPTCHA"
            rad.intentos = (rad.intentos or 0) + 1
            session.commit()
            await bot.cerrar()
            return

        logger.info(f"reCAPTCHA resuelto para tutela {tutela.id}")

        # Paso 10: Enviar y descargar constancia
        rad.estado = "enviando"
        session.commit()
        resultado_envio = await bot.enviar_y_descargar()

        if resultado_envio.get("error"):
            rad.estado = "fallida"
            rad.ultimo_error = resultado_envio["error"]
            rad.intentos = (rad.intentos or 0) + 1
            session.commit()
            await bot.cerrar()
            return

        # Extraer número de radicado
        num_radicado = resultado_envio.get("num_radicado", "")
        rad.num_radicado = num_radicado
        rad.constancia_path = resultado_envio.get("path")
        rad.estado = "radicada"
        rad.intentos = (rad.intentos or 0) + 1
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
        session.commit()
    finally:
        await bot.cerrar()
