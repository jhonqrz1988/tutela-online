import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Radicacion(Base):
    __tablename__ = "radicaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tutela_id: Mapped[int] = mapped_column(ForeignKey("tutelas.id"))
    estado: Mapped[str] = mapped_column(String(50), default="pendiente")
    num_radicado: Mapped[str] = mapped_column(String(50), nullable=True)
    constancia_path: Mapped[str] = mapped_column(String(500), nullable=True)
    token_verificacion: Mapped[str] = mapped_column(String(20), nullable=True)
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    ultimo_error: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    tutela: Mapped["Tutela"] = relationship(back_populates="radicacion")  # noqa: F821
