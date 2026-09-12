"""El panel identifica las tutelas por cédula y muestra el número de tutela actual.

El id interno (autoincremental) sigue siendo la clave de referencia (Mercado Pago
TUT-{id}, PDFs, webhook); la cédula pasa a ser el identificador visible.
"""
import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.api.admin import SESSION_COOKIE, _crear_sesion
from app.config import settings
from app.database import Base, get_session
from app.main import app
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


class TestPanelCedula(unittest.TestCase):
    def setUp(self):
        settings.admin_password = "test-password"
        settings.secret_key = "test-key-fijo"
        self.session = _nueva_sesion()
        self.client = TestClient(app)
        self.client.__enter__()
        self.client.cookies.set(SESSION_COOKIE, _crear_sesion())

        def _override_get_session():
            yield self.session

        app.dependency_overrides[get_session] = _override_get_session

    def tearDown(self):
        app.dependency_overrides.pop(get_session, None)
        self.client.__exit__(None, None, None)
        self.client.close()

    def _crear_tutela_con_cedula(self, cedula="98765432"):
        user = User(telefono="573009990104", nombre="Luis Mora", consentimiento=True)
        self.session.add(user)
        self.session.flush()
        t = Tutela(
            user_id=user.id,
            tipo="salud",
            estado="radicada",
            datos_json=json.dumps({"accionante_cedula": cedula, "accionante_nombre": "Luis Mora"}),
        )
        self.session.add(t)
        self.session.commit()
        return t.id

    def test_panel_muestra_cedula_como_identificador(self):
        self._crear_tutela_con_cedula()
        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertIn("<th>C&eacute;dula</th>", html, "La tabla debe tener la columna Cédula")
        self.assertIn("98765432", html, "La cédula debe aparecer en la fila")

    def test_panel_muestra_numero_de_tutela_actual(self):
        self._crear_tutela_con_cedula()
        resp = self.client.get("/admin")
        self.assertIn("de tutela actual: <strong>1</strong>", resp.text, "Debe verse el número de tutela actual")

    def test_detalle_incluye_cedula(self):
        t_id = self._crear_tutela_con_cedula("111222333")
        resp = self.client.get(f"/admin/api/tutelas/{t_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("cedula"), "111222333")


if __name__ == "__main__":
    unittest.main()