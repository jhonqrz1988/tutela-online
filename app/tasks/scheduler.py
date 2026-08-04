from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings

scheduler = BackgroundScheduler()


def iniciar_scheduler():
    """Radicación automática nocturna. DESACTIVADO: la radicación es manual
    desde el panel (`/admin`), el bot no se usa. Rendimiento no crítico.
    """
    if not settings.enable_scheduler:
        return
    import app.tasks.jobs

    scheduler.add_job(
        app.tasks.jobs.procesar_cola_radicacion,
        trigger="cron",
        hour=settings.filing_hour,
        minute=settings.filing_minute,
        day_of_week="mon-fri",
        id="radicacion_nocturna",
        replace_existing=True,
    )
    scheduler.start()
