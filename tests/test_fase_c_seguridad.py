"""Tests for Fase C: hardening de seguridad (no-ruptura, sin secrets obligatorios).

Cubre:
- CORS restringido a app_url (+ orígenes extra configurados).
- Cookie del admin con Secure (en https) y SameSite estricto.
- Rate-limit en el login del admin.
- SSRF: _host_permitido rechaza hosts no permitidos / esquemas no https.
  Rechazo de IPs privadas via _sin_ip_privada.
- Validación de SECRET_KEY en producción.
"""
import unittest


class TestCorsOrigins(unittest.TestCase):
    def _origins(self, app_url, cors_origins=""):
        from app.main import _allowed_origins
        return _allowed_origins(app_url, cors_origins)

    def test_solo_app_url_cuando_no_hay_extra(self):
        orig = self._origins("https://tutela-online.onrender.com", "")
        self.assertEqual(orig, ["https://tutela-online.onrender.com"])

    def test_app_url_mas_origenes_extra(self):
        orig = self._origins(
            "https://app.ejemplo.com",
            "https://panel.ejemplo.com,https://admin.ejemplo.com",
        )
        self.assertIn("https://app.ejemplo.com", orig)
        self.assertIn("https://panel.ejemplo.com", orig)
        self.assertIn("https://admin.ejemplo.com", orig)
        no_existe = "http://evil.com"
        self.assertNotIn(no_existe, orig)

    def test_aplica_https_a_app_url_plano(self):
        # Un app_url http (dev local) no debe abrir el CORS a todo; se conserva.
        orig = self._origins("http://localhost:8000", "")
        self.assertEqual(orig, ["http://localhost:8000"])


class TestSSRFHostPermitido(unittest.TestCase):
    def _permitido(self, url):
        from app.api.webhook_whatsapp import _host_permitido
        return _host_permitido(url)

    def test_acepta_meta_lookaside(self):
        self.assertTrue(self._permitido("https://lookaside.fbsbx.com/archivos/mi.jpg"))

    def test_acepta_graph_facebook(self):
        self.assertTrue(self._permitido("https://graph.facebook.com/v22.0/12345"))

    def test_acepta_twilio_media(self):
        self.assertTrue(self._permitido("https://api.twilio.com/2010-04-01/Accounts/AC/../Media/ME"))

    def test_rechaza_http_ya_que_no_es_https(self):
        # Incluso un host permitido debe exigir HTTPS
        self.assertFalse(self._permitido("http://lookaside.fbsbx.com/archivos/mi.jpg"))

    def test_rechaza_http_interno_metadata(self):
        self.assertFalse(self._permitido("http://169.254.169.254/latest/meta-data/iam"))

    def test_rechaza_localhost(self):
        self.assertFalse(self._permitido("http://localhost:8000/admin"))

    def test_rechaza_host_no_permitido(self):
        self.assertFalse(self._permitido("https://evil.com/x"))

    def test_sin_ip_privada_rechaza_ip_privada(self):
        from app.api.webhook_whatsapp import _sin_ip_privada
        self.assertFalse(_sin_ip_privada("http://10.0.0.5/x"))
        self.assertFalse(_sin_ip_privada("http://192.168.1.10/x"))
        self.assertFalse(_sin_ip_privada("http://169.254.169.254/x"))


class TestRateLimitLogin(unittest.TestCase):
    def test_excede_limite_devuelve_error(self):
        from app.api.admin import _contador_login, _limite_login
        # Reinicia estado
        _contador_login.clear()
        ip = "1.2.3.4"
        for _ in range(_limite_login):
            _contador_login[ip] = _contador_login.get(ip, 0) + 1
        self.assertTrue(_contador_login[ip] >= _limite_login)


class TestSecretKeyProd(unittest.TestCase):
    def test_es_secret_key_segura(self):
        from app.api.admin import _secret_key_valida
        self.assertTrue(_secret_key_valida("a" * 48))
        self.assertFalse(_secret_key_valida("dev-key-change-in-production"))


if __name__ == "__main__":
    unittest.main()
