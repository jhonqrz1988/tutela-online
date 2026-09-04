"""Tests for Fase D: cola de radicación automática del bot.

Una tutela ya pagada (pago_confirmado) debe entrar a la cola de radicación
para que el scheduler la radique automáticamente sin intervención manual.
"""
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.tutela import Tutela
from app.models.user import User

from app.tasks import jobs


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    return TestingSession()


class TestColaIncluyePagoConfirmado(unittest.TestCase):
    def test_pago_confirmado_se_procesa(self):
        """Una tutela en 'pago_confirmado' debe procesarse por el scheduler."""
        Session = _nueva_sesion()
        user = User(telefono="573000006666", estado="activo", consentimiento=True)
        Session.add(user)
        Session.flush()
        t = Tutela(user_id=user.id, tipo="salud", estado="pago_confirmado", datos_json="{}")
        Session.add(t)
        Session.commit()

        procesadas = []

        async def fake_iniciar(tutela_id):
            procesadas.append(tutela_id)
            return {"ok": True}

        with mock.patch.object(jobs, "SessionLocal", return_value=Session), \
             mock.patch.object(jobs, "es_horario_habil", return_value=True), \
             mock.patch.object(jobs, "iniciar_radicacion", side_effect=fake_iniciar):
            jobs.procesar_cola_radicacion()

        self.assertIn(t.id, procesadas, "Una tutela pagada (pago_confirmado) debe entrar a la cola")

    def test_estados_en_cola_constante(self):
        """La constante de estados en cola debe incluir los estados pagados/retry."""
        self.assertIn("pago_confirmado", jobs.ESTADOS_EN_COLA)
        self.assertIn("pendiente_radicacion", jobs.ESTADOS_EN_COLA)
        self.assertIn("fallida", jobs.ESTADOS_EN_COLA)


if __name__ == "__main__":
    unittest.main()