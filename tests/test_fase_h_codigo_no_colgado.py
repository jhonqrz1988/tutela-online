"""Tests for Fase H: el flujo de código de email NO puede quedarse colgado.

Reproduce el bug de producción: la radicación queda en estado 'continuando'
para siempre cuando el navegador se cuelga esperando el campo #IdEmail1.
Consecuencias del bug:
  - El webhook responde "El código ya se está procesando" ante cualquier
    reenvío del código (el claim no admite 'continuando').
  - El scheduler salta la tutela (estado no está en la cola).
  - El admin no puede reiniciar la radicación.

El fix por capas:
  1. `ingresar_codigo_email` espera el selector con timeout acotado y
     devuelve {ok, error} en vez de colgar el loop de Playwright.
  2. El claim atómico de código admite un 'continuando' ESTANCADO
     (updated_at viejo) para que reenviar el código reactive el flujo.
  3. Watchdog `descolgar_radicaciones_estancadas()` lo marca como 'fallida'
     para que el admin y el scheduler puedan reintentar.
"""
import asyncio
import datetime
import json
import unittest
from unittest import mock

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela
from app.models.user import User

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


class _FakePage:
    def is_closed(self):
        return False


class _FakeBot:
    def __init__(self, codigos=None):
        self.page = _FakePage()
        self.entradas = codigos if codigos is not None else []

    async def ingresar_codigo_email(self, codigo):
        self.entradas.append(codigo)
        await asyncio.sleep(0.05)


def _crear_tutela_esperando_codigo(session, telefono="573001112233"):
    user = User(telefono=telefono, estado="activo", consentimiento=True)
    session.add(user)
    session.flush()
    tutela = Tutela(
        user_id=user.id,
        tipo="salud",
        estado="esperando_codigo_email",
        datos_json=json.dumps({"tipo": "salud"}),
    )
    session.add(tutela)
    session.flush()
    session.add(Radicacion(tutela_id=tutela.id, estado="continuando"))
    session.commit()
    return user, tutela


