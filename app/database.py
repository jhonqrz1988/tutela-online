from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


def _ensure_sqlite_dir(db_url: str) -> None:
    """Crea el directorio padre del archivo SQLite si no existe.

    Soporta URLs tipo sqlite:///./storage/tutelas.db y
    sqlite:////data/tutelas.db (volumen Railway en /data).
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
    Base.metadata.create_all(bind=engine)


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
