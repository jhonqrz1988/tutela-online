"""/docs, /redoc y /openapi.json solo disponibles en desarrollo; ocultos en producción.

El esquema OpenAPI expone todas las rutas internas del API; en producción
(tutelapp.co) no debe ser accesible públicamente.
"""
import unittest

from app.main import _config_docs


class TestDocsProduccion(unittest.TestCase):
    def test_oculta_docs_en_produccion(self):
        cfg = _config_docs(es_produccion=True)
        self.assertIsNone(cfg["docs_url"], "Swagger /docs debe estar oculto en producción")
        self.assertIsNone(cfg["redoc_url"], "ReDoc /redoc debe estar oculto en producción")
        self.assertIsNone(cfg["openapi_url"], "El esquema /openapi.json debe estar oculto en producción")

    def test_abre_docs_en_entorno_local(self):
        cfg = _config_docs(es_produccion=False)
        self.assertEqual(cfg["docs_url"], "/docs")
        self.assertEqual(cfg["redoc_url"], "/redoc")
        self.assertEqual(cfg["openapi_url"], "/openapi.json")


if __name__ == "__main__":
    unittest.main()