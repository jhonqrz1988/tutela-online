import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class VisitaLanding(Base):
    """Registro de cada carga de la página de aterrizaje (/).

    Guarda solo datos de campaña/tráfico (UTM/Facebook), sin IP ni datos
    del visitante, para medir la llegada de pauta publicitaria.
    """

    __tablename__ = "visitas_landing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fuente: Mapped[str] = mapped_column(String(100), default="directo")
    medio: Mapped[str] = mapped_column(String(50), nullable=True)
    campania: Mapped[str] = mapped_column(String(200), nullable=True)
    termino: Mapped[str] = mapped_column(String(200), nullable=True)
    contenido: Mapped[str] = mapped_column(String(200), nullable=True)
    es_pauta: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())