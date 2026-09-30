"""Rastreo del origen real de cada conversación (ad_id de Meta).

Los anuncios click-to-WhatsApp abren el chat con el usuario, pero el webhook
descartaba el bloque ``referral`` (ad_id, source_id, headline) y el
``metadata.phone_number_id`` del payload. Sin eso no había forma de:

- reconciliar "conversaciones que reporta Meta" vs "mensajes que llegó al bot",
- detectar mensajes que entran por un número receptor distinto al configurado.

Estos tests fijan que el webhook persiste ese origen en
``MensajeWhatsApp.metadata_json`` y que el servicio lo reporta.
"""
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.api import webhook_whatsapp
from app.database import Base, get_session
from app.main import app
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


def _payload(referral=None, phone_number_id="1157524497451238"):
    msg = {
        "from": "573001112223",
        "id": "wamid.orig",
        "timestamp": "1700000000",
        "type": "text",
        "text": {"body": "Hola"},
    }
    if referral:
        msg["referral"] = referral
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "0",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "phone_number_id": phone_number_id,
                                "display_phone_number": "15551540154",
                            },
                            "messages": [msg],
                        }
                    }
                ],
            }
        ],
    }


class TestAislarOrigen(unittest.TestCase):
    """``_aislar_origen`` extrae el origen de cada mensaje del payload de Meta."""

    def test_extrae_ad_id_y_phone_number_id(self):
        origen = webhook_whatsapp._aislar_origen(
            {
                "metadata": {
                    "phone_number_id": "999",
                    "display_phone_number": "1555",
                },
                "messages": [
                    {
                        "from": "573001112223",
                        "type": "text",
                        "text": {"body": "Hola"},
                        "referral": {
                            "ad_id": "AD777",
                            "source_id": "SID",
                            "headline": "Titulo",
                            "source_type": "ad",
                        },
                    }
                ],
            }
        )
        self.assertEqual(len(origen), 1)
        self.assertEqual(origen[0]["telefono"], "573001112223")
        self.assertEqual(origen[0]["ad_id"], "AD777")
        self.assertEqual(origen[0]["phone_number_id"], "999")
        self.assertEqual(origen[0]["source_id"], "SID")
        self.assertEqual(origen[0]["headline"], "Titulo")

    def test_sin_referral_deja_ad_id_none(self):
        origen = webhook_whatsapp._aislar_origen(
            {
                "metadata": {"phone_number_id": "999"},
                "messages": [
                    {
                        "from": "573009998877",
                        "type": "text",
                        "text": {"body": "Hola"},
                    }
                ],
            }
        )
        self.assertEqual(len(origen), 1)
        self.assertIsNone(origen[0].get("ad_id"))
        self.assertEqual(origen[0]["phone_number_id"], "999")

    def test_recorta_campos_largos_del_referral(self):
        origen = webhook_whatsapp._aislar_origen(
            {
                "metadata": {"phone_number_id": "999"},
                "messages": [
                    {
                        "from": "573001112223",
                        "type": "text",
                        "text": {"body": "Hola"},
                        "referral": {"ad_id": "A" * 300, "headline": "H" * 300},
                    }
                ],
            }
        )
        self.assertEqual(len(origen[0]["ad_id"]), 50)
        self.assertEqual(len(origen[0]["headline"]), 120)

    def test_ignora_campos_no_texto_del_referral(self):
        """Solo se extraen campos conocidos; campos raros se descartan."""
        origen = webhook_whatsapp._aislar_origen(
            {
                "metadata": {"phone_number_id": "999"},
                "messages": [
                    {
                        "from": "573001112223",
                        "type": "text",
                        "text": {"body": "Hola"},
                        "referral": {"ad_id": "AD1", "raro": {"anidado": 1}},
                    }
                ],
            }
        )
        self.assertNotIn("raro", origen[0])
        json.dumps(origen[0])  # serializable

    def test_no_rompe_con_mensaje_sin_from_ni_metadata(self):
        origen = webhook_whatsapp._aislar_origen({"messages": [{}]})
        self.assertEqual(origen, [])


class TestWebhookPersisteOrigen(unittest.TestCase):
    """El endpoint /webhook/meta guarda el origen en MensajeWhatsApp.metadata_json."""

    def setUp(self):
        self.S = _nueva_sesion()
        self.session = self.S()
        self.client = TestClient(app)
        self.client.__enter__()

        def _override_get_session():
            yield self.session

        app.dependency_overrides[get_session] = _override_get_session

    def tearDown(self):
        app.dependency_overrides.pop(get_session, None)
        self.client.__exit__(None, None, None)
        self.client.close()

    def _post(self, payload):
        with mock.patch.object(
            webhook_whatsapp, "_verify_meta_signature", return_value=True
        ), mock.patch.object(
            webhook_whatsapp, "enviar_texto", return_value=True
        ), mock.patch.object(
            webhook_whatsapp, "enviar_botones", return_value=True
        ):
            return self.client.post("/webhook/meta", json=payload)

    def _ultimo_mensaje(self):
        return (
            self.session.query(MensajeWhatsApp)
            .order_by(MensajeWhatsApp.id.desc())
            .first()
        )

    def test_mensaje_con_referral_persiste_ad_id(self):
        resp = self._post(
            _payload(
                referral={
                    "ad_id": "23889771234567890",
                    "source_id": "1234567890",
                    "headline": "Crea tu tutela",
                    "source_type": "ad",
                }
            )
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))
        meta = json.loads(self._ultimo_mensaje().metadata_json)
        self.assertEqual(meta["ad_id"], "23889771234567890")
        self.assertEqual(meta["phone_number_id"], "1157524497451238")

    def test_mensaje_sin_referral_persiste_solo_phone_number_id(self):
        self._post(_payload())
        meta = json.loads(self._ultimo_mensaje().metadata_json)
        self.assertEqual(meta["phone_number_id"], "1157524497451238")
        self.assertIsNone(meta.get("ad_id"))