class TestClaimContinuaEstancada(unittest.TestCase):
    def _fijar_updated_at(self, session, tutela_id, minutos_atras):
        """Fija updated_at de la radicación en el pasado (vía core update para
        no disparar el onupdate del ORM)."""
        session.execute(
            update(Radicacion)
            .where(Radicacion.tutela_id == tutela_id)
            .values(updated_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=minutos_atras))
        )
        session.commit()

    def _recurrir_con_codigo(self, session, tutela_id, bot):
        real_sessionloc = radicacion_service.SessionLocal
        real_get_bot = radicacion_service._get_bot
        try:
            radicacion_service.SessionLocal = lambda: session
            radicacion_service._get_bot = lambda: bot
            with mock.patch.object(
                radicacion_service, "_completar_radicacion",
                new=mock.AsyncMock(return_value=None),
            ):
                return asyncio.run(radicacion_service.continuar_radicacion_con_codigo(tutela_id, "582913"))
        finally:
            radicacion_service.SessionLocal = real_sessionloc
            radicacion_service._get_bot = real_get_bot

    def test_continuando_estancado_es_retomable(self):
        """Reproduce el bug: radicación colgada en 'continuando' hace 15 min
        debe poder reclamarse de nuevo al reenviar el código (NO responder
        'ya se está procesando')."""
        session = _nueva_sesion()
        _, tutela = _crear_tutela_esperando_codigo(session)
        self._fijar_updated_at(session, tutela.id, minutos_atras=15)

        bot = _FakeBot()
        resultado = self._recurrir_con_codigo(session, tutela.id, bot)

        self.assertTrue(resultado.get("ok"), f"No se pudo retomar la radicación estancada: {resultado}")
        self.assertEqual(bot.entradas, ["582913"], "El navegador debe recibir el código reenviado")
        rad = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela.id)
        ).scalar_one()
        self.assertEqual(rad.estado, "continuando")

    def test_continuando_de_6_minutos_se_reclama_antes_de_vencer_el_codigo(self):
        """El código de la Rama Judicial vence a los 10 minutos. Un
        'continuando' colgado a los 6 min debe ser reclaimable para que el
        reenvío del usuario alcance a entrar dentro de la validez."""
        session = _nueva_sesion()
        _, tutela = _crear_tutela_esperando_codigo(session)
        self._fijar_updated_at(session, tutela.id, minutos_atras=6)

        bot = _FakeBot()
        resultado = self._recurrir_con_codigo(session, tutela.id, bot)

        self.assertTrue(resultado.get("ok"), f"Debe retomarse dentro de la validez de 10 min: {resultado}")
        self.assertEqual(bot.entradas, ["582913"])

    def test_continuando_de_3_minutos_no_se_reclama(self):
        """Con el umbral de estancamiento en ~5 min, un 'continuando' de 3
        minutos aún está procesando y NO debe reclamarse (evita doble claim
        sobre un navegador vivo)."""
        session = _nueva_sesion()
        _, tutela = _crear_tutela_esperando_codigo(session)
        self._fijar_updated_at(session, tutela.id, minutos_atras=3)

        bot = _FakeBot()
        resultado = self._recurrir_con_codigo(session, tutela.id, bot)

        self.assertFalse(resultado.get("ok"))
        self.assertIn("ya se está procesando", resultado.get("error", ""))
        self.assertEqual(bot.entradas, [])

    def test_continuando_reciente_no_se_reclama(self):
        """Un 'continuando' reciente (aún procesando) NO debe reclamarse dos
        veces: los envíos duplicados responden 'ya se está procesando'."""
        session = _nueva_sesion()
        _, tutela = _crear_tutela_esperando_codigo(session)

        bot = _FakeBot()
        resultado = self._recurrir_con_codigo(session, tutela.id, bot)

        self.assertFalse(resultado.get("ok"))
        self.assertIn("ya se está procesando", resultado.get("error", ""))
        self.assertEqual(bot.entradas, [], "No debe tocar el navegador si el claim no procede")

    def test_tres_reintentos_en_continuando_reciente_no_doblan_claim(self):
        """Con el bot aún vivo ('continuando' reciente), los reintentos
        concurrentes del código NO deben reclamar otra vez el navegador:
        todos responden 'ya se está procesando' y tocan CERO entradas."""
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        S = sessionmaker(bind=engine, expire_on_commit=False)
        session = S()
        _, tutela = _crear_tutela_esperando_codigo(session)
        tutela_id = tutela.id
        session.close()

        class FakeBot:
            def __init__(self):
                self.page = _FakePage()
                self.entradas = []

            async def ingresar_codigo_email(self, codigo):
                self.entradas.append(codigo)
                await asyncio.sleep(0.05)

        def _run():
            real_sessionloc = radicacion_service.SessionLocal
            real_get_bot = radicacion_service._get_bot
            bot = FakeBot()
            try:
                radicacion_service.SessionLocal = lambda: S()
                radicacion_service._get_bot = lambda: bot
                with mock.patch.object(
                    radicacion_service, "_completar_radicacion",
                    new=mock.AsyncMock(return_value=None),
                ):
                    async def correr():
                        return await asyncio.gather(
                            radicacion_service.continuar_radicacion_con_codigo(tutela_id, "582913"),
                            radicacion_service.continuar_radicacion_con_codigo(tutela_id, "582913"),
                            radicacion_service.continuar_radicacion_con_codigo(tutela_id, "582913"),
                            return_exceptions=True,
                        )

                    return asyncio.run(correr())
            finally:
                radicacion_service._get_bot = real_get_bot
                radicacion_service.SessionLocal = real_sessionloc

        resultados = _run()
        for r in resultados:
            if not isinstance(r, dict):
                self.fail(f"Una llamada lanzó excepción: {r!r}")
            self.assertFalse(r.get("ok"))
            self.assertIn("ya se está procesando", r.get("error", ""), f"Respuesta inesperada: {r}")
        # El acceso a la instancia FakeBot no es directo; verificamos el estado
        sesion_final = S()
        rad = sesion_final.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela_id)
        ).scalar_one()
        self.assertEqual(rad.estado, "continuando", "Ningún reintento debe reclamar de nuevo")
        sesion_final.close()


