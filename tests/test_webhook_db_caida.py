"""El webhook debe devolver 500 (no 200) cuando la base de datos falla.

Incidente 2026-10-28: la BD del proveedor agotó su cuota y toda escritura falló.
El webhook atrapaba la excepción y respondía **200**, así que Meta consideraba el
mensaje entregado y no lo reintentaba: el usuario escribía y el bot quedaba mudo
sin que nadie se enterara.

Regla: error de BD -> 500 para que Meta reintente. Cualquier otro error del flujo
(parseo, envío, IA) sigue devolviendo 200: reintentar no lo arreglaría y solo
generaría duplicados.
"""
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.api import webhook_whatsapp
from app.database import Base, get_session
from app.main import app


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _error_bd():
    return OperationalError(
        "INSERT INTO mensajes_whatsapp ...",
        {},
        Exception("Your account or project has exceeded the quota"),
    )


class TestWebhookConBaseCaida(unittest.TestCase):
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

    def test_error_de_bd_devuelve_500_para_que_meta_reintente(self):
        mensaje = {
            "id": "wamid.bd",
            "from": "573001112233",
            "type": "text",
            "text": {"body": "hola"},
        }
        with mock.patch.object(
            webhook_whatsapp, "procesar_mensaje",
            new=mock.AsyncMock(side_effect=_error_bd()),
        ), self.assertLogs("app.api.webhook_whatsapp", level="ERROR") as logs:
            resp = self._post(mensaje)

        self.assertEqual(resp.status_code, 500)
        self.assertFalse(resp.json().get("ok"), "un 200 haría que Meta no reintente")
        self.assertEqual(resp.json().get("error"), "database_unavailable")
        self.assertTrue(
            any("base de datos no disponible" in linea for linea in logs.output),
            "debe quedar rastro en el log de la caída de BD",
        )

    def test_error_no_relacionado_con_la_bd_sigue_devolviendo_200(self):
        """Reintentar un error de lógica solo generaría mensajes duplicados."""
        mensaje = {
            "id": "wamid.logica",
            "from": "573001112233",
            "type": "text",
            "text": {"body": "hola"},
        }
        with mock.patch.object(
            webhook_whatsapp, "procesar_mensaje",
            new=mock.AsyncMock(side_effect=ValueError("dato raro del payload")),
        ), self.assertLogs("app.api.webhook_whatsapp", level="ERROR"):
            resp = self._post(mensaje)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))


if __name__ == "__main__":
    unittest.main()
