"""Regresión: un `from` vacío del webhook chegaba hasta Meta como `to: ""`.

Síntoma en producción: el bot "respondía" a un número que nunca avanzaba y en
Render aparecía

    Meta botones RECHAZADO to=*** http=400
    body={"error":{"message":"The parameter to is required.","code":100}}

y además se creaban usuarios/tutelas con el teléfono en blanco.

Aquí se fija el comportamiento en las tres barreras: entrada del webhook, punto
único `procesar_mensaje` y servicio de envío.
"""
import asyncio
import unittest
from unittest import mock

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.api import webhook_whatsapp
from app.database import Base, get_session
from app.main import app
from app.models.tutela import Tutela
from app.models.user import User
from app.services import whatsapp_service


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


class TestNumeroUtil(unittest.TestCase):
    def test_detecta_telefonos_validos(self):
        for valor in ("573045382925", "+573045382925", "whatsapp:+573045382925", "573045382925 "):
            with self.subTest(valor=valor):
                self.assertTrue(webhook_whatsapp._numero_util(valor))

    def test_rechaza_valores_sin_digitos(self):
        for valor in ("", "   ", None, "whatsapp:", "+", "+ "):
            with self.subTest(valor=valor):
                self.assertFalse(webhook_whatsapp._numero_util(valor))


class TestProcesarMensajeSinTelefono(unittest.TestCase):
    def setUp(self):
        self.session = _nueva_sesion()
        p1 = mock.patch.object(webhook_whatsapp, "enviar_texto", return_value=True)
        p2 = mock.patch.object(webhook_whatsapp, "enviar_botones", return_value=True)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def test_aborta_sin_crear_usuario_ni_tutela(self):
        for vacio in ("", "   ", "whatsapp:", "+"):
            with self.subTest(telefono=vacio):
                resultado = asyncio.run(
                    webhook_whatsapp.procesar_mensaje(
                        self.session, vacio, "Hola", 0, "", False
                    )
                )
                self.assertFalse(resultado["ok"])
                self.assertEqual(resultado["respuestas"], [])

        usuarios = self.session.execute(select(func.count(User.id))).scalar()
        tutelas = self.session.execute(select(func.count(Tutela.id))).scalar()
        self.assertEqual(usuarios, 0, "no debe crearse un usuario sin teléfono")
        self.assertEqual(tutelas, 0, "no debe crearse una tutela sin teléfono")

    def test_no_intenta_enviar_nada(self):
        with mock.patch.object(webhook_whatsapp, "enviar_texto") as t, \
                mock.patch.object(webhook_whatsapp, "enviar_botones") as b:
            asyncio.run(webhook_whatsapp.procesar_mensaje(self.session, "", "Hola", 0, "", False))
        t.assert_not_called()
        b.assert_not_called()

    def test_telefono_valido_sigue_funcionando(self):
        resultado = asyncio.run(
            webhook_whatsapp.procesar_mensaje(
                self.session, "573045382925", "Hola", 0, "", False
            )
        )
        self.assertTrue(resultado["ok"])
        self.assertTrue(self.session.execute(select(func.count(User.id))).scalar())


class TestWebhookMetaIgnoraMensajeSinFrom(unittest.TestCase):
    def setUp(self):
        self.session = _nueva_sesion()
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

    def _post(self, mensaje: dict):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "WABA",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"phone_number_id": "1185525421321347"},
                        "messages": [mensaje],
                    },
                }],
            }],
        }
        return self.client.post("/webhook/meta", json=payload)

    def test_mensaje_sin_from_no_crea_basura(self):
        with mock.patch.object(webhook_whatsapp, "enviar_texto", return_value=True), \
                mock.patch.object(webhook_whatsapp, "enviar_botones", return_value=True):
            resp = self._post({"id": "wamid.x", "type": "text", "text": {"body": "Hola"}})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("ok"), True)
        self.assertEqual(
            self.session.execute(select(func.count(User.id))).scalar(), 0,
            "un mensaje sin 'from' no debe crear usuarios",
        )
        self.assertEqual(
            self.session.execute(select(func.count(Tutela.id))).scalar(), 0,
            "un mensaje sin 'from' no debe crear tutelas",
        )

    def test_mensaje_con_from_valido_sigue_entrando(self):
        with mock.patch.object(webhook_whatsapp, "enviar_texto", return_value=True), \
                mock.patch.object(webhook_whatsapp, "enviar_botones", return_value=True):
            resp = self._post({
                "from": "573045382925",
                "id": "wamid.y",
                "type": "text",
                "text": {"body": "Hola"},
            })

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            self.session.execute(select(func.count(User.id))).scalar(), 1,
            "un mensaje con 'from' válido debe crear el usuario",
        )


class TestServicioNoLlamaAMetaSinTelefono(unittest.TestCase):
    def test_enviar_botones_aborta(self):
        with mock.patch.object(whatsapp_service.httpx, "post") as post:
            for vacio in ("", "  ", "+", "whatsapp:"):
                with self.subTest(telefono=vacio):
                    ok = whatsapp_service.enviar_botones(vacio, "texto", [("1", "Uno")])
                    self.assertFalse(ok)
        post.assert_not_called()

    def test_enviar_texto_meta_aborta(self):
        with mock.patch.object(whatsapp_service.httpx, "post") as post:
            self.assertFalse(whatsapp_service._enviar_meta_texto("", "texto"))
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()