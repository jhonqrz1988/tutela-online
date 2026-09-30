"""Entrega real de la respuesta del bot y tipos de mensaje no soportados.

Síntoma reportado: "hay usuarios (probado en iPhone) donde el flujo no inicia".
Dos causasInstrumentadas acá:

1. **Envíos fallidos invisibles**: ``_r``/``_b`` ignoraban el ``bool`` de
   ``enviar_texto``/``enviar_botones``. Con el número ``EXPIRED`` Meta rechaza
   la respuesta y el log se perdía: la BD parecía una conversación normal
   (mensaje entrante guardado) aunque el usuario nunca recibiera nada.

2. **Tipos de mensaje no parseados**: el primer mensaje desde un anuncio puede
   llegar como ``interactive`` con ``button`` (no ``button_reply``), o como
   ``reaction``/``sticker``/``location``/``unsupported``. El parser solo leía
   ``button_reply``/``list_reply``, dejando ``body_text`` vacío y sin forma de
   reanudar el flujo.

Aquí se fija: cada mensaje registra si la respuesta fue entregada o falló
(``envio_estado``) y el panel expone "Mensajes entrantes sin respuesta".
"""
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
from app.models.user import User
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


class TestRegistroEnvio(unittest.TestCase):
    """El mensaje entrante queda marcado como entregado/fallido según el envío."""

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

    def _payload(self, msg):
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "0",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": "1157524497451238"},
                                "messages": [msg],
                            }
                        }
                    ],
                }
            ],
        }

    def _post(self, payload, envio_ok):
        with mock.patch.object(
            webhook_whatsapp, "_verify_meta_signature", return_value=True
        ), mock.patch.object(
            webhook_whatsapp, "enviar_texto", return_value=envio_ok
        ), mock.patch.object(
            webhook_whatsapp, "enviar_botones", return_value=envio_ok
        ):
            return self.client.post("/webhook/meta", json=payload)

    def _mensajes_estado(self):
        return [
            (m.body, m.envio_estado)
            for m in self.session.query(MensajeWhatsApp).order_by(MensajeWhatsApp.id).all()
        ]

    def test_envio_exitoso_marca_entregado(self):
        self._post(
            self._payload(
                {"from": "573000000001", "id": "wamid.1", "type": "text", "text": {"body": "Hola"}}
            ),
            envio_ok=True,
        )
        estados = self._mensajes_estado()
        self.assertTrue(estados, "Debe registrarse el mensaje entrante")
        for _, estado in estados:
            self.assertEqual(estado, "entregado")

    def test_envio_fallido_marca_fallido(self):
        """Si Meta rechaza la respuesta (número EXPIRED), el mensaje queda 'fallido'."""
        self._post(
            self._payload(
                {"from": "573000000002", "id": "wamid.2", "type": "text", "text": {"body": "Hola"}}
            ),
            envio_ok=False,
        )
        estados = self._mensajes_estado()
        self.assertTrue(estados)
        for _, estado in estados:
            self.assertEqual(estado, "fallido")

    def test_varios_mensajes_mezcla_estados(self):
        self._post(
            self._payload(
                {"from": "573000000003", "id": "wamid.3", "type": "text", "text": {"body": "Hola"}}
            ),
            envio_ok=False,
        )
        self._post(
            self._payload(
                {"from": "573000000004", "id": "wamid.4", "type": "text", "text": {"body": "Hola"}}
            ),
            envio_ok=True,
        )
        estados = {m.from_number: m.envio_estado for m in self.session.query(MensajeWhatsApp).all()}
        self.assertEqual(estados.get("573000000003"), "fallido")
        self.assertEqual(estados.get("573000000004"), "entregado")


class TestConteoSinRespuesta(unittest.TestCase):
    """``conversaciones_sin_respuesta`` cuenta los usuarios que escribieron pero
    no recibieron respuesta del bot (el síntoma del número EXPIRED)."""

    def _crear(self, filas):
        S = _nueva_sesion()
        session = S()
        for numero, estado in filas:
            session.add(
                MensajeWhatsApp(from_number=numero, body="hola", envio_estado=estado)
            )
        session.commit()
        return S, session

    def _rango(self):
        return (
            datetime.now(timezone.utc) - timedelta(days=1),
            datetime.now(timezone.utc) + timedelta(days=1),
        )

    def test_conta_solo_fallidos(self):
        S, session = self._crear(
            [
                ("573000000001", "fallido"),
                ("573000000001", "fallido"),
                ("573000000002", "entregado"),
            ]
        )
        inicio, fin = self._rango()
        n = visitas_service.conversaciones_sin_respuesta(session, inicio, fin)
        # solo el 000001 (2 mensajes fallidos) cuenta como conversación sin respuesta
        self.assertEqual(n, 1)
        session.close()

    def test_ignora_fuera_de_rango(self):
        S, session = self._crear([("573000000001", "fallido")])
        msg = session.query(MensajeWhatsApp).first()
        msg.created_at = datetime.now(timezone.utc) - timedelta(days=90)
        session.commit()
        inicio, fin = self._rango()
        self.assertEqual(visitas_service.conversaciones_sin_respuesta(session, inicio, fin), 0)
        session.close()


class TestParserTiposNoSoportados(unittest.TestCase):
    """Tipos que antes producían body vacío ahora se interpretan."""

    def test_interactive_button_usa_el_texto_del_boton(self):
        """Un clic en CTA de anuncio llega como interactive.button (no button_reply)."""
        origen = webhook_whatsapp._parsear_mensaje(
            {
                "from": "573001112223",
                "type": "interactive",
                "interactive": {"type": "button", "button": {"text": "Quiero info"}},
            }
        )
        # El parser preserva el texto; el flujo lo normaliza para comparar.
        self.assertEqual(origen["body_text"], "Quiero info")

    def test_cta_de_anuncio_inicia_el_flujo(self):
        """Integración: el clic en el CTA de un anuncio debe arrancar la bienvenida."""
        import asyncio

        S = _nueva_sesion()
        session = S()
        with mock.patch.object(webhook_whatsapp, "enviar_texto", return_value=True), \
                mock.patch.object(webhook_whatsapp, "enviar_botones", return_value=True):
            asyncio.run(
                webhook_whatsapp.procesar_mensaje(
                    session, "573009998887", "Quiero info", 0, "", False,
                )
            )
        user = session.query(User).filter(User.telefono == "573009998887").one()
        self.assertEqual(user.estado, "nuevo")
        session.close()

    def test_interactive_nfm_reply_se_maneja(self):
        origen = webhook_whatsapp._parsear_mensaje(
            {
                "from": "573001112223",
                "type": "interactive",
                "interactive": {"type": "nfm_reply", "nfm_reply": {"response_json": '{"a":1}'}},
            }
        )
        self.assertIn("body_text", origen)

    def test_reaction_usa_el_emoji_como_texto(self):
        origen = webhook_whatsapp._parsear_mensaje(
            {"from": "573001112223", "type": "reaction", "reaction": {"emoji": "\U0001f44d"}}
        )
        self.assertEqual(origen["body_text"], "\U0001f44d")

    def test_sticker_devuelve_marca_sticker(self):
        origen = webhook_whatsapp._parsear_mensaje(
            {"from": "573001112223", "type": "sticker", "sticker": {"id": "st1"}}
        )
        self.assertEqual(origen["body_text"], "[sticker]")

    def test_location_devuelve_marca(self):
        origen = webhook_whatsapp._parsear_mensaje(
            {"from": "573001112223", "type": "location", "location": {"latitude": 1.0, "longitude": 2.0}}
        )
        self.assertEqual(origen["body_text"], "[ubicacion]")

    def test_unsupported_devuelve_marca(self):
        origen = webhook_whatsapp._parsear_mensaje(
            {"from": "573001112223", "type": "unsupported"}
        )
        self.assertEqual(origen["body_text"], "[no soportado]")

    def test_text_normal_sigue_funcionando(self):
        origen = webhook_whatsapp._parsear_mensaje(
            {"from": "573001112223", "type": "text", "text": {"body": "Hola"}}
        )
        self.assertEqual(origen["body_text"], "Hola")

    def test_audio_marca_es_audio(self):
        origen = webhook_whatsapp._parsear_mensaje(
            {"from": "573001112223", "type": "audio", "audio": {"id": "au1"}}
        )
        self.assertTrue(origen["es_audio"])

    def test_tipo_desconocido_no_rompe(self):
        origen = webhook_whatsapp._parsear_mensaje(
            {"from": "573001112223", "type": "lo_que_sea"}
        )
        self.assertEqual(origen["body_text"], "[no soportado]")


class TestPanelMuestraSinRespuesta(unittest.TestCase):
    """El dashboard expone 'escribieron y no respondimos' + la alerta roja."""

    def setUp(self):
        from app.config import settings

        settings.admin_password = "test-password"
        settings.secret_key = "test-key-entrega"
        self.S = _nueva_sesion()
        self.session = self.S()
        self.client = TestClient(app)
        self.client.__enter__()
        from app.api.admin import SESSION_COOKIE, _crear_sesion

        self.client.cookies.set(SESSION_COOKIE, _crear_sesion())

        def _override_get_session():
            yield self.session

        app.dependency_overrides[get_session] = _override_get_session

    def tearDown(self):
        app.dependency_overrides.pop(get_session, None)
        self.client.__exit__(None, None, None)
        self.client.close()

    def test_panel_avisa_cuando_hay_usuarios_sin_respuesta(self):
        self.session.add(MensajeWhatsApp(from_number="573077700001", body="hola", envio_estado="fallido"))
        self.session.commit()

        with mock.patch("app.api.admin.contar_conversaciones", return_value=1), \
                mock.patch("app.api.admin.visitas_legacy", return_value=0):
            resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Escribieron y no respondimos", resp.text)
        self.assertIn("escribieron y no recibieron respuesta", resp.text)

    def test_panel_no_muestra_alerta_sin_fallos(self):
        self.session.add(MensajeWhatsApp(from_number="573077700002", body="hola", envio_estado="entregado"))
        self.session.commit()

        with mock.patch("app.api.admin.contar_conversaciones", return_value=1), \
                mock.patch("app.api.admin.visitas_legacy", return_value=0):
            resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Escribieron y no respondimos", resp.text)
        self.assertNotIn("escribieron y no recibieron respuesta", resp.text)


if __name__ == "__main__":
    unittest.main()