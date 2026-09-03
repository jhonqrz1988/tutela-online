"""Tests for Fase A: flujo de verificación de email en radicación.

Reproduce el bug crítico A1/A2: el estado `esperando_codigo_email` es
inalcanzable en `procesar_mensaje`. Un usuario que envía el código numérico
de verificación que le llegó por correo terminaba creando una tutela NUEVA
(perdiendo el flujo) en lugar de continuar la radicación.

Usa una BD SQLite en memoria aislada y parchea el envío de WhatsApp y la
orquestación de radicación para evitar red/Playwright.
"""
import asyncio
import json
import unittest
from unittest import mock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.tutela import Tutela
from app.models.user import User

from app.api import webhook_whatsapp
from app.services import radicacion_service


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    return TestingSession()


class TestCodigoEmail(unittest.TestCase):
    def _crear_usuario_tutela(self, session, estado_tutela="esperando_codigo_email"):
        user = User(telefono="573001112233", estado="activo", consentimiento=True)
        session.add(user)
        session.flush()
        tutela = Tutela(
            user_id=user.id,
            tipo="salud",
            estado=estado_tutela,
            datos_json=json.dumps({"tipo": "salud", "_step": 0}),
        )
        session.add(tutela)
        session.commit()
        return user, tutela

    async def _procesar(self, session, telefono, body):
        with mock.patch.object(webhook_whatsapp, "enviar_texto", return_value=True), \
             mock.patch.object(webhook_whatsapp, "enviar_botones", return_value=True), \
             mock.patch.object(
                 radicacion_service, "continuar_radicacion_con_codigo",
                 new=mock.AsyncMock(return_value={"ok": True}),
             ) as mock_cont:
            resp = await webhook_whatsapp.procesar_mensaje(session, telefono, body, 0, "", False)
        return resp, mock_cont

    def test_codigo_no_crea_tutela_nueva(self):
        """Un código válido enviado en esperando_codigo_email NO debe crear tutela nueva."""
        session = _nueva_sesion()
        user, tutela = self._crear_usuario_tutela(session)

        resp, mock_cont = asyncio.run(self._procesar(session, user.telefono, "582913"))

        # Debe haber exactamente UNA tutela (no se creó una nueva)
        tutelas = session.execute(select(Tutela)).scalars().all()
        self.assertEqual(len(tutelas), 1)
        self.assertEqual(tutelas[0].id, tutela.id)
        self.assertTrue(mock_cont.called, "Se debe continuar la radicación con el código")

    def test_codigo_invalido_pide_de_nuevo(self):
        """Un código inválido (no numérico o de longitud incorrecta) pide reintentar."""
        session = _nueva_sesion()
        user, tutela = self._crear_usuario_tutela(session)

        resp, mock_cont = asyncio.run(self._procesar(session, user.telefono, "abc"))

        self.assertFalse(mock_cont.called, "Código inválido no debe continuar la radicación")
        # No debe crear nueva tutela tampoco
        tutelas = session.execute(select(Tutela)).scalars().all()
        self.assertEqual(len(tutelas), 1)


if __name__ == "__main__":
    unittest.main()
