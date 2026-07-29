import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telefono: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(200), nullable=True)
    email: Mapped[str] = mapped_column(String(200), nullable=True)
    estado: Mapped[str] = mapped_column(String(50), default="nuevo")
    consentimiento: Mapped[bool] = mapped_column(default=False)
    consentimiento_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    consentimiento_timestamp: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    tutelas: Mapped[list["Tutela"]] = relationship(back_populates="user")  # noqa: F821
