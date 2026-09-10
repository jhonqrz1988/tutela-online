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
from app.models.radicacion import Radicacion
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

    def _crear_tutela_esperando_codigo(self, session, usuario_telefono):
        """Escenario real de producción: el admin reintenta (tutela queda en
        'pendiente_radicacion') y la Radicacion queda esperando el código de email."""
        user = User(telefono=usuario_telefono, estado="activo", consentimiento=True)
        session.add(user)
        session.flush()
        tutela = Tutela(
            user_id=user.id,
            tipo="salud",
            estado="pendiente_radicacion",
            datos_json=json.dumps({"tipo": "salud", "_step": 0}),
        )
        session.add(tutela)
        session.flush()
        session.add(Radicacion(tutela_id=tutela.id, estado="esperando_codigo_email"))
        session.commit()
        return user, tutela

    def test_codigo_continua_cuando_tutela_queda_en_pendiente_radicacion(self):
        """Bug reportado en producción: tutela en 'pendiente_radicacion' con radicación
        esperando código. Enviar el código NO debe reiniciar creando tutela nueva."""
        session = _nueva_sesion()
        user, tutela = self._crear_tutela_esperando_codigo(session, "573009991122")

        resp, mock_cont = asyncio.run(self._procesar(session, user.telefono, "582913"))

        tutelas = session.execute(select(Tutela)).scalars().all()
        self.assertEqual(len(tutelas), 1)
        self.assertEqual(tutelas[0].id, tutela.id)
        self.assertTrue(mock_cont.called, "Debe continuar la radicación con el código")

    def test_codigo_invalido_en_pendiente_radicacion_no_reinicia(self):
        """Código inválido mientras la tutela espera el código tampoco reinicia."""
        session = _nueva_sesion()
        user, tutela = self._crear_tutela_esperando_codigo(session, "573009993344")

        resp, mock_cont = asyncio.run(self._procesar(session, user.telefono, "abc"))

        self.assertFalse(mock_cont.called, "Código inválido no debe continuar la radicación")
        tutelas = session.execute(select(Tutela)).scalars().all()
        self.assertEqual(len(tutelas), 1)

    def test_mensaje_en_pendiente_radicacion_sin_rad_no_reinicia(self):
        """Mensaje durante 'pendiente_radicacion' sin rad esperando código no crea
        tutela nueva (cae al menú)."""
        session = _nueva_sesion()
        user = User(telefono="573009995566", estado="activo", consentimiento=True)
        session.add(user)
        session.flush()
        tutela = Tutela(user_id=user.id, tipo="salud", estado="pendiente_radicacion", datos_json="{}")
        session.add(tutela)
        session.commit()

        resp, mock_cont = asyncio.run(self._procesar(session, user.telefono, "paralelepipedo"))

        self.assertFalse(mock_cont.called)
        tutelas = session.execute(select(Tutela)).scalars().all()
        self.assertEqual(len(tutelas), 1)
        self.assertEqual(tutelas[0].id, tutela.id)

    def test_codigo_prioriza_tutela_con_radicacion_esperando_codigo(self):
        """Si existe una tutela más nueva (huérfana de un flujo previo que creó
        el bug A1/A2), el código del correo debe ir a la tutela cuya Radicación
        espera el código y NO alimentar ni crear la tutela nueva."""
        session = _nueva_sesion()
        user = User(telefono="573009997744", estado="activo", consentimiento=True)
        session.add(user)
        session.flush()
        tutela_rad = Tutela(
            user_id=user.id,
            tipo="salud",
            estado="pendiente_radicacion",
            datos_json=json.dumps({"tipo": "salud"}),
        )
        session.add(tutela_rad)
        session.flush()
        session.add(Radicacion(tutela_id=tutela_rad.id, estado="esperando_codigo_email"))
        tutela_nueva = Tutela(
            user_id=user.id,
            tipo="salud",
            estado="recogiendo_datos",
            datos_json=json.dumps({"tipo": "salud", "_step": 0}),
        )
        session.add(tutela_nueva)
        session.commit()

        resp, mock_cont = asyncio.run(self._procesar(session, user.telefono, "582913"))

        tutelas = session.execute(select(Tutela)).scalars().all()
        self.assertEqual(len(tutelas), 2, "No se debe crear una tercera tutela")
        self.assertTrue(mock_cont.called, "Debe continuar la radicación con el código")
        self.assertEqual(mock_cont.call_args[0][0], tutela_rad.id,
                         "El código debe ir a la tutela cuya Radicación espera el código")
        datos_nueva = json.loads(tutela_nueva.datos_json)
        self.assertEqual(datos_nueva.get("_step"), 0, "La tutela huérfana no debe avanzar")

    def test_con_parqueo_perdido_todos_los_envios_responden_sin_tocar_bot(self):
        """Cambio de diseño (escenario a): el señalador ya no reclama la radicación
        con un claim atómico, sólo despierta el parqueo en memoria vía event loop.

        Si el parqueo se perdió (servidor reiniciado), TODOS los envíos del código
        responden con error de no-parqueo y el navegador jamás se toca (imposible
        que 5 fills concurrentes colguen el webhook)."""
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        S = sessionmaker(bind=engine, expire_on_commit=False)
        session = S()
        user, tutela = self._crear_tutela_esperando_codigo(session, "573009998855")
        tutela_id = tutela.id
        session.close()

        class FakeBot:
            def __init__(self):
                self.entradas = []

            async def verificar_conexion(self):
                return True

            async def ingresar_codigo_email(self, codigo):
                self.entradas.append(codigo)
                await asyncio.sleep(0.05)

        def _run_concurrentes():
            real_sessionloc = radicacion_service.SessionLocal
            real_get_bot = radicacion_service._get_bot
            bot = FakeBot()
            try:
                radicacion_service.SessionLocal = lambda: S()
                radicacion_service._get_bot = lambda: bot
                async def correr():
                    r1, r2, r3 = await asyncio.gather(
                        radicacion_service.continuar_radicacion_con_codigo(tutela_id, "582913"),
                        radicacion_service.continuar_radicacion_con_codigo(tutela_id, "582913"),
                        radicacion_service.continuar_radicacion_con_codigo(tutela_id, "582913"),
                        return_exceptions=True,
                    )
                    return r1, r2, r3

                return asyncio.run(correr()), bot
            finally:
                radicacion_service.SessionLocal = real_sessionloc
                radicacion_service._get_bot = real_get_bot

        resultados, bot = _run_concurrentes()
        self.assertEqual(len(resultados), 3)
        for r in resultados:
            self.assertIsInstance(r, dict, f"Una llamada concurrente lanzó excepción: {r!r}")
            self.assertFalse(r.get("ok"), f"Ningún envío debe ganar sin parqueo: {r}")
            self.assertIn("reinició", r.get("error", ""), f"Error inesperado: {r}")
        self.assertEqual(bot.entradas, [], "Sin parqueo el navegador no debe tocarse")

        sesion_final = S()
        rad = sesion_final.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela_id)
        ).scalar_one()
        self.assertEqual(
            rad.estado, "esperando_codigo_email",
            "Sin parqueo el señalador no debe tocar el estado (sólo despierta la coroutine)",
        )
        sesion_final.close()


if __name__ == "__main__":
    unittest.main()
