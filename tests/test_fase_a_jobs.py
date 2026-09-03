"""Tests for Fase A: bugs en jobs.py (A3-A6).

- A4: es_horario_habil debe usar zona de Bogotá (UTC-5), no hora naive (UTC).
- A3: procesar_cola_radicacion no debe reutilizar una sesión cerrada.
- A5: la cola debe incluir el estado 'pendiente'.
- A6: no debe estrellarse si la tutela no tiene radicación previa (rad=None).
"""
import unittest
from datetime import datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela
from app.models.user import User

from app.tasks import jobs


class _FakeDatetime:
    """Simula datetime.now(tz) a partir de un instante fijo en UTC.

    Lunes 13:00 UTC == Lunes 08:00 Bogotá (dentro del horario 8-12 hábil).
    La versión naive (sin tz) devolvería 13:00 -> 13 no está en (8-12) ni (14-16).
    """
    _utc_now = datetime(2024, 5, 6, 13, 0, tzinfo=timezone.utc)

    @staticmethod
    def now(tz=None):
        if tz is not None:
            return _FakeDatetime._utc_now.astimezone(tz)
        return _FakeDatetime._utc_now.replace(tzinfo=None)


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    return TestingSession()


class TestEsHorarioHabil(unittest.TestCase):
    def test_usa_zona_bogota_no_utc(self):
        """A las 13:00 UTC (08:00 Bogotá) en lunes debe ser horario hábil."""
        with mock.patch.object(jobs, "datetime", _FakeDatetime):
            self.assertTrue(jobs.es_horario_habil(), "13:00 UTC = 08:00 Bogotá debe ser horario hábil")

    def test_logica_pura_con_inyeccion(self):
        """Lógica pura con un datetime explícito de Bogotá."""
        bogota = ZoneInfo("America/Bogota")
        self.assertTrue(jobs.es_horario_habil(datetime(2024, 5, 6, 8, 0, tzinfo=bogota)))
        self.assertTrue(jobs.es_horario_habil(datetime(2024, 5, 6, 11, 59, tzinfo=bogota)))
        self.assertTrue(jobs.es_horario_habil(datetime(2024, 5, 6, 14, 0, tzinfo=bogota)))
        self.assertFalse(jobs.es_horario_habil(datetime(2024, 5, 6, 12, 0, tzinfo=bogota)))  # fuera (break)
        self.assertFalse(jobs.es_horario_habil(datetime(2024, 5, 6, 16, 0, tzinfo=bogota)))   # fuera
        self.assertTrue(jobs.es_horario_habil(datetime(2024, 5, 7, 10, 0, tzinfo=bogota)))   # martes hábil
        self.assertFalse(jobs.es_horario_habil(datetime(2024, 5, 11, 10, 0, tzinfo=bogota)))  # sábado


class TestProcesarColaRadicacion(unittest.TestCase):
    def test_incluye_estado_pendiente_y_no_crash_sin_radicacion(self):
        """Una tutela 'pendiente' debe procesarse; una sin radicación no debe crashear."""
        Session = _nueva_sesion()

        user = User(telefono="573000000001", estado="activo", consentimiento=True)
        Session.add(user)
        Session.flush()

        t_pendiente = Tutela(user_id=user.id, tipo="salud", estado="pendiente", datos_json="{}")
        t_pend_rad = Tutela(user_id=user.id, tipo="salud", estado="pendiente_radicacion", datos_json="{}")
        Session.add_all([t_pendiente, t_pend_rad])
        Session.commit()

        procesadas = []

        async def fake_iniciar(tutela_id):
            procesadas.append(tutela_id)
            return {"ok": True}

        with mock.patch.object(jobs, "SessionLocal", return_value=Session), \
             mock.patch.object(jobs, "es_horario_habil", return_value=True), \
             mock.patch.object(jobs, "iniciar_radicacion", side_effect=fake_iniciar):
            jobs.procesar_cola_radicacion()

        self.assertIn(t_pendiente.id, procesadas, "La tutela en estado 'pendiente' debe procesarse")
        self.assertIn(t_pend_rad.id, procesadas, "La tutela en 'pendiente_radicacion' sin radicación no debe crashear")

    def test_respeta_max_intentos(self):
        """Una tutela fallida con 3 intentos o más no se reprocesa."""
        Session = _nueva_sesion()
        user = User(telefono="573000000002", estado="activo", consentimiento=True)
        Session.add(user)
        Session.flush()
        t = Tutela(user_id=user.id, tipo="salud", estado="fallida", datos_json="{}")
        Session.add(t)
        Session.commit()
        rad = Radicacion(tutela_id=t.id, estado="fallida", intentos=3)
        Session.add(rad)
        Session.commit()

        procesadas = []

        async def fake_iniciar(tutela_id):
            procesadas.append(tutela_id)
            return {"ok": True}

        with mock.patch.object(jobs, "SessionLocal", return_value=Session), \
             mock.patch.object(jobs, "es_horario_habil", return_value=True), \
             mock.patch.object(jobs, "iniciar_radicacion", side_effect=fake_iniciar):
            jobs.procesar_cola_radicacion()

        self.assertNotIn(t.id, procesadas, "Tutela con intentos agotados no debe reprocesarse")


if __name__ == "__main__":
    unittest.main()
