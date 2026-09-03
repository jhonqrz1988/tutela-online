"""Tests for Fase A: tutelas.py devuelve HTTP 404 real (A7).

Hoy `obtener_tutela` y `generar_pdf_tutela` devuelven `{"error":...}, 404`
(tupla), que FastAPI no interpreta como HTTP 404 -> produce error 500.
"""
import unittest

from starlette.testclient import TestClient

from app.api.admin import SESSION_COOKIE, _crear_sesion
from app.config import settings
from app.main import app


class TestTutelas404(unittest.TestCase):
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

    def test_obtener_tutela_inexistente_404(self):
        self.client.cookies.set(SESSION_COOKIE, _crear_sesion())
        resp = self.client.get("/api/v1/tutelas/999999")
        self.assertEqual(resp.status_code, 404, f"Se esperaba 404, se obtuvo {resp.status_code}")

    def test_generar_pdf_tutela_inexistente_404(self):
        self.client.cookies.set(SESSION_COOKIE, _crear_sesion())
        resp = self.client.post("/api/v1/tutelas/999999/generar-pdf")
        self.assertEqual(resp.status_code, 404, f"Se esperaba 404, se obtuvo {resp.status_code}")


if __name__ == "__main__":
    unittest.main()
