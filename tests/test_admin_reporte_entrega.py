"""El reporte de entrega debe servirse autenticado y con los datos reales.

Cubre que el panel no quede abierto por descuido (es un endpoint del admin) y que
la plantilla se renderice con los números que se insertaron.
"""
import unittest

import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.api.admin import SESSION_COOKIE, _crear_sesion
from app.config import settings
from app.database import Base, get_session
from app.main import app
from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import EnvioWhatsApp


class TestReporteEntregaHTTP(unittest.TestCase):
    def setUp(self):
        settings.secret_key = "test-key-reporte"
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.session.add(EnvioWhatsApp(
            wamid="wamid.rep1", from_number="573000000123", estado="fallido",
            error_code=131026,
        ))
        self.session.commit()

        self.client = TestClient(app)
        self.client.__enter__()

        def _override():
            yield self.session

        app.dependency_overrides[get_session] = _override

        def _teardown():
            app.dependency_overrides.pop(get_session, None)
            self.client.__exit__(None, None, None)
            self.client.close()

        self.addCleanup(_teardown)

    def test_html_exige_login(self):
        resp = self.client.get("/admin/reporte-entrega", follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303, 401))

    def test_html_se_sirve_autenticado(self):
        resp = self.client.get(
            "/admin/reporte-entrega",
            cookies={SESSION_COOKIE: _crear_sesion()},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Reporte de entrega", resp.text)

    def test_json_se_sirve_autenticado(self):
        resp = self.client.get(
            "/admin/api/reporte-entrega",
            cookies={SESSION_COOKIE: _crear_sesion()},
        )
        self.assertEqual(resp.status_code, 200)
        cuerpo = resp.json()
        self.assertIn("resumen", cuerpo)
        self.assertIn("sin_respuesta", cuerpo)
        self.assertIn("basura_del_bug", cuerpo)
        # El fallo insertado debe verse en el histórico de errores de Meta.
        codigos = [e["codigo"] for e in cuerpo["errores_meta"]]
        self.assertIn(131026, codigos)

    def test_dias_invalido_no_rompe(self):
        # "abc" y "0" caen al valor por defecto (0 es falsy); el resto se acota a [1, 90].
        casos = {"abc": 7, "0": 7, "-1": 1, "9999": 90, "30": 30}
        for valor, esperado in casos.items():
            with self.subTest(dias=valor):
                resp = self.client.get(
                    f"/admin/api/reporte-entrega?dias={valor}",
                    cookies={SESSION_COOKIE: _crear_sesion()},
                )
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["ventana_dias"], esperado)

    def test_html_con_dias_invalido_tambien(self):
        resp = self.client.get(
            "/admin/reporte-entrega?dias=abc",
            cookies={SESSION_COOKIE: _crear_sesion()},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 200)


class TestAtrapadosHTTP(unittest.TestCase):
    """Los endpoints de atrapados/desbloquear deben exigir login."""

    def setUp(self):
        settings.secret_key = "test-key-atrapados"
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()

        user = User(telefono="573000000999", estado="activo", consentimiento=True)
        self.session.add(user)
        self.session.commit()
        tutela = Tutela(
            user_id=user.id, tipo="salud", estado="recogiendo_datos",
            datos_json="{}", created_at=datetime.datetime(2020, 1, 1),
        )
        self.session.add(tutela)
        self.session.commit()

        self.client = TestClient(app)
        self.client.__enter__()

        def _override():
            yield self.session

        app.dependency_overrides[get_session] = _override

        def _teardown():
            app.dependency_overrides.pop(get_session, None)
            self.client.__exit__(None, None, None)
            self.client.close()

        self.addCleanup(_teardown)

    def test_listar_atrapados_exige_login(self):
        resp = self.client.get("/admin/api/atrapados", follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303, 401))

    def test_listar_atrapados_devuelve_el_atrapado(self):
        resp = self.client.get(
            "/admin/api/atrapados",
            cookies={SESSION_COOKIE: _crear_sesion()},
        )
        self.assertEqual(resp.status_code, 200)
        cuerpo = resp.json()
        self.assertTrue(cuerpo["ok"])
        telefonos = [a["telefono"] for a in cuerpo["atrapados"]]
        self.assertIn("573000000999", telefonos)

    def test_desbloquear_exige_login(self):
        resp = self.client.post("/admin/api/atrapados/1/desbloquear", follow_redirects=False)
        self.assertIn(resp.status_code, (302, 303, 401))

    def test_desbloquear_reinicia_el_usuario(self):
        resp = self.client.post(
            "/admin/api/atrapados/1/desbloquear",
            cookies={SESSION_COOKIE: _crear_sesion()},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        # La tutela debe estar borrada.
        self.assertEqual(self.session.execute(select(Tutela)).scalars().all(), [])
        user = self.session.execute(select(User)).scalars().first()
        self.assertEqual(user.estado, "nuevo")

    def test_la_pagina_muestra_los_atrapados(self):
        resp = self.client.get(
            "/admin/reporte-entrega",
            cookies={SESSION_COOKIE: _crear_sesion()},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Desbloquear", resp.text)
        self.assertIn("573000000999", resp.text)


if __name__ == "__main__":
    unittest.main()