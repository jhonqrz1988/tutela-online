"""Tests de Fase B: STORAGE_DIR configurable (persistencia de archivos).

Verifica que las funciones de rutas de archivos usen settings.storage_dir como
raíz (no hardcodeado a 'storage'), de modo que con STORAGE_DIR=/data/storage
los PDFs/pruebas/constancias se guarden donde haya persistencia.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import settings
from app.utils import file_utils


class TestStorageDirConfigurable(unittest.TestCase):
    def _ruta_en(self, storage_dir, fn, *args):
        with mock.patch.object(settings, "storage_dir", storage_dir):
            return fn(*args)

    def test_paths_se_generan_bajo_storage_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = self._ruta_en(tmp, file_utils.path_tutela_pdf)
            self.assertEqual(Path(ruta).parent, Path(tmp) / "tutelas")
            self.assertTrue(Path(ruta).parent.is_dir())

    def test_pruebas_y_constancias_usan_storage_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            p_prueba = self._ruta_en(tmp, file_utils.path_prueba, ".jpg")
            p_const = self._ruta_en(tmp, file_utils.path_constancia)
            p_img = self._ruta_en(tmp, file_utils.path_constancia_imagen, ".png")
            self.assertEqual(os.path.dirname(p_prueba), os.path.join(tmp, "pruebas"))
            self.assertEqual(os.path.dirname(p_const), os.path.join(tmp, "constancias"))
            self.assertEqual(os.path.dirname(p_img), os.path.join(tmp, "constancias"))


class TestNavegadorScreenshotDir(unittest.TestCase):
    def test_screenshot_dir_usa_storage_dir(self):
        from app.bot import navegador
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(settings, "storage_dir", tmp):
            b = navegador.RadicadorBot()
            self.assertEqual(str(b._screenshot_dir), str(Path(tmp) / "screenshots"))
            self.assertTrue(b._screenshot_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