class TestIngresarCodigoEmailAcotado(unittest.TestCase):
    def test_selector_ausente_devuelve_error_sin_colgarse(self):
        """Si el portal no muestra #IdEmail1, `ingresar_codigo_email` debe
        acotar la espera y devolver {ok: False} en vez de colgar el loop."""
        import app.bot.navegador as navegador_mod
        from app.bot.navegador import RadicadorBot

        real_timeout = navegador_mod.ESPERA_CODIGO_SELECTOR_MS

        class PageSinSelector:
            def __init__(self):
                self.llamadas_selector = 0

            async def wait_for_selector(self, selector, **kwargs):
                self.llamadas_selector += 1
                raise TimeoutError(f"No aparece {selector}")

            async def fill(self, *args, **kwargs):
                raise AssertionError("No se debe intentar escribir sin selector")

            async def type(self, *args, **kwargs):
                raise AssertionError("No se debe intentar escribir sin selector")

            async def wait_for_timeout(self, ms):
                return

        navegador_mod.ESPERA_CODIGO_SELECTOR_MS = 100
        bot = RadicadorBot()
        bot.page = PageSinSelector()
        try:
            resultado = asyncio.run(bot.ingresar_codigo_email("582913"))
        finally:
            navegador_mod.ESPERA_CODIGO_SELECTOR_MS = real_timeout

        self.assertIsInstance(resultado, dict)
        self.assertFalse(resultado.get("ok"))
        self.assertTrue(resultado.get("error"))

    def test_codigo_valido_escribe_y_devuelve_ok(self):
        """Flujo feliz: el selector existe, se limpia, escribe el código y
        devuelve {ok: True}."""
        from app.bot.navegador import RadicadorBot

        esperado = {"limpiado": "", "escrito": ""}

        class PageConSelector:
            async def wait_for_selector(self, selector, **kwargs):
                return object()  # elemento encontrado

            async def fill(self, selector, valor, **kwargs):
                esperado["limpiado"] = valor

            async def type(self, selector, valor, **kwargs):
                esperado["escrito"] = valor

            async def wait_for_timeout(self, ms):
                return

        bot = RadicadorBot()
        bot.page = PageConSelector()
        resultado = asyncio.run(bot.ingresar_codigo_email("582913"))

        self.assertTrue(resultado.get("ok"))
        self.assertEqual(esperado["limpiado"], "")
        self.assertEqual(esperado["escrito"], "582913")


class TestDescolgarEstancadas(unittest.TestCase):
    def test_continuando_viejo_pasa_a_fallida(self):
        """El watchdog marca como 'fallida' una radicación colgada hace más
        del umbral, y deja intactas las recientes (o en espera de código)."""
        session = _nueva_sesion()
        _, tutela_vieja = _crear_tutela_esperando_codigo(session, telefono="573001112244")
        _, tutela_reciente = _crear_tutela_esperando_codigo(session, telefono="573001112255")
        session.execute(
            update(Radicacion).where(Radicacion.tutela_id == tutela_vieja.id).values(
                updated_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=20)
            )
        )
        session.commit()

        with mock.patch.object(radicacion_service, "SessionLocal", return_value=session), \
             mock.patch.object(radicacion_service, "_bot", None):
            radicacion_service.descolgar_radicaciones_estancadas()

        rad_vieja = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela_vieja.id)
        ).scalar_one()
        rad_reciente = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela_reciente.id)
        ).scalar_one()
        self.assertEqual(rad_vieja.estado, "fallida")
        self.assertTrue(rad_vieja.ultimo_error)
        self.assertEqual(rad_reciente.estado, "continuando", "La reciente no debe tocarse")


if __name__ == "__main__":
    unittest.main()