"""Panel del embudo real de conversión en el dashboard admin.

El dashboard mezclaba métricas de distinto nivel (visitas ~ clics ~ conversaciones)
y el "Total visitas" del mes incluía registros anteriores al filtro de bots
(user_agent NULL, si clasificar), lo que parecía una conversión de pauta malísima.
Estos tests fijan:

- ``contar_conversaciones``: teléfonos distintos que escribieron al bot.
- ``visitas_clasificadas``: visitas humanas capturadas CON filtro de bots.
- ``visitas_legacy``: visitas anteriores al filtro (user_agent NULL, sin clasificar).
- El dashboard renderiza las tarjetas de conversaciones y el desglose legacy.
"""
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.api.admin import SESSION_COOKIE, _crear_sesion
from app.config import settings
from app.database import Base, get_session
from app.main import app
from app.models.tutela import Tutela
from app.models.user import User
from app.models.visita import VisitaLanding
from app.models.whatsapp import MensajeWhatsApp
from app.services import visitas_service


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class TestContarConversaciones(unittest.TestCase):
    """La métrica 'conversación' = teléfonos distintos que escribieron al bot."""

    def _agregar(self, S, numeros, dentro=True):
        session = S()
        base = datetime.now(timezone.utc)
        for num in numeros:
            m = MensajeWhatsApp(from_number=num, body="hola")
            if not dentro:
                m.created_at = base - timedelta(days=90)
            session.add(m)
        session.commit()
        session.close()

    def test_telefonos_distintos_cuentan_uno_cada_uno(self):
        S = _nueva_sesion()
        self._agregar(S, ["573000000001", "573000000001", "573000000002"])
        session = S()
        n = visitas_service.contar_conversaciones(
            session, datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc)
        )
        self.assertEqual(n, 2)
        session.close()

    def test_mensajes_fuera_de_rango_no_cuentan(self):
        S = _nueva_sesion()
        self._agregar(S, ["573000000001", "573000000002"], dentro=False)
        session = S()
        n = visitas_service.contar_conversaciones(
            session, datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc)
        )
        self.assertEqual(n, 0)
        session.close()


class TestVisitasClasificadasVsLegacy(unittest.TestCase):
    """Las visitas previas al filtro de bots (user_agent NULL) se separan del
    tráfico humano medido (es_bot=False con user_agent capturado)."""

    def _crear(self):
        S = _nueva_sesion()
        session = S()
        session.add_all([
            VisitaLanding(fuente="directo", es_bot=False, user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/123.0"),
            VisitaLanding(fuente="fb", es_bot=True, user_agent="facebookexternalhit/1.1"),
            VisitaLanding(fuente="directo", es_bot=False, user_agent=""),
        ])
        # Fila "legacy" real: la migración agregó la columna con NULL y SQLAlchemy
        # no toca el valor de las filas preexistentes (NULL de verdad).
        session.execute(
            text("INSERT INTO visitas_landing (fuente, es_bot, es_pauta, user_agent) VALUES ('directo', 0, 0, NULL)")
        )
        session.commit()
        return S

    def test_clasificadas_solo_humanas_con_ua(self):
        S = self._crear()
        session = S()
        inicio = datetime.now(timezone.utc) - timedelta(days=1)
        fin = datetime.now(timezone.utc)
        self.assertEqual(visitas_service.visitas_clasificadas(session, inicio, fin), 1)
        session.close()

    def test_legacy_solo_visitas_sin_user_agent(self):
        S = self._crear()
        session = S()
        inicio = datetime.now(timezone.utc) - timedelta(days=1)
        fin = datetime.now(timezone.utc)
        self.assertEqual(visitas_service.visitas_legacy(session, inicio, fin), 1)
        session.close()


class TestPanelMuestraEmbudo(unittest.TestCase):
    """El dashboard renderiza conversaciones (mes y 24h) y el desglose legacy."""

    def setUp(self):
        settings.admin_password = "test-password"
        settings.secret_key = "test-key-fijo"
        self.S = _nueva_sesion()
        self.session = self.S()
        self.client = TestClient(app)
        self.client.__enter__()
        self.client.cookies.set(SESSION_COOKIE, _crear_sesion())

        def _override_get_session():
            yield self.session

        app.dependency_overrides[get_session] = _override_get_session

    def tearDown(self):
        app.dependency_overrides.pop(get_session, None)
        self.client.__exit__(None, None, None)
        self.client.close()

    def test_panel_incluye_conversaciones_y_legacy(self):
        user = User(telefono="573099990001", nombre="Ana", consentimiento=True)
        self.session.add(user)
        self.session.flush()
        self.session.add(Tutela(user_id=user.id, tipo="salud", estado="recogiendo_datos"))
        self.session.add(MensajeWhatsApp(from_number="573099990001", body="hola"))
        self.session.add(MensajeWhatsApp(from_number="573099990001", body="acepto"))
        self.session.add(MensajeWhatsApp(from_number="573099990002", body="hola"))
        self.session.commit()

        with mock.patch("app.api.admin.contar_conversaciones", return_value=2), \
             mock.patch("app.api.admin.visitas_legacy", return_value=1) as m_legacy:
            resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Conversaciones", resp.text)
        self.assertIn(">2<", resp.text, "Debe verse el número de conversaciones")
        m_legacy.assert_called_once()
        self.assertIn("sin user-agent", resp.text, "Debe avisar de visitas anteriores al filtro de bots")


if __name__ == "__main__":
    unittest.main()