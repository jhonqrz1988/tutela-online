import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()

# Estado de la radicación automática en tiempo real, editable desde el panel
# admin (GET /admin/api/scheduler, POST /admin/api/scheduler/toggle). Se
# inicializa con el valor de enable_scheduler en el arranque.
_automatico_enabled = False


def _agregar_job():
    """Registra el job de radicación automática (si no existe)."""
    if scheduler.get_job("radicacion_automatica") is not None:
        return

    import app.tasks.jobs

    def _job_con_switch():
        if not _automatico_enabled:
            logger.info("Radicación automática pausada (toggle del panel admin)")
            return
        app.tasks.jobs.procesar_cola_radicacion()

    scheduler.add_job(
        _job_con_switch,
        trigger="cron",
        hour="8-17",
        minute="*/15",
        day_of_week="mon-fri",
        # SIN timezone el cron se evalúa en la zona horaria del proceso (en
        # Render es UTC): '8-17' corría de 08:00 a 17:00 UTC = 03:00-12:00
        # Bogotá, por lo que el job jamás disparaba en la ventana hábil
        # (8-12/14-17 Bogotá) → la radicación automática nunca arrancaba.
        timezone="America/Bogota",
        id="radicacion_automatica",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # ─── Recordatorios a usuarios a la espera ───────────────────────
    # Cada hora, 24/7: la ventana de 24 h de Meta corre desde el último mensaje
    # del usuario, no desde su horario de atención, así que un recordatorio a
    # las 9 pm igual sirve. Comparte el switch manual del panel (un solo
    # interruptor para toda la automatización).
    if scheduler.get_job("recordatorios_inactivos") is None:

        def _job_recordatorios():
            if not _automatico_enabled:
                logger.info("Recordatorios pausados (toggle del panel admin)")
                return
            app.tasks.jobs.enviar_recordatorios_inactivos()

        scheduler.add_job(
            _job_recordatorios,
            trigger="cron",
            minute="5",
            timezone="America/Bogota",
            id="recordatorios_inactivos",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=300,
        )


def _agregar_job_retencion():
    """Registra la purga diaria de visitas/clics (mantenimiento de la BD).

    A propósito NO cuelga de ``_automatico_enabled``: si el admin apaga la
    radicación desde el panel, la limpieza debe seguir corriendo. Se controla
    con su propio flag (``ENABLE_RETENCION``).
    """
    if not settings.enable_retencion:
        return
    if scheduler.get_job("retencion_datos") is not None:
        return

    import app.tasks.jobs

    scheduler.add_job(
        app.tasks.jobs.purgar_datos_antiguos,
        trigger="cron",
        hour=3,
        minute=0,
        # 03:00 Bogotá = valle de tráfico; además el DELETE diario mantiene la
        # base activa, lo que evita la pausa por inactividad de Supabase Free.
        timezone="America/Bogota",
        id="retencion_datos",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )


def iniciar_scheduler():
    """Scheduler de radicación automática + mantenimiento de la BD.

    Radicación: cada 15 minutos en horario hábil (8am-12pm y 2pm-5pm, lun-vie).
    El job ya valida horario internamente.
    Retención: purga diaria a las 03:00 Bogotá, con su propio flag.
    Si ambos flags están apagados no se crean hilos, pero el scheduler queda
    disponible para activar la radicación en caliente desde el panel admin.
    """
    global _automatico_enabled
    _automatico_enabled = settings.enable_scheduler
    if _automatico_enabled:
        _agregar_job()
    _agregar_job_retencion()

    if not (_automatico_enabled or settings.enable_retencion):
        logger.info("Scheduler deshabilitado (enable_scheduler=False); puede activarse desde el panel admin")
        return

    if not scheduler.running:
        scheduler.start()
    if _automatico_enabled:
        logger.info("Scheduler de radicación iniciado (cada 15min, lun-vie 8-16h)")
    if settings.enable_retencion:
        logger.info("Retención de datos activa (purga diaria 03:00 Bogotá)")


def scheduler_en_ejecucion() -> bool:
    """True si el hilo del scheduler está corriendo (aunque esté pausado)."""
    return scheduler.running


def automatico_activo() -> bool:
    """True si la radicación automática está activa en este momento."""
    return _automatico_enabled


def set_scheduler_automatico(habilitar: bool) -> bool:
    """Activa o desactiva en caliente la radicación automática desde el panel admin."""
    global _automatico_enabled
    _automatico_enabled = bool(habilitar)
    if _automatico_enabled:
        _agregar_job()
        # La retención no depende de este switch, pero si el hilo arranca aquí
        # (nunca se inició al boot) hay que registrarla también.
        _agregar_job_retencion()
        if not scheduler.running:
            scheduler.start()
        logger.info("Radicación automática ACTIVADA desde el panel admin")
    else:
        logger.info("Radicación automática DESACTIVADA desde el panel admin")
    return _automatico_enabled


def detener_scheduler():
    """Detiene el scheduler graceful."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler detenido")
