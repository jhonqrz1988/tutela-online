import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CitaLegal(Base):
    __tablename__ = "citas_legales"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tipo: Mapped[str] = mapped_column(String(30))
    referencia: Mapped[str] = mapped_column(String(200), unique=True)
    referencia_normalizada: Mapped[str] = mapped_column(String(200), index=True)
    titulo_corto: Mapped[str] = mapped_column(String(200), nullable=True)
    texto_resumen: Mapped[str] = mapped_column(Text, nullable=True)
    url_fuente: Mapped[str] = mapped_column(String(500), nullable=True)
    aplica_a: Mapped[str] = mapped_column(String(50), default="salud")
    vigente: Mapped[bool] = mapped_column(Boolean, default=True)
    fecha_verificacion: Mapped[datetime.date] = mapped_column(Date, nullable=True)
    verificado_por: Mapped[str] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())


class CitaPendiente(Base):
    __tablename__ = "citas_pendientes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tutela_id: Mapped[int] = mapped_column(Integer, ForeignKey("tutelas.id"))
    referencia_textual: Mapped[str] = mapped_column(String(300))
    contexto: Mapped[str] = mapped_column(Text, nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    tutela: Mapped["Tutela"] = relationship(back_populates="citas_pendientes")  # noqa: F821