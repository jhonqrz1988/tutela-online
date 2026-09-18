"""Screenshots de diagnóstico agrupados por tutela en el panel admin.

Problema reportado: el dashboard muestra las capturas de TODAS las tutelas
mezcladas (lista de las últimas 40), no las de la tutela que se está revisando.

Este test exige que el bot prefije las capturas con 't{id}_' (nombre asociable a
la tutela) y que el endpoint /admin/screenshots acepte ?tutela_id=N y filtre.
"""
import tempfile
import unittest
from unittest import mock

from starlette.testclient import TestClient

from app.api.admin import SESSION_COOKIE, _crear_sesion
from app.config import settings
from app.main import app


class TestScreenshotsPorTutela(unittest.TestCase):
    def setUp(self):
        settings.admin_password = "test-password"
        settings.secret_key = "test-key-fijo"
        self.client = TestClient(app)
        self.client.__enter__()
        self.client.cookies.set(SESSION_COOKIE, _crear_sesion())

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.client.close()
        self.client.cookies.clear()

    def test_pide_screenshots_de_una_tutela_y_no_mezcla(self):
        from app.bot import navegador

        # Screenshots de tres tutelas distintas en el mismo storage
        # (simulados: solo se piden los .png existentes en el directorio).
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(settings, "storage_dir", tmp):
            dir_shots = navegador.RadicadorBot()._screenshot_dir
            dir_shots.mkdir(parents=True, exist_ok=True)
            for nombre in (
                "t50_accionante_vacio.png",
                "t50_codigo_sin_cajon.png",
                "t49_codigo_no_escrito.png",
                "t48_envio_validacion_error.png",
            ):
                (dir_shots / nombre).write_bytes(b"PNGDATA")

            resp = self.client.get("/admin/screenshots?tutela_id=50")
            self.assertEqual(resp.status_code, 200)
            nombres = [i["nombre"] for i in resp.json()["imagenes"]]
            self.assertEqual(set(nombres), {"t50_accionante_vacio.png", "t50_codigo_sin_cajon.png"})

    def test_sin_filtro_devuelve_todas(self):
        from app.bot import navegador

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(settings, "storage_dir", tmp):
            dir_shots = navegador.RadicadorBot()._screenshot_dir
            dir_shots.mkdir(parents=True, exist_ok=True)
            (dir_shots / "t50_accionante_vacio.png").write_bytes(b"PNGDATA")
            (dir_shots / "t49_codigo_no_escrito.png").write_bytes(b"PNGDATA")

            resp = self.client.get("/admin/screenshots")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(len(resp.json()["imagenes"]), 2)

    def test_bot_prefija_el_screenshot_con_el_id_de_tutela(self):
        """El nombre de la captura debe llevar 't{id}_' para poder agruparse
        después; sin eso ninguna lógica del front puede distinguir a qué tutela
        pertenece."""
        from app.bot import navegador

        bot = navegador.RadicadorBot()
        bot.tutela_id = 50
        self.assertEqual(bot._nombre_evidencia("accionante_vacio"), "t50_accionante_vacio")

        bot2 = navegador.RadicadorBot()
        self.assertEqual(bot2._nombre_evidencia("accionante_vacio"), "accionante_vacio")


if __name__ == "__main__":
    unittest.main()