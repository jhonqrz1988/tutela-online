"""Precio centralizado en settings.mercadopago_amount.

Los mensajes del bot y la página de pago deben mostrar el mismo precio que el
checkout de Mercado Pago (settings.mercadopago_amount), nunca un literal fijo.
"""
import unittest

from app.api.pagos import _pagina_pago
from app.api.webhook_whatsapp import CONFIRMAR_PAGO_TEXTO, POST_PDF_OPCIONES
from app.config import settings
from app.services.mercadopago_service import formatear_monto, texto_precio


class TestPrecioFormateado(unittest.TestCase):
    def setUp(self):
        self.precio_original = settings.mercadopago_amount

    def tearDown(self):
        settings.mercadopago_amount = self.precio_original

    def test_formatear_monto_viene_de_settings(self):
        settings.mercadopago_amount = 29000.0
        self.assertEqual(formatear_monto(), "$29.000")
        self.assertEqual(texto_precio(), "$29.000 COP")

    def test_formatear_monto_cambia_con_settings(self):
        settings.mercadopago_amount = 40000.0
        self.assertEqual(formatear_monto(), "$40.000")
        self.assertEqual(texto_precio(), "$40.000 COP")

    def test_constantes_whatsapp_usan_precio_de_settings(self):
        self.assertIn(texto_precio(), CONFIRMAR_PAGO_TEXTO)
        self.assertIn(texto_precio(), POST_PDF_OPCIONES)

    def test_pagina_pago_muestra_precio_dinamico(self):
        settings.mercadopago_amount = 29000.0
        html = _pagina_pago("aviso")
        self.assertIn("$29.000 COP", html)
        settings.mercadopago_amount = 40000.0
        html = _pagina_pago("aviso")
        self.assertIn("$40.000 COP", html)

    def test_pagina_pago_es_mobile_first(self):
        """La página de pago se ve bien en celulares: viewport meta, texto grande
        y botón con área táctil amplia (regresión: sin viewport el navegador móvil
        encoge el texto y se ve diminuto)."""
        html = _pagina_pago("aviso")
        self.assertIn('name="viewport"', html)
        self.assertIn("font-size:19px", html)
        self.assertIn("font-size:18px", html)
        self.assertIn("min-height:60px", html)


if __name__ == "__main__":
    unittest.main()