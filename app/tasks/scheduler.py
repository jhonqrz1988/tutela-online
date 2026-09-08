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
        hour="8-16",
        minute="*/15",
        day_of_week="mon-fri",
        id="radicacion_automatica",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )


def iniciar_scheduler():
    """Scheduler de radicación automática.

    Ejecuta la cola de radicación cada 15 minutos durante horario hábil
    (8am-12pm y 2pm-4pm, lunes a viernes). El job ya valida horario internamente.
    Si enable_scheduler=False el scheduler no se arranca (no crea hilos) pero
    queda disponible para activarlo en caliente desde el panel admin.
    """
    global _automatico_enabled
    _automatico_enabled = settings.enable_scheduler
    if not _automatico_enabled:
        logger.info("Scheduler deshabilitado (enable_scheduler=False); puede activarse desde el panel admin")
        return

    _agregar_job()
    if not scheduler.running:
        scheduler.start()
    logger.info("Scheduler de radicación iniciado (cada 15min, lun-vie 8-16h)")


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
