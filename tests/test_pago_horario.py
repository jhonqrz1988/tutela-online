"""Tests for aviso de horario hábil en la página de pago (/pago/{tutela_id}).

Cubre:
- La página informativa (sin Mercado Pago) muestra el horario de la Rama
  Judicial junto al monto.
- Con Mercado Pago configurado NO se redirige en seco: se muestra una página
  intermedia con el aviso y el enlace al checkout.
- El texto del aviso cambia según si ahora es horario hábil o no.
- La página avisa sobre el posible código de verificación por correo, muestra
  el correo del usuario y NO menciona pagos por Nequi/transferencia (solo
  Mercado Pago).
"""
import json
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.config import settings
from app.database import Base, get_session
from app.main import app
from app.models.tutela import Tutela
from app.models.user import User


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    return TestingSession()


class TestPaginaPagoHorario(unittest.TestCase):
    def _cliente_con_tutela(self, session):
        user = User(telefono="573009990002", nombre="Lina Mora", consentimiento=True)
        session.add(user)
        session.flush()
        tutela = Tutela(user_id=user.id, tipo="salud", estado="esperando_pago", datos_json="{}")
        session.add(tutela)
        session.commit()
        return tutela.id

    def _abrir(self, session, tutela_id):
        client = TestClient(app)
        client.__enter__()

        def _override_get_session():
            yield session

        app.dependency_overrides[get_session] = _override_get_session
        try:
            return client.get(f"/pago/{tutela_id}")
        finally:
            app.dependency_overrides.pop(get_session, None)
            client.__exit__(None, None, None)
            client.close()

    def test_pagina_sin_mp_muestra_horario_y_monto(self):
        """Sin Mercado Pago la página informativa incluye el aviso de horario."""
        from app.api import pagos as pagos_mod

        session = _nueva_sesion()
        tutela_id = self._cliente_con_tutela(session)
        with mock.patch.object(settings, "mercadopago_access_token", ""), \
             mock.patch.object(pagos_mod, "es_horario_habil", return_value=True):
            resp = self._abrir(session, tutela_id)

        self.assertEqual(resp.status_code, 200)
        html = resp.text.lower()
        self.assertIn("lun a vie", html, "El aviso debe mencionar los días hábiles")
        self.assertIn("8:00 am", html, "El aviso debe mencionar la hora de apertura")
        self.assertIn("4:00 pm", html, "El aviso debe mencionar el cierre de horario")
        self.assertIn("$29.000", resp.text, "Debe mostrar el monto junto al aviso")

    def test_con_mp_muestra_pagina_intermedia_con_checkout(self):
        """Con Mercado Pago, no redirect en seco; pausa con aviso y enlace al checkout."""
        from app.api import pagos as pagos_mod

        session = _nueva_sesion()
        tutela_id = self._cliente_con_tutela(session)

        with mock.patch.object(settings, "mercadopago_access_token", "TEST-TOKEN"), \
             mock.patch.object(
                 pagos_mod, "crear_preferencia_checkout",
                 return_value={"init_point": "https://checkout.mercadopago.com/pago/ABC123"},
             ), \
             mock.patch.object(pagos_mod, "es_horario_habil", return_value=False):
            resp = self._abrir(session, tutela_id)

        self.assertEqual(resp.status_code, 200, "No debe redirigir en seco con 302")
        html = resp.text.lower()
        self.assertIn("lun a vie", html, "La página intermedia debe avisar el horario")
        self.assertIn("próximo día hábil", html, "Si no hay horario hábil debe avisar la espera")
        self.assertIn("checkout.mercadopago.com/pago/abc123", html.lower(),
                      "Debe ofrecer el enlace para continuar al checkout")

    def test_con_mp_incluye_aviso_codigo_correo_y_solo_mercadopago(self):
        """La página pide compartir el código de verificación del correo tras pagar
        y NO menciona Nequi ni transferencia (canales inactivos)."""
        from app.api import pagos as pagos_mod

        session = _nueva_sesion()
        user = User(telefono="573009990002", nombre="Lina Mora", consentimiento=True)
        session.add(user)
        session.flush()
        tutela = Tutela(
            user_id=user.id, tipo="salud", estado="esperando_pago",
            datos_json=json.dumps({"accionante_email": "ana@correo.com"}),
        )
        session.add(tutela)
        session.commit()

        with mock.patch.object(settings, "mercadopago_access_token", "TEST-TOKEN"), \
             mock.patch.object(
                 pagos_mod, "crear_preferencia_checkout",
                 return_value={"init_point": "https://checkout.mercadopago.com/pago/XYZ"},
             ), \
             mock.patch.object(pagos_mod, "es_horario_habil", return_value=True):
            resp = self._abrir(session, tutela.id)

        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertIn("Código de verificación por correo", html,
                      "Debe avisar del posible código que llega al correo")
        self.assertIn("compártenos ese", html,
                      "Debe pedir que compartan el código por WhatsApp")
        self.assertIn("ana@correo.com", html, "Debe mostrar el correo del usuario")
        self.assertIn("únicamente", html, "Debe indicar que el pago es solo por MP")
        self.assertNotIn("Nequi", html, "Nequi no está activo y no debe mencionarse")
        self.assertNotIn("transferencia", html,
                         "Transferencia no está activa y no debe mencionarse")
        self.assertIn("font-size:16px", html, "La leyenda de pago debe verse más grande")


class TestTextoAvisoHorario(unittest.TestCase):
    def test_en_horario_radica_de_inmediato(self):
        from app.api.pagos import texto_aviso_horario
        texto = texto_aviso_horario(True)
        self.assertNotIn("próximo día hábil", texto)

    def test_fuera_de_horario_avisa_espera(self):
        from app.api.pagos import texto_aviso_horario
        texto = texto_aviso_horario(False)
        self.assertIn("próximo día hábil", texto)


if __name__ == "__main__":
    unittest.main()