"""Tests for el toggle del scheduler y el monitoreo en el panel admin.

Cubre:
- GET /admin/api/scheduler devuelve el estado de la radicación automática.
- POST /admin/api/scheduler/toggle enciende/apaga en caliente sin crashear
  (el hilo real se evita mockeando el objeto scheduler).
- El botón "Ejecutar bot" (/reintentar) acepta pago_confirmado y
  esperando_codigo_email (respaldo manual ampliado).
- El panel incluye el mini-resumen de pasos del bot en la tabla.
"""
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.api.admin import SESSION_COOKIE, _crear_sesion
from app.config import settings
from app.database import Base, get_session
from app.main import app
from app.models.radicacion import PasoRadicacion, Radicacion
from app.models.tutela import Tutela
from app.models.user import User


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    return TestingSession()


class TestEstadoScheduler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings.admin_password = "test-password"
        settings.secret_key = "test-key-fijo"
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.client.close()

    def setUp(self):
        from app.tasks import scheduler as sched

        sched._automatico_enabled = False

    def tearDown(self):
        self.client.cookies.clear()
        from app.tasks import scheduler as sched

        sched._automatico_enabled = False

    def test_estado_scheduler_sin_auth_devuelve_401(self):
        self.client.cookies.clear()
        resp = self.client.get("/admin/api/scheduler")
        self.assertEqual(resp.status_code, 401)

    def test_get_estado_scheduler_default_off(self):
        self.client.cookies.set(SESSION_COOKIE, _crear_sesion())
        resp = self.client.get("/admin/api/scheduler")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("automatico", data)
        self.assertIn("horario", data)
        self.assertFalse(data["automatico"])

    def test_toggle_scheduler_enciende_y_apaga(self):
        self.client.cookies.set(SESSION_COOKIE, _crear_sesion())

        with mock.patch("app.tasks.scheduler.scheduler") as sched_mock:
            sched_mock.running = False
            sched_mock.get_job.return_value = None

            resp_on = self.client.post("/admin/api/scheduler/toggle")
            self.assertEqual(resp_on.status_code, 200)
            self.assertTrue(resp_on.json()["automatico"])

            resp_off = self.client.post("/admin/api/scheduler/toggle")
            self.assertEqual(resp_off.status_code, 200)
            self.assertFalse(resp_off.json()["automatico"])

    def test_set_scheduler_automatico_no_inicia_hilo_con_enable_false(self):
        """No debe arrancar el hilo cuando arranca con enable_scheduler=False."""
        from app.tasks import scheduler as sched

        settings.enable_scheduler = False
        with mock.patch.object(sched.scheduler, "start") as m_start:
            sched.iniciar_scheduler()
            m_start.assert_not_called()
        settings.enable_scheduler = False


class TestReintentarAmpliado(unittest.TestCase):
    def _post(self, tutela_id, session):
        client = TestClient(app)
        client.__enter__()

        def _override_get_session():
            yield session

        app.dependency_overrides[get_session] = _override_get_session
        client.cookies.set(SESSION_COOKIE, _crear_sesion())
        try:
            return client.post(f"/admin/tutelas/{tutela_id}/reintentar")
        finally:
            app.dependency_overrides.pop(get_session, None)
            client.__exit__(None, None, None)
            client.close()

    def test_acepta_pago_confirmado(self):
        session = _nueva_sesion()
        user = User(telefono="573009990101", nombre="Rosa Diaz", consentimiento=True)
        session.add(user)
        session.flush()
        t = Tutela(user_id=user.id, tipo="salud", estado="pago_confirmado", datos_json="{}")
        session.add(t)
        session.commit()

        from app.services import radicacion_service as rad_svc

        async def fake_iniciar(tutela_id, forzar=False):
            return {"ok": True, "completado": True}

        with mock.patch.object(rad_svc, "iniciar_radicacion", side_effect=fake_iniciar):
            resp = self._post(t.id, session)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))

    def test_acepta_esperando_codigo_email(self):
        session = _nueva_sesion()
        user = User(telefono="573009990102", nombre="Pablo Gil", consentimiento=True)
        session.add(user)
        session.flush()
        t = Tutela(user_id=user.id, tipo="salud", estado="esperando_codigo_email", datos_json="{}")
        session.add(t)
        session.commit()

        from app.services import radicacion_service as rad_svc

        async def fake_iniciar(tutela_id, forzar=False):
            return {"ok": True, "esperando_codigo": True}

        with mock.patch.object(rad_svc, "iniciar_radicacion", side_effect=fake_iniciar):
            resp = self._post(t.id, session)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))


class TestPanelIncluyePasos(unittest.TestCase):
    def test_admin_panel_muestra_progreso_bot(self):
        settings.admin_password = "test-password"
        settings.secret_key = "test-key-fijo"
        session = _nueva_sesion()
        user = User(telefono="573009990103", nombre="Ana Vera", consentimiento=True)
        session.add(user)
        session.flush()
        t = Tutela(user_id=user.id, tipo="salud", estado="fallida", datos_json="{}")
        session.add(t)
        session.commit()
        rad = Radicacion(tutela_id=t.id, estado="fallida")
        session.add(rad)
        session.commit()
        session.add(PasoRadicacion(radicacion_id=rad.id, paso="llenar_formulario", estado="ok"))
        session.add(PasoRadicacion(radicacion_id=rad.id, paso="resolver_captcha", estado="error", detalle="fallo"))
        session.commit()

        client = TestClient(app)
        client.__enter__()

        def _override_get_session():
            yield session

        app.dependency_overrides[get_session] = _override_get_session
        client.cookies.set(SESSION_COOKIE, _crear_sesion())
        try:
            resp = client.get("/admin")
        finally:
            app.dependency_overrides.pop(get_session, None)
            client.__exit__(None, None, None)
            client.close()

        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertIn("Progreso bot", html, "La tabla debe tener la columna de progreso del bot")
        self.assertIn("Formulario", html, "Debe aparecer la etiqueta del primer paso")
        self.assertIn("formulario", html.lower(), "Debe aparecer el paso registrado")

    def test_fecha_bogota_convierte_utc_a_hora_local(self):
        """El panel debe mostrar fechas en hora de Bogotá (UTC-5), no en UTC."""
        from datetime import datetime

        from app.api.admin import _fecha_bogota

        # 22:28 UTC (lo que guarda func.now en SQLite) = 17:28 en Bogotá
        naive_utc = datetime(2026, 9, 9, 22, 28, 39)
        self.assertEqual(_fecha_bogota(naive_utc), "2026-09-09 17:28")
        self.assertEqual(_fecha_bogota(None), "")


if __name__ == "__main__":
    unittest.main()