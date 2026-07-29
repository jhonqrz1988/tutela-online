import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

TIPOS_HABILITADOS = ["salud"]


class Tutela(Base):
    __tablename__ = "tutelas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    tipo: Mapped[str] = mapped_column(String(50), default="salud")
    estado: Mapped[str] = mapped_column(String(50), default="borrador")
    datos_json: Mapped[str] = mapped_column(Text, nullable=True)
    pdf_path: Mapped[str] = mapped_column(String(500), nullable=True)
    estado_verificacion: Mapped[str] = mapped_column(String(30), default="sin_verificar")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="tutelas")  # noqa: F821
    radicacion: Mapped[list["Radicacion"]] = relationship(back_populates="tutela")  # noqa: F821
    mensajes: Mapped[list["MensajeWhatsApp"]] = relationship(back_populates="tutela")  # noqa: F821
    citas_pendientes: Mapped[list["CitaPendiente"]] = relationship(back_populates="tutela")  # noqa: F821
