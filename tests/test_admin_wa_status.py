from unittest import mock
import unittest

from starlette.testclient import TestClient

from app.api.admin import SESSION_COOKIE, _crear_sesion
from app.config import settings
from app.main import app
from app.services import whatsapp_service


def _cookie():
    settings.secret_key = "test-key-fijo"
    return _crear_sesion()


class TestEstadoNumeroServicio(unittest.TestCase):
    def setUp(self):
        self._tok = settings.meta_access_token
        self._pid = settings.meta_phone_number_id

    def tearDown(self):
        settings.meta_access_token = self._tok
        settings.meta_phone_number_id = self._pid

    def test_sin_configurar_devuelve_configurado_false(self):
        settings.meta_access_token = ""
        settings.meta_phone_number_id = ""
        with mock.patch.object(whatsapp_service.httpx, "get") as get:
            res = whatsapp_service.consultar_estado_numero()
        get.assert_not_called()
        self.assertIs(res["configurado"], False)

    def test_consulta_exitosa_devuelve_estado(self):
        settings.meta_access_token = "tok"
        settings.meta_phone_number_id = "PH123"
        payload = {
            "display_phone_number": "+57 310 6386975",
            "verified_name": "TutelApp",
            "code_verification_status": "VERIFIED",
        }
        with mock.patch.object(whatsapp_service.httpx, "get") as get:
            get.return_value = mock.Mock(is_success=True, json=lambda: payload)
            res = whatsapp_service.consultar_estado_numero()
        url = get.call_args.args[0]
        self.assertIn("PH123", url)
        self.assertIn("code_verification_status", url)
        self.assertEqual(res["code_verification_status"], "VERIFIED")
        self.assertEqual(res["verified_name"], "TutelApp")
        self.assertIs(res["configurado"], True)

    def test_error_de_red_no_lanza_y_reporta_error(self):
        settings.meta_access_token = "tok"
        settings.meta_phone_number_id = "PH123"
        class FakeResp:
            is_success = False
            status_code = 500
            text = "boom"
        with mock.patch.object(whatsapp_service.httpx, "get") as get:
            get.return_value = FakeResp()
            res = whatsapp_service.consultar_estado_numero()
        self.assertIs(res["configurado"], True)
        self.assertEqual(res["http_status"], 500)
        res2 = None
        with mock.patch.object(whatsapp_service.httpx, "get") as get:
            get.side_effect = Exception("timeout")
            res2 = whatsapp_service.consultar_estado_numero()
        self.assertIs(res2["configurado"], True)
        self.assertIn("error", res2)


class TestEndpointWaStatus(unittest.TestCase):
    def setUp(self):
        settings.admin_password = "test-password"
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        settings.admin_password = ""

    def test_sin_auth_devuelve_401(self):
        self.client.cookies.clear()
        resp = self.client.get("/admin/api/wa-status")
        self.assertEqual(resp.status_code, 401)

    def test_con_auth_devuelve_estado_del_servicio(self):
        self.client.cookies.set(SESSION_COOKIE, _cookie())
        with mock.patch.object(whatsapp_service, "consultar_estado_numero") as consultar:
            consultar.return_value = {"configurado": True, "code_verification_status": "EXPIRED"}
            resp = self.client.get("/admin/api/wa-status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["code_verification_status"], "EXPIRED")


if __name__ == "__main__":
    unittest.main()