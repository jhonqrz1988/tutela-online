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
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Resultado de entregar la respuesta del bot a este mensaje entrante:
    # "entregado" (Meta aceptó) o "fallido" (rechazó, p. ej. número EXPIRED).
    # Permite medir "usuarios que escribieron pero nunca recibieron respuesta".
    envio_estado: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tutela_id: Mapped[int] = mapped_column(ForeignKey("tutelas.id"), nullable=True)
    es_recibido: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    tutela: Mapped["Tutela"] = relationship(back_populates="mensajes")  # noqa: F821


class EnvioWhatsApp(Base):
    """Un mensaje saliente del bot, seguido por los ``statuses`` de Meta.

    El POST a la Graph API solo devuelve HTTP 200 y un ``wamid``: eso NO prueba
    entrega. Meta notifica después ``sent``/``delivered``/``read``/``failed`` en
    el webhook. Sin esta tabla no hay forma de saber si un número "no arranca"
    porque el mensaje nunca llegó, y el motivo real (número no está en WhatsApp,
    ventana de 24 h vencida, usuario bloqueó el negocio...) se pierde.
    """

    __tablename__ = "envios_whatsapp"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wamid: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    from_number: Mapped[str] = mapped_column(String(20), index=True)
    # "aceptado" (HTTP 200) -> "entregado" -> "leido"; "fallido" si Meta reporta error.
    estado: Mapped[str] = mapped_column(String(20), default="aceptado", index=True)
    error_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_detalle: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tutela_id: Mapped[int | None] = mapped_column(ForeignKey("tutelas.id"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    actualizado_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
