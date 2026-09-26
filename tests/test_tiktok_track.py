"""Tests para el canal server-side (Events API 2.0) del pixel de TikTok.

El flujo dedup es: el clic en wa.me genera un ``event_id`` en el navegador;
ese mismo id se usa en ``ttq.track('Contact', {...}, {event_id})`` (Pixel SDK)
y en el POST a ``/api/tiktok/track`` (sendBeacon) → el servidor reenvía a
Events API con el mismo ``event_id``. TikTok deduplica con  (event_id, event,
pixel_code) idénticos.

Cubre:
- ``payload_evento``: estructura del body para /event/track/ de Events API 2.0.
- ``enviar_evento_tiktok``: tolerante, no crashea, usa Access-Token, no loguea secretos.
- GET /api/tiktok saluda (health del pixel).
- POST /api/tiktok/track: valida entrada, rate limit por IP, no expone el token.
"""
import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from starlette.testclient import TestClient

from app.config import settings
from app.main import app

PIXEL_ID = "DARUU0RC77U88MSO7QUG"


def _flush_rate_limit():
    from app.api import tiktok as tiktok_api

    tiktok_api._LIMIT_POR_IP.clear()


class TestPayloadEvento(unittest.TestCase):
    def test_estructura_eventos_api_2_0(self):
        from app.services import tiktok_service

        settings.tiktok_pixel_id = PIXEL_ID
        settings.tiktok_test_event_code = ""
        payload = tiktok_service._payload_evento(
            event="Contact",
            event_id="abc-123",
            event_time=1712345678,
        )
        self.assertEqual(payload["event_source"], "web")
        self.assertEqual(payload["event_source_id"], PIXEL_ID)
        self.assertNotIn("test_event_code", payload)
        evento = payload["data"][0]
        self.assertEqual(evento["event"], "Contact")
        self.assertEqual(evento["event_time"], 1712345678)
        self.assertEqual(evento["event_id"], "abc-123")
        settings.tiktok_pixel_id = ""

    def test_omite_campos_empty_en_user(self):
        from app.services import tiktok_service

        settings.tiktok_pixel_id = PIXEL_ID
        settings.tiktok_test_event_code = ""
        payload = tiktok_service._payload_evento("Contact", "e-1", 1712345678)
        user = payload["data"][0]["user"]
        self.assertNotIn("ttclid", user)
        self.assertNotIn("ttp", user)
        settings.tiktok_pixel_id = ""

    def test_incluye_ttclid_y_ttp(self):
        from app.services import tiktok_service

        settings.tiktok_pixel_id = PIXEL_ID
        settings.tiktok_test_event_code = ""
        payload = tiktok_service._payload_evento(
            "Contact", "e-2", 1712345678, ttclid="clid-1", ttp="cp-1"
        )
        user = payload["data"][0]["user"]
        self.assertEqual(user["ttclid"], "clid-1")
        self.assertEqual(user["ttp"], "cp-1")
        settings.tiktok_pixel_id = ""

    def test_incluye_test_event_code_si_configurado(self):
        from app.services import tiktok_service

        settings.tiktok_pixel_id = PIXEL_ID
        settings.tiktok_test_event_code = "TEST-CODIGO-123"
        payload = tiktok_service._payload_evento("Contact", "e-3", 1712345678)
        self.assertEqual(payload["test_event_code"], "TEST-CODIGO-123")
        settings.tiktok_test_event_code = ""
        settings.tiktok_pixel_id = ""


class TestEnviarEventoTiktok(unittest.TestCase):
    def test_con_token_hace_post_y_devuelve_ok(self):
        from app.services import tiktok_service

        async def _fake_post(self, url, json=None, headers=None):
            self.url = url
            self.json = json
            self.headers = headers
            return SimpleNamespace(status_code=200, json=lambda: {"code": 0, "message": "OK"})

        class _FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            post = _fake_post

        settings.tiktok_access_token = "token-secreto"
        settings.tiktok_pixel_id = PIXEL_ID
        with mock.patch.object(tiktok_service.httpx, "AsyncClient", _FakeClient):
            ok = asyncio.run(
                tiktok_service.enviar_evento_tiktok(
                    "Contact", "e-1", 1712345678, ttclid="clid-1", ttp="cp-1"
                )
            )
        self.assertTrue(ok)
        settings.tiktok_access_token = ""
        settings.tiktok_pixel_id = ""

    def test_error_red_no_crashea(self):
        from app.services import tiktok_service

        async def _boom(self, url, json=None, headers=None):
            raise RuntimeError("red caida")

        class _FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            post = _boom

        settings.tiktok_access_token = "token-secreto"
        settings.tiktok_pixel_id = PIXEL_ID
        with mock.patch.object(tiktok_service.httpx, "AsyncClient", _FakeClient):
            ok = asyncio.run(
                tiktok_service.enviar_evento_tiktok("Contact", "e-1", 1712345678)
            )
        self.assertFalse(ok, "No debe lanzar excepción ni devolver True en error de red")
        settings.tiktok_access_token = ""
        settings.tiktok_pixel_id = ""

    def test_respuesta_http_error_devuelve_false(self):
        from app.services import tiktok_service

        async def _fake_post(self, url, json=None, headers=None):
            return SimpleNamespace(status_code=429, json=lambda: {"code": 1001})

        class _FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            post = _fake_post

        settings.tiktok_access_token = "token-secreto"
        settings.tiktok_pixel_id = PIXEL_ID
        with mock.patch.object(tiktok_service.httpx, "AsyncClient", _FakeClient):
            ok = asyncio.run(
                tiktok_service.enviar_evento_tiktok("Contact", "e-1", 1712345678)
            )
        self.assertFalse(ok)
        settings.tiktok_access_token = ""
        settings.tiktok_pixel_id = ""

    def test_sin_token_no_hace_ningun_post(self):
        from app.services import tiktok_service

        settings.tiktok_access_token = ""
        settings.tiktok_pixel_id = PIXEL_ID
        with mock.patch.object(tiktok_service.httpx, "AsyncClient") as m:
            ok = asyncio.run(
                tiktok_service.enviar_evento_tiktok("Contact", "e-1", 1712345678)
            )
        self.assertFalse(ok)
        m.assert_not_called()
        settings.tiktok_pixel_id = ""

    def test_url_y_cabeceras_correctas(self):
        from app.services import tiktok_service

        capturado = {}

        async def _fake_post(self, url, json=None, headers=None):
            capturado["url"] = url
            capturado["json"] = json
            capturado["headers"] = headers
            return SimpleNamespace(status_code=200, json=lambda: {"code": 0})

        class _FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            post = _fake_post

        settings.tiktok_access_token = "token-secreto-xyz"
        settings.tiktok_pixel_id = PIXEL_ID
        with mock.patch.object(tiktok_service.httpx, "AsyncClient", _FakeClient):
            asyncio.run(tiktok_service.enviar_evento_tiktok("Contact", "e-9", 1712345678))

        self.assertEqual(
            capturado["url"], "https://business-api.tiktok.com/open_api/v1.3/event/track/"
        )
        self.assertEqual(capturado["headers"].get("Access-Token"), "token-secreto-xyz")
        self.assertEqual(capturado["json"]["data"][0]["event_id"], "e-9")
        settings.tiktok_access_token = ""
        settings.tiktok_pixel_id = ""


class TestEndpointsTikTok(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        settings.tiktok_access_token = ""
        settings.tiktok_pixel_id = PIXEL_ID

    @classmethod
    def tearDownClass(cls):
        settings.tiktok_access_token = ""
        settings.tiktok_pixel_id = ""
        cls.client.__exit__(None, None, None)
        cls.client.close()

    def setUp(self):
        _flush_rate_limit()
        settings.tiktok_access_token = ""
        settings.tiktok_pixel_id = PIXEL_ID

    def tearDown(self):
        settings.tiktok_access_token = ""
        settings.tiktok_pixel_id = ""
        _flush_rate_limit()

    def test_get_saluda_pixel(self):
        resp = self.client.get("/api/tiktok")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("tiktok"), "ok")
        self.assertEqual(data.get("pixel_id"), PIXEL_ID)
        self.assertNotIn("access_token", data)
        self.assertNotIn("token", str(data).lower())

    def test_track_valido_reenvia_payload(self):
        from app.services import tiktok_service

        recibido = {}

        async def _fake_enviar(event, event_id, event_time, ttclid=None, ttp=None):
            recibido["event"] = event
            recibido["event_id"] = event_id
            recibido["ttclid"] = ttclid
            recibido["ttp"] = ttp
            return True

        with mock.patch.object(
            tiktok_service, "enviar_evento_tiktok", side_effect=_fake_enviar
        ):
            resp = self.client.post(
                "/api/tiktok/track",
                json={
                    "event": "Contact",
                    "event_id": "beacon-1",
                    "ttclid": "clid-x",
                    "ttp": "cp-y",
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("ok"), True)
        self.assertEqual(recibido["event"], "Contact")
        self.assertEqual(recibido["event_id"], "beacon-1")
        self.assertEqual(recibido["ttclid"], "clid-x")
        self.assertEqual(recibido["ttp"], "cp-y")

    def test_track_usa_event_time_actual(self):
        """El servidor pone event_time con epoch actual (int), no confía en el cliente."""
        from app.services import tiktok_service

        recibido = {}

        async def _fake_enviar(event, event_id, event_time, ttclid=None, ttp=None):
            recibido["event_time"] = event_time
            return True

        with mock.patch.object(
            tiktok_service, "enviar_evento_tiktok", side_effect=_fake_enviar
        ):
            resp = self.client.post(
                "/api/tiktok/track",
                json={"event": "Contact", "event_id": "beacon-2"},
            )
        self.assertEqual(resp.status_code, 200)
        ahora = int(datetime.now(timezone.utc).timestamp())
        self.assertAlmostEqual(recibido["event_time"], ahora, delta=10)

    def test_track_rechaza_event_vacio_o_faltante(self):
        resp = self.client.post("/api/tiktok/track", json={"event_id": "x"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json().get("ok"))
        self.assertIn("error", resp.json())

        resp = self.client.post("/api/tiktok/track", json={"event": "", "event_id": "x"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json().get("ok"))

    def test_track_rechaza_event_id_faltante(self):
        resp = self.client.post("/api/tiktok/track", json={"event": "Contact"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json().get("ok"))

    def test_track_rechaza_event_id_demasiado_largo(self):
        resp = self.client.post(
            "/api/tiktok/track",
            json={"event": "Contact", "event_id": "x" * 201},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json().get("ok"))

    def test_track_rechaza_ttclid_demasiado_largo(self):
        resp = self.client.post(
            "/api/tiktok/track",
            json={"event": "Contact", "event_id": "e-1", "ttclid": "x" * 501},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json().get("ok"))

    def test_track_rate_limit_por_ip(self):
        from app.api import tiktok as tiktok_api

        # Agota el cupo del minuto
        for _ in range(tiktok_api._LIMITE_POR_MINUTO):
            r = self.client.post("/api/tiktok/track", json={"event": "Contact", "event_id": f"e-{_}"})
            self.assertEqual(r.status_code, 200)

        resp = self.client.post("/api/tiktok/track", json={"event": "Contact", "event_id": "extra"})
        self.assertEqual(resp.status_code, 429)

    def test_track_usa_epoch_ts_int(self):
        from app.services import tiktok_service

        recibido = {}

        async def _fake_enviar(event, event_id, event_time, ttclid=None, ttp=None):
            recibido["ts"] = event_time
            return True

        with mock.patch.object(tiktok_service, "enviar_evento_tiktok", side_effect=_fake_enviar):
            self.client.post("/api/tiktok/track", json={"event": "Contact", "event_id": "t-1"})
        self.assertIsInstance(recibido["ts"], int)


if __name__ == "__main__":
    unittest.main()