"""Tests del registro server-side de clics en wa.me (endpoint /api/click-wa).

Mide clics REALES independientes de si la conversación llega: permite distinguir
"hizo clic" (llegó al chat de WhatsApp) de "llegó el mensaje" (conversación).
"""
import unittest
from unittest import mock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.database import Base
from app.main import app
from app.models.clic import ClicWhatsApp
from app.services import visitas_service


def _flush_rate_limit():
    from app.api import clics as clics_api

    clics_api._LIMIT_POR_IP.clear()


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class TestRegistrarClic(unittest.TestCase):
    def test_clic_directo_sin_parametros(self):
        S = _nueva_sesion()
        with mock.patch.object(visitas_service, "SessionLocal", S):
            visitas_service.registrar_clic_whatsapp("", "btn-hero", "Mozilla Chrome")
        session = S()
        c = session.execute(select(ClicWhatsApp)).scalar_one()
        self.assertEqual(c.fuente, "directo")
        self.assertFalse(c.es_pauta)
        self.assertEqual(c.ubicacion, "btn-hero")
        session.close()

    def test_clic_facebook_marca_pauta(self):
        S = _nueva_sesion()
        with mock.patch.object(visitas_service, "SessionLocal", S):
            visitas_service.registrar_clic_whatsapp(
                "fbclid=abc&utm_source=fb&utm_medium=paid", "float-wa", "Mozilla Chrome"
            )
        session = S()
        c = session.execute(select(ClicWhatsApp)).scalar_one()
        self.assertEqual(c.fuente, "fb")
        self.assertEqual(c.medio, "paid")
        self.assertTrue(c.es_pauta)
        session.close()

    def test_clic_tiktok_por_ttclid(self):
        S = _nueva_sesion()
        with mock.patch.object(visitas_service, "SessionLocal", S):
            visitas_service.registrar_clic_whatsapp("ttclid=ah~xyz", "btn-precio", "Mozilla")
        session = S()
        c = session.execute(select(ClicWhatsApp)).scalar_one()
        self.assertEqual(c.fuente, "tiktok")
        self.assertTrue(c.es_pauta)
        session.close()

    def test_clic_de_bot_no_se_registra(self):
        S = _nueva_sesion()
        with mock.patch.object(visitas_service, "SessionLocal", S):
            visitas_service.registrar_clic_whatsapp("utm_source=fb", "btn-hero", "facebookexternalhit/1.1")
        session = S()
        n = session.execute(select(ClicWhatsApp)).scalars().all()
        self.assertEqual(len(n), 0, "un bot no genera clic humano medible")
        session.close()

    def test_error_de_bd_no_propaga(self):
        def siempre_falla(*args, **kwargs):
            raise RuntimeError("bd caida")

        with mock.patch.object(visitas_service, "SessionLocal", siempre_falla):
            visitas_service.registrar_clic_whatsapp("utm_source=fb", "btn-hero", "Mozilla")


class TestEndpointClic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.client.close()

    def setUp(self):
        _flush_rate_limit()

    def test_post_valido_registra_clic(self):
        S = _nueva_sesion()
        with mock.patch.object(visitas_service, "SessionLocal", S):
            resp = self.client.post(
                "/api/click-wa",
                json={"query": "utm_source=fb&fbclid=abc", "ubicacion": "btn-hero"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("ok"), True)
        session = S()
        c = session.execute(select(ClicWhatsApp)).scalar_one()
        self.assertTrue(c.es_pauta)
        self.assertEqual(c.fuente, "fb")
        session.close()

    def test_post_sin_body_usa_vacios(self):
        S = _nueva_sesion()
        with mock.patch.object(visitas_service, "SessionLocal", S):
            resp = self.client.post("/api/click-wa", json={})
        self.assertEqual(resp.status_code, 200)
        session = S()
        c = session.execute(select(ClicWhatsApp)).scalar_one()
        self.assertEqual(c.fuente, "directo")
        session.close()

    def test_query_demasiado_larga_rechazada(self):
        with mock.patch.object(visitas_service, "SessionLocal", _nueva_sesion):
            resp = self.client.post(
                "/api/click-wa",
                json={"query": "a" * 3000, "ubicacion": "btn-hero"},
            )
        self.assertEqual(resp.status_code, 400)

    def test_rate_limit_por_ip(self):
        S = _nueva_sesion()
        for _ in range(60):
            with mock.patch.object(visitas_service, "SessionLocal", S):
                self.client.post("/api/click-wa", json={"query": "", "ubicacion": "btn"})
        with mock.patch.object(visitas_service, "SessionLocal", S):
            resp = self.client.post("/api/click-wa", json={"query": "", "ubicacion": "btn"})
        self.assertEqual(resp.status_code, 429)
        session = S()
        n = len(session.execute(select(ClicWhatsApp)).scalars().all())
        session.close()
        self.assertLessEqual(n, 60)


if __name__ == "__main__":
    unittest.main()