class TestReportePorAnuncioYNumeros(unittest.TestCase):
    """Reporte de conversaciones por anuncio y por número receptor."""

    def _crear(self, filas):
        S = _nueva_sesion()
        session = S()
        for numero, meta in filas:
            session.add(
                MensajeWhatsApp(
                    from_number=numero,
                    body="hola",
                    metadata_json=json.dumps(meta) if meta else None,
                )
            )
        session.commit()
        return S, session

    def _rango(self):
        return (
            datetime.now(timezone.utc) - timedelta(days=1),
            datetime.now(timezone.utc) + timedelta(days=1),
        )

    def test_conversaciones_por_anuncio_agrupa_por_ad_id(self):
        S, session = self._crear(
            [
                ("573000000001", {"ad_id": "AD1"}),
                ("573000000001", {"ad_id": "AD1"}),
                ("573000000002", {"ad_id": "AD1"}),
                ("573000000003", {"ad_id": "AD2"}),
                ("573000000004", None),
            ]
        )
        inicio, fin = self._rango()
        reporte = visitas_service.conversaciones_por_anuncio(session, inicio, fin)
        por_id = {r["ad_id"]: r["conversaciones"] for r in reporte}
        self.assertEqual(por_id.get("AD1"), 2)
        self.assertEqual(por_id.get("AD2"), 1)
        session.close()

    def test_conversaciones_por_anuncio_ignora_sin_ad_id(self):
        S, session = self._crear(
            [
                ("573000000001", None),
                ("573000000002", {"otro": "campo"}),
            ]
        )
        inicio, fin = self._rango()
        self.assertEqual(visitas_service.conversaciones_por_anuncio(session, inicio, fin), [])
        session.close()

    def test_conversaciones_por_anuncio_ignora_fuera_de_rango(self):
        S, session = self._crear([("573000000001", {"ad_id": "AD1"})])
        msg = session.query(MensajeWhatsApp).first()
        msg.created_at = datetime.now(timezone.utc) - timedelta(days=90)
        session.commit()
        inicio, fin = self._rango()
        self.assertEqual(visitas_service.conversaciones_por_anuncio(session, inicio, fin), [])
        session.close()

    def test_numeros_receptores_lista_cada_phone_number_id(self):
        S, session = self._crear(
            [
                ("573000000001", {"phone_number_id": "111"}),
                ("573000000001", {"phone_number_id": "111"}),
                ("573000000002", {"phone_number_id": "222"}),
            ]
        )
        inicio, fin = self._rango()
        receptores = visitas_service.numeros_receptores(session, inicio, fin)
        ids = {r["phone_number_id"]: r["conversaciones"] for r in receptores}
        self.assertEqual(ids.get("111"), 1)
        self.assertEqual(ids.get("222"), 1)
        session.close()

    def test_numeros_receptores_sin_metadata_no_aparece(self):
        S, session = self._crear([("573000000001", None)])
        inicio, fin = self._rango()
        self.assertEqual(visitas_service.numeros_receptores(session, inicio, fin), [])
        session.close()


class TestPanelMuestraOrigenDeConversaciones(unittest.TestCase):
    """El dashboard muestra conversaciones por anuncio y avisa si entra tráfico
    por un número receptor distinto del configurado (anuncio/WABA mal apuntado)."""

    def setUp(self):
        from app.api.admin import SESSION_COOKIE, _crear_sesion
        from app.config import settings

        settings.admin_password = "test-password"
        settings.secret_key = "test-key-fijo"
        # El número configurado NO debe coincidir con los del reporte, para que
        # el panel tenga que marcar tráfico entrante por otro número.
        settings.meta_phone_number_id = "555000"
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

    def test_panel_muestra_conversaciones_por_anuncio(self):
        self.session.add(
            MensajeWhatsApp(
                from_number="573099990001",
                body="hola",
                metadata_json=json.dumps({"ad_id": "AD123", "headline": "Crea tu tutela"}),
            )
        )
        self.session.commit()

        with mock.patch("app.api.admin.conversaciones_por_anuncio") as m_ads, \
             mock.patch("app.api.admin.numeros_receptores", return_value=[]):
            m_ads.return_value = [
                {"ad_id": "AD123", "conversaciones": 4, "mensajes": 9, "headline": "Crea tu tutela"}
            ]
            resp = self.client.get("/admin")

        self.assertEqual(resp.status_code, 200)
        m_ads.assert_called_once()
        self.assertIn("AD123", resp.text, "Debe verse el ad_id del anuncio")
        self.assertIn("por anuncio", resp.text)

    def test_panel_avisa_numero_receptor_distinto(self):
        with mock.patch("app.api.admin.conversaciones_por_anuncio", return_value=[]), \
             mock.patch("app.api.admin.numeros_receptores") as m_num:
            m_num.return_value = [
                {"phone_number_id": "111", "conversaciones": 5},
                {"phone_number_id": "999", "conversaciones": 2},
            ]
            resp = self.client.get("/admin")

        self.assertEqual(resp.status_code, 200)
        m_num.assert_called_once()
        # 999 no es el phone_number_id configurado en el test → debe alertar.
        self.assertIn("distinto", resp.text.lower())


if __name__ == "__main__":
    unittest.main()