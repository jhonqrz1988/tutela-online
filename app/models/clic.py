import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ClicWhatsApp(Base):
    """Registro server-side de cada clic en un botón/enlace wa.me de la landing.

    Complementa a ``VisitaLanding``: una visita llega a la landing, un clic la
    abandona hacia WhatsApp. Sirve para distinguir "hizo clic" (llegó al chat a
    escribir) de "llegó el mensaje" (conversación real). Los que llegan con
    User-Agent de bot/crawler NO se registran.
    """

    __tablename__ = "clics_whatsapp"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fuente: Mapped[str] = mapped_column(String(100), default="directo")
    medio: Mapped[str] = mapped_column(String(50), nullable=True)
    campania: Mapped[str] = mapped_column(String(200), nullable=True)
    es_pauta: Mapped[bool] = mapped_column(Boolean, default=False)
    ubicacion: Mapped[str | None] = mapped_column(String(100), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())