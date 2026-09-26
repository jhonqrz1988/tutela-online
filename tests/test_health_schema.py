from unittest import mock
import unittest

from starlette.testclient import TestClient

from app.api import health as health_mod
from app.main import app


class TestHealthSchema(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def _mock_session(self, columnas, tablas):
        """Session mock que responde a SELECT 1 y a los PRAGMA del schema."""

        def _execute(texto, **kw):
            s = str(texto)
            if s.strip().startswith("SELECT 1"):
                return mock.Mock()
            if "PRAGMA table_info" in s:
                return [tuple([i, c, "VARCHAR(500)", 0, None, 0]) for i, c in enumerate(columnas)]
            return mock.Mock()

        class _Cols:
            pass

        with mock.patch.object(health_mod, "SessionLocal") as sl:
            sl.return_value.__enter__ = lambda self: self
            sl.return_value.__exit__ = lambda *a: False
            sl.return_value.execute = _execute

            inspector_mock = mock.Mock()
            inspector_mock.get_table_names.return_value = tablas
            inspector_mock.get_columns.return_value = [
                {"name": c} for c in columnas
            ]
            with mock.patch.object(health_mod, "inspect") as insp:
                insp.return_value = inspector_mock
                return health_mod._esquema_bd()

    def test_esquema_sano(self):
        res = self._mock_session(
            ["id", "fuente", "es_bot", "user_agent"],
            ["visitas_landing", "clics_whatsapp", "tutelas", "users"],
        )
        self.assertIs(res["visitas_landing"]["es_bot"], True)
        self.assertIs(res["visitas_landing"]["user_agent"], True)
        self.assertIs(res["tablas"]["clics_whatsapp"], True)
        self.assertIsNone(res["error"])

    def test_faltan_columnas_detecta(self):
        res = self._mock_session(
            ["id", "fuente"],
            ["visitas_landing", "clics_whatsapp"],
        )
        self.assertIs(res["visitas_landing"]["es_bot"], False)
        self.assertIs(res["visitas_landing"]["user_agent"], False)

    def test_falta_tabla_detecta(self):
        res = self._mock_session(
            ["id", "fuente", "es_bot", "user_agent"],
            ["visitas_landing"],
        )
        self.assertIs(res["tablas"]["clics_whatsapp"], False)

    def test_health_incluye_schema(self):
        with mock.patch.object(health_mod, "SessionLocal") as sl:
            sl.return_value.__enter__ = lambda self: self
            sl.return_value.__exit__ = lambda *a: False
            sl.return_value.execute = lambda s, **kw: mock.Mock()
            r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertIn("schema", r.json())


if __name__ == "__main__":
    unittest.main()