"""Seguimiento real de la entrega de los envíos del bot.

El POST a la Graph API devuelve HTTP 200 y un ``wamid``, lo cual NO prueba que el
mensaje llegara. Meta notifica después ``sent``/``delivered``/``read``/``failed``
en el webhook. Estos tests fijan que:

1. cada envío aceptado queda registrado con su wamid (para poder casarlo), y
2. los ``statuses`` actualizan el estado real, incluido ``failed`` con su motivo.
"""
import asyncio
import unittest
from unittest import mock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.api import webhook_whatsapp
from app.database import Base, get_session
from app.main import app
from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import EnvioWhatsApp
from app.services import whatsapp_service

WAMID = "wamid.HBgMNTczMDQ1MzgyOTI1FQIAEJgSRjY4Q0U4MTUzNj"


class _RespuestaMeta:
    def __init__(self, status_code=200, cuerpo=None, texto=""):
        self.status_code = status_code
        self._cuerpo = cuerpo if cuerpo is not None else {"messages": [{"id": WAMID}]}
        self.text = texto

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._cuerpo


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


class TestCapturaWamid(unittest.TestCase):
    def test_texto_aceptado_registra_wamid(self):
        with mock.patch.object(whatsapp_service.settings, "whatsapp_provider", "meta"), \
                mock.patch.object(whatsapp_service.httpx, "post", return_value=_RespuestaMeta()):
            whatsapp_service.reiniciar_wamids_envio()
            self.addCleanup(whatsapp_service.reiniciar_wamids_envio)
            ok = whatsapp_service._enviar_meta_texto("573045382925", "Hola")

        self.assertTrue(ok)
        self.assertEqual([i["wamid"] for i in whatsapp_service.wamids_envio()], [WAMID])

    def test_botones_aceptados_registran_wamid(self):
        with mock.patch.object(whatsapp_service.settings, "whatsapp_provider", "meta"), \
                mock.patch.object(whatsapp_service.httpx, "post", return_value=_RespuestaMeta()):
            whatsapp_service.reiniciar_wamids_envio()
            self.addCleanup(whatsapp_service.reiniciar_wamids_envio)
            ok = whatsapp_service.enviar_botones("573045382925", "texto", [("1", "Uno")])

        self.assertTrue(ok)
        self.assertEqual([i["wamid"] for i in whatsapp_service.wamids_envio()], [WAMID])

    def test_respuesta_rechazada_no_registra_wamid(self):
        with mock.patch.object(whatsapp_service.settings, "whatsapp_provider", "meta"), \
                mock.patch.object(whatsapp_service.httpx, "post", return_value=_RespuestaMeta(
                    400, texto='{"error":{"message":"The parameter to is required.","code":100}}')):
            whatsapp_service.reiniciar_wamids_envio()
            self.addCleanup(whatsapp_service.reiniciar_wamids_envio)
            ok = whatsapp_service._enviar_meta_texto("573045382925", "Hola")

        self.assertFalse(ok)
        self.assertEqual(whatsapp_service.wamids_envio(), [])

    def test_json_invalido_no_rompe(self):
        with mock.patch.object(whatsapp_service.settings, "whatsapp_provider", "meta"), \
                mock.patch.object(whatsapp_service.httpx, "post", return_value=_RespuestaMeta(
                    200, cuerpo={"no": "messages"}, texto="no json")):
            whatsapp_service.reiniciar_wamids_envio()
            self.addCleanup(whatsapp_service.reiniciar_wamids_envio)
            self.assertTrue(whatsapp_service._enviar_meta_texto("573045382925", "Hola"))
        self.assertEqual(whatsapp_service.wamids_envio(), [])


class TestEnvioRegistradoAlProcesar(unittest.TestCase):
    def test_flujo_real_registra_el_envio(self):
        session = _nueva_sesion()
        with mock.patch.object(whatsapp_service.settings, "whatsapp_provider", "meta"), \
                mock.patch.object(whatsapp_service.httpx, "post", return_value=_RespuestaMeta()):
            asyncio.run(
                webhook_whatsapp.procesar_mensaje(
                    session, "573045382925", "Hola", 0, "", False
                )
            )

        envio = session.execute(select(EnvioWhatsApp)).scalars().first()
        self.assertIsNotNone(envio, "debe quedar registrado el envío aceptado")
        self.assertEqual(envio.wamid, WAMID)
        self.assertEqual(envio.estado, "aceptado")
        self.assertEqual(envio.from_number, "573045382925")
        # El saludo inicial se manda antes del consentimiento: la tutela aún no
        # existe, así que el envío queda sin asociar (no es un error).
        self.assertIsNone(envio.tutela_id)

    def test_envio_se_asocia_a_la_tutela_existente(self):
        session = _nueva_sesion()
        user = User(telefono="573045382925")
        session.add(user)
        session.commit()
        tutela = Tutela(user_id=user.id, tipo="salud", estado="borrador", datos_json="{}")
        session.add(tutela)
        session.commit()

        with mock.patch.object(whatsapp_service.settings, "whatsapp_provider", "meta"), \
                mock.patch.object(whatsapp_service.httpx, "post", return_value=_RespuestaMeta()):
            asyncio.run(
                webhook_whatsapp.procesar_mensaje(
                    session, "573045382925", "estoy en recargo", 0, "", False
                )
            )

        envios = self._envios(session)
        self.assertTrue(envios, "debe quedar registrado el envío")
        for envio in envios:
            self.assertEqual(envio.tutela_id, tutela.id)

    @staticmethod
    def _envios(session):
        return session.execute(select(EnvioWhatsApp)).scalars().all()


class TestEstadosDeMeta(unittest.TestCase):
    def setUp(self):
        self.session = _nueva_sesion()
        self.session.add(EnvioWhatsApp(wamid=WAMID, from_number="573045382925", estado="aceptado"))
        self.session.commit()

    def test_delivered_pasa_a_entregado(self):
        webhook_whatsapp._procesar_statuses(
            self.session, {"statuses": [{"id": WAMID, "status": "delivered"}]}
        )
        fila = self.session.execute(select(EnvioWhatsApp)).scalars().first()
        self.assertEqual(fila.estado, "entregado")

    def test_read_pasa_a_leido(self):
        webhook_whatsapp._procesar_statuses(
            self.session, {"statuses": [{"id": WAMID, "status": "read"}]}
        )
        fila = self.session.execute(select(EnvioWhatsApp)).scalars().first()
        self.assertEqual(fila.estado, "leido")

    def test_failed_guarda_codigo_y_motivo(self):
        webhook_whatsapp._procesar_statuses(self.session, {"statuses": [{
            "id": WAMID,
            "status": "failed",
            "recipient_id": "573045382925",
            "errors": [{
                "code": 131047,
                "title": "Re-engagement message",
                "message": "more than 24 hours",
                "error_data": {"details": "El usuario no escribió en 24 h"},
            }],
        }]})
        fila = self.session.execute(select(EnvioWhatsApp)).scalars().first()
        self.assertEqual(fila.estado, "fallido")
        self.assertEqual(fila.error_code, 131047)
        self.assertIn("24 h", fila.error_detalle)

    def test_estado_no_retrcede(self):
        webhook_whatsapp._procesar_statuses(
            self.session, {"statuses": [{"id": WAMID, "status": "read"}]}
        )
        webhook_whatsapp._procesar_statuses(
            self.session, {"statuses": [{"id": WAMID, "status": "sent"}]}
        )
        fila = self.session.execute(select(EnvioWhatsApp)).scalars().first()
        self.assertEqual(fila.estado, "leido", "un 'sent' tardío no debe degradar el estado")

    def test_wamid_desconocido_no_rompe(self):
        webhook_whatsapp._procesar_statuses(
            self.session, {"statuses": [{"id": "wamid.otro", "status": "delivered"}]}
        )
        fila = self.session.execute(select(EnvioWhatsApp)).scalars().first()
        self.assertEqual(fila.estado, "aceptado")

    def test_payload_raro_no_rompe(self):
        for valor in ([], [None], [{}], [{"id": None, "status": None}], ["texto"]):
            with self.subTest(valor=valor):
                webhook_whatsapp._procesar_statuses(self.session, {"statuses": valor})


class TestWebhookRecibeStatuses(unittest.TestCase):
    def setUp(self):
        self.session = _nueva_sesion()
        self.session.add(EnvioWhatsApp(wamid=WAMID, from_number="573045382925", estado="aceptado"))
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

    def test_payload_solo_con_statuses(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "WABA",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"phone_number_id": "1185525421321347"},
                        "statuses": [{
                            "id": WAMID,
                            "status": "failed",
                            "timestamp": "1759329000",
                            "errors": [{"code": 131026, "message": "not deliverable"}],
                        }],
                    },
                }],
            }],
        }
        resp = self.client.post("/webhook/meta", json=payload)

        self.assertEqual(resp.status_code, 200)
        fila = self.session.execute(select(EnvioWhatsApp)).scalars().first()
        self.assertEqual(fila.estado, "fallido")
        self.assertEqual(fila.error_code, 131026)

    def test_no_crea_usuarios_por_un_status(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "WABA",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "1185525421321347"},
                        "statuses": [{"id": WAMID, "status": "delivered"}],
                    },
                }],
            }],
        }
        self.client.post("/webhook/meta", json=payload)
        self.assertEqual(
            self.session.execute(select(User.id)).scalars().all(), [],
            "un status no debe crear usuarios",
        )


if __name__ == "__main__":
    unittest.main()