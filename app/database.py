import logging
import time
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

# Reintentos de conexión en el arranque: en Render, Postgres puede recién
# reiniciarse justo al desplegar y la primera conexión SSL falla ("SSL
# connection has been closed unexpectedly") -> el boot abortaba con status 3.
DB_REINTENTOS = 6
DB_ESPERA_SEG = 8  # cubre hasta ~48 s; Render espera ~60 s por el /health

# Diagnóstico: último resultado de la migración (persistido en memoria y
# expuesto por /health) para detectar fallos silenciosos en prod sin logs.
_ULTIMA_MIGRACION: dict = {}


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
    from sqlalchemy import exc

    ultimo_error = None
    for intento in range(1, DB_REINTENTOS + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                _migrar_esquema(conn)
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


def _migrar_esquema(conn):
    """Ajustes de esquema idempotentes para tablas ya creadas.

    ``create_all`` no agrega columnas a tablas existentes; estas migraciones
    ligeras las añaden sin borrar datos. Best-effort: si algo falla se loguea
    y se continúa con el arranque (nunca se pierden datos).

    IMPORTANTE: el DDL se hace commit explícitamente. En SQLite se autocomitea
    (por eso pasa desapercibido en local), pero en PostgreSQL los ALTER TABLE
    dentro de una transacción se revierten al cerrar la conexión — lo que dejaba
    el dashboard admin sin ``es_bot``/``user_agent`` -> 500 al cargar.
    """
    resultado = {"ok": False, "detalle": "", "tablas": [], "columnas": {}, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        inspector = inspect(conn)
        tablas_existentes = set(inspector.get_table_names())
        resultado["tablas"] = sorted(tablas_existentes)

        if "visitas_landing" in tablas_existentes:
            columnas = {c["name"] for c in inspector.get_columns("visitas_landing")}
            resultado["columnas"] = sorted(columnas)
            if "user_agent" not in columnas:
                conn.execute(text("ALTER TABLE visitas_landing ADD COLUMN user_agent VARCHAR(500)"))
            if "es_bot" not in columnas:
                conn.execute(text("ALTER TABLE visitas_landing ADD COLUMN es_bot BOOLEAN NOT NULL DEFAULT 0"))
        resultado["ok"] = True
    except Exception as e:  # noqa: BLE001 - la migración nunca debe impedir el boot
        logger.warning(f"No se pudo ajustar el esquema: {e}")
        resultado["detalle"] = str(e)[:300]
    finally:
        try:
            conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"No se pudo confirmar la migración: {e}")
            resultado["detalle"] = f"{resultado['detalle']}; commit: {str(e)[:150]}"
    _ULTIMA_MIGRACION.update(resultado)
    return resultado


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
