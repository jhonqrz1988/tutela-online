import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MensajeWhatsApp(Base):
    __tablename__ = "mensajes_whatsapp"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_number: Mapped[str] = mapped_column(String(20), index=True)
    body: Mapped[str] = mapped_column(Text, nullable=True)
    tipo_mensaje: Mapped[str] = mapped_column(String(50), default="texto")
    media_url: Mapped[str] = mapped_column(String(500), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=True)
    tutela_id: Mapped[int] = mapped_column(ForeignKey("tutelas.id"), nullable=True)
    es_recibido: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    tutela: Mapped["Tutela"] = relationship(back_populates="mensajes")  # noqa: F821
