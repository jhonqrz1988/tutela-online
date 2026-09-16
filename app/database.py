import logging
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

# Reintentos de conexión en el arranque: en Render, Postgres puede recién
# reiniciarse justo al desplegar y la primera conexión SSL falla ("SSL
# connection has been closed unexpectedly") -> el boot abortaba con status 3.
DB_REINTENTOS = 6
DB_ESPERA_SEG = 8  # cubre hasta ~48 s; Render espera ~60 s por el /health


def _ensure_sqlite_dir(db_url: str) -> None:
    """Crea el directorio padre del archivo SQLite si no existe.

    Soporta URLs tipo sqlite:///./storage/tutelas.db y
    sqlite:////data/tutelas.db (volumen montado en /data, ej. Render o VPS).
    Sin esto, SQLite falla si el directorio no existe.
    """
    if not db_url.startswith("sqlite"):
        return
    # Extrae la ruta del archivo: después de sqlite:///
    raw = db_url.split("sqlite:///")[-1].split("?")[0].split(";")[0]
    # Quita prefijo +aiosqlite si estuviera en la ruta
    raw = raw.replace("+aiosqlite", "")
    if raw and raw != ":memory:":
        Path(raw).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

# Neon pooler cierra conexiones inactivas -> pool_pre_ping verifica antes de usar
engine_kwargs = {"connect_args": connect_args}
if not settings.database_url.startswith("sqlite"):
    engine_kwargs.update({
        "pool_pre_ping": True,
        "pool_recycle": 300,  # reciclar cada 5 min (Neon cierra a los ~5 min)
    })

engine = create_engine(
    settings.database_url.replace("+aiosqlite", ""),
    echo=False,
    **engine_kwargs,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def init_db():
    import app.models  # noqa: F401
    _crear_tablas_con_reintentos()


def _crear_tablas_con_reintentos():
    """Crea las tablas reconectando con backoff si el gestor está reiniciando.

    El fallo es transitorio: se re-intenta con una pausa corta y, si al final
    sigue caído, se propaga (la app no debe arrancar sin base de datos).
    """
    from sqlalchemy import exc, text

    ultimo_error = None
    for intento in range(1, DB_REINTENTOS + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            Base.metadata.create_all(bind=engine)
            return
        except exc.OperationalError as e:
            ultimo_error = e
            logger.warning(
                f"Base de datos no disponible (intento {intento}/{DB_REINTENTOS}): "
                f"{e.__cause__ or e}"
            )
            if intento < DB_REINTENTOS:
                time.sleep(DB_ESPERA_SEG)
    raise ultimo_error


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
