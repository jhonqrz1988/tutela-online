import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def iniciar_scheduler():
    """Scheduler de radicación automática.

    Ejecuta la cola de radicación cada 15 minutos durante horario hábil
    (8am-12pm y 2pm-4pm, lunes a viernes). El job ya valida horario internamente.
    """
    if not settings.enable_scheduler:
        logger.info("Scheduler deshabilitado (enable_scheduler=False)")
        return

    import app.tasks.jobs

    # Ejecutar cada 15 minutos en horario laboral (8-16h lun-vie)
    # El job internamente valida los rangos exactos (8-12, 14-16)
    scheduler.add_job(
        app.tasks.jobs.procesar_cola_radicacion,
        trigger="cron",
        hour="8-16",
        minute="*/15",
        day_of_week="mon-fri",
        id="radicacion_automatica",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    scheduler.start()
    logger.info("Scheduler de radicación iniciado (cada 15min, lun-vie 8-16h)")


def detener_scheduler():
    """Detiene el scheduler graceful."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler detenido")
