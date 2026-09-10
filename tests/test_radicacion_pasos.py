"""Tests for monitoreo de radicación: secuencia de pasos ok/error.

Cubre:
- El bot reporta cada paso (paso_1_lugar_envio, paso_5_accionado, ...) con
  estado ok/error vía el callback on_paso.
- El servicio persiste los pasos en la tabla PasoRadicacion (iniciar_bot,
  llenar_formulario, captcha, enviar, radicada/fallida).
- El admin expone los pasos ordenados por hora en /admin/api/tutelas/{id}.
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

from app.bot.navegador import RadicadorBot
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


def _crear_tutela(session, estado="pendiente"):
    user = User(telefono="573009990001", estado="activo", consentimiento=True)
    session.add(user)
    session.flush()
    tutela = Tutela(
        user_id=user.id,
        tipo="salud",
        estado=estado,
        datos_json=json.dumps({"tipo": "salud", "eps": "Salud Total"}),
    )
    session.add(tutela)
    session.commit()
    return user, tutela


class FakePage:
    async def wait_for_timeout(self, *args, **kwargs):
        return None


def _make_bot(page=None):
    bot = RadicadorBot.__new__(RadicadorBot)
    bot.page = page or FakePage()
    bot.on_paso = None
    return bot


class TestNavegadorReportaPasos(unittest.TestCase):
    """El bot debe emitir paso por paso vía on_paso, ok al terminar cada fase."""

    def _bot_con_colector(self):
        bot = _make_bot()
        pasos = []

        def cb(paso, estado, detalle=""):
            pasos.append((paso, estado, detalle))

        bot.on_paso = cb
        return bot, pasos

    def test_llenar_formulario_reporta_pasos_ok(self):
        """Los pasos 1-4 se registran como ok cuando el formulario se llena."""
        bot, pasos = self._bot_con_colector()
        with mock.patch.object(settings_sim(), "simulate_bot", False), \
             mock.patch.object(bot, "_modal_aceptar_terminos", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_paso_lugar_envio", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_paso_tipo_registro", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_paso_lugar_hechos", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_paso_accionante", new=mock.AsyncMock(return_value=False)):
            resultado = asyncio.run(bot.llenar_formulario({}))

        self.assertTrue(resultado.get("ok"))
        nombres = [p for p, _, _ in pasos]
        self.assertIn("paso_1_lugar_envio", nombres)
        self.assertIn("paso_2_tipo_registro", nombres)
        self.assertIn("paso_3_lugar_hechos", nombres)
        self.assertIn("paso_4_accionante", nombres)
        self.assertEqual(
            [e for _, e, _ in pasos],
            ["ok"] * len(pasos),
            "Todos los pasos deben reportarse como ok",
        )

    def test_llenar_formulario_reporta_error_del_paso_fallido(self):
        """Si un paso lanza excepción, se reporta error en ese paso."""
        bot, pasos = self._bot_con_colector()

        async def explota(datos):
            raise RuntimeError("select no encontrado")

        with mock.patch.object(settings_sim(), "simulate_bot", False), \
             mock.patch.object(bot, "_modal_aceptar_terminos", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_paso_lugar_envio", new=explota), \
             mock.patch.object(bot, "_paso_tipo_registro", new=mock.AsyncMock()):
            resultado = asyncio.run(bot.llenar_formulario({}))

        self.assertFalse(resultado.get("ok"))
        self.assertTrue(
            any(paso == "paso_1_lugar_envio" and estado == "error" for paso, estado, _ in pasos),
            "El paso que falló debe reportarse con estado error",
        )

    def test_completar_post_codigo_reporta_pasos_ok(self):
        """Los pasos 5-8 se registran como ok."""
        bot, pasos = self._bot_con_colector()
        with mock.patch.object(settings_sim(), "simulate_bot", False), \
             mock.patch.object(bot, "_paso_accionado", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_paso_derechos", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_paso_archivos", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_paso_juramento", new=mock.AsyncMock()):
            resultado = asyncio.run(bot.completar_post_codigo({}, "ruta.pdf"))

        self.assertTrue(resultado.get("ok"))
        nombres = [p for p, _, _ in pasos]
        for esperado in ("paso_5_accionado", "paso_6_derechos", "paso_7_archivos", "paso_8_juramento"):
            self.assertIn(esperado, nombres)


def settings_sim():
    from app.config import settings

    return settings


class TestNavegadorRecaptcha(unittest.TestCase):
    """El token de reCAPTCHA se inserta en el textarea y en el callback T()
    SIEMPRE como literal JS (nunca dejar un identificador `token` suelto,
    que explota con ReferenceError en el navegador)."""

    def _pagina_que_guarda_script(self):
        clase = self

        class PageToken:
            async def evaluate(self, script):
                clase.script = script

        page = PageToken()
        self.script = ""
        return page

    def test_token_se_interpola_como_literal_en_textarea_y_callback(self):
        bot = _make_bot(self._pagina_que_guarda_script())
        with mock.patch.object(settings_sim(), "simulate_bot", False), \
             mock.patch("app.services.captcha_service.resolver_recaptcha_v2",
                        new=mock.AsyncMock(return_value="TOKEN_123")):
            resultado = asyncio.run(bot.resolver_recaptcha())

        self.assertTrue(resultado, "Debe resolver el reCAPTCHA")
        self.assertIn("g-recaptcha-response').value = 'TOKEN_123'", self.script)
        self.assertIn("client.T('TOKEN_123')", self.script)
        self.assertNotIn("client.T(token)", self.script, "El token no puede ser un identificador JS suelto")


class TestServicioRegistraPasos(unittest.TestCase):
    """El servicio persiste los pasos en la BD, con estado ok y error."""

    def _fondo(self, bot_fake):
        session = _nueva_sesion()
        user, tutela = _crear_tutela(session)
        with mock.patch.object(radicacion_service, "SessionLocal", return_value=session), \
             mock.patch.object(radicacion_service, "_get_bot", return_value=bot_fake), \
             mock.patch.object(radicacion_service, "enviar_texto", return_value=True), \
             mock.patch.object(radicacion_service, "enviar_imagen", return_value=True):
            resultado = asyncio.run(radicacion_service.iniciar_radicacion(tutela.id, forzar=True))
        return session, tutela, resultado

    def test_radicacion_exitosa_registra_secuencia_hasta_radicada(self):
        """Flujo completo (sin código): pasos iniciar..enviar ok y radicada."""
        from app.models.radicacion import PasoRadicacion

        class FakeBot:
            def __init__(self):
                self.on_paso = None

            async def iniciar(self):
                pass

            async def navegar_portal(self):
                pass

            async def llenar_formulario(self, datos):
                return {"ok": True, "requiere_codigo_email": False}

            async def completar_post_codigo(self, datos, ruta):
                return {"ok": True}

            async def resolver_recaptcha(self):
                return True

            async def enviar_y_descargar(self):
                return {"path": "storage/constancia_x.png", "num_radicado": "11001-2026-00009"}

            async def tomar_screenshot(self, nombre):
                return None

            async def cerrar(self):
                pass

        session, tutela, resultado = self._fondo(FakeBot())
        self.assertTrue(resultado.get("ok"))

        pasos = session.execute(
            select(PasoRadicacion).order_by(PasoRadicacion.id)
        ).scalars().all()

        nombres = [p.paso for p in pasos]
        for esperado in ("iniciar_bot", "navegar_portal", "llenar_formulario",
                         "resolver_captcha", "enviar_y_descargar", "radicada"):
            self.assertIn(esperado, nombres, f"Falta el paso {esperado} en {nombres}")

        self.assertTrue(all(p.estado == "ok" for p in pasos))
        from app.models.radicacion import Radicacion

        rad = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela.id)
        ).scalar_one()
        self.assertEqual(rad.estado, "radicada")
        self.assertEqual(rad.num_radicado, "11001-2026-00009")

    def test_radicacion_fallida_registra_error(self):
        """Si llenar_formulario falla, se registra paso error y estado fallida."""
        from app.models.radicacion import PasoRadicacion, Radicacion

        class FakeBot:
            def __init__(self):
                self.on_paso = None

            async def iniciar(self):
                pass

            async def navegar_portal(self):
                pass

            async def llenar_formulario(self, datos):
                return {"ok": False, "error": "select ciudad no encontrado"}

            async def cerrar(self):
                pass

        session, tutela, resultado = self._fondo(FakeBot())

        pasos = session.execute(
            select(PasoRadicacion).order_by(PasoRadicacion.id)
        ).scalars().all()
        nombres = [p.paso for p in pasos]

        self.assertFalse(resultado.get("ok"))
        self.assertIn("navegar_portal", nombres)
        self.assertIn("llenar_formulario", nombres)
        self.assertTrue(
            any(p.paso == "llenar_formulario" and p.estado == "error" for p in pasos),
            "El paso llenar_formulario debe registrarse con estado error",
        )
        rad = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela.id)
        ).scalar_one()
        self.assertEqual(rad.estado, "fallida")
        self.assertIn("ciudad", rad.ultimo_error or "")


class TestAdminExponePasos(unittest.TestCase):
    """El endpoint /admin/api/tutelas/{id} incluye la lista de pasos ordenada."""

    def test_detalle_incluye_pasos(self):
        from app.models.radicacion import PasoRadicacion, Radicacion

        session = _nueva_sesion()
        user, tutela = _crear_tutela(session)
        rad = Radicacion(tutela_id=tutela.id, estado="radicada", num_radicado="11001-2026-00010")
        session.add(rad)
        session.commit()
        session.add(PasoRadicacion(radicacion_id=rad.id, paso="iniciar_bot", estado="ok"))
        session.add(PasoRadicacion(radicacion_id=rad.id, paso="radicada", estado="ok"))
        session.commit()

        from app.api import admin as admin_api

        resp = admin_api.detalle_tutela(tutela.id, _FakeRequest(), session)
        pasos = resp["radicacion"]["pasos"]
        self.assertEqual(len(pasos), 2)
        self.assertEqual([p["paso"] for p in pasos], ["iniciar_bot", "radicada"])
        self.assertTrue(all("estado" in p and "created_at" in p for p in pasos))


class _FakeRequest:
    def __init__(self):
        self.headers = {}


if __name__ == "__main__":
    unittest.main()