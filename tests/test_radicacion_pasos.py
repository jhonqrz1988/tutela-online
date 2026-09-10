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


class TestNavegadorDerechos(unittest.TestCase):
    """Los derechos del cliente vienen como artículos de la IA (Art. 48 CP...)
    pero el portal usa categorías. Se mapean a categorías, se deduplican y si
    no se puede seleccionar ninguno no se inventa (se aborta con error claro)."""

    _datos_salud = {"tipo": "salud", "derechos_vulnerados": ["Art. 48 CP", "Art. 49 CP"]}

    def _pagina_que_guarda_scripts(self):
        clase = self
        clase.scripts = []

        class PageOpts:
            async def evaluate(self, script):
                clase.scripts.append(script)
                return []

        return PageOpts()

    def _bot_con_select(self, resolver):
        from app.bot.navegador import RadicadorBot

        class PageDerechos:
            async def wait_for_timeout(self, ms):
                return None

            async def evaluate(self, script):
                return []

        bot = RadicadorBot.__new__(RadicadorBot)
        bot.page = PageDerechos()
        bot._seleccionar_select = resolver
        return bot

    def test_candidatos_mapean_articulos_a_categorias(self):
        from app.bot.navegador import _candidatos_derecho

        self.assertIn("salud", _candidatos_derecho("Art. 48 CP", "salud"))
        self.assertIn("vida", _candidatos_derecho("Art. 11 CP", "salud"))
        self.assertIn("tutela", _candidatos_derecho("Art. 86 CP", "salud"))
        self.assertIn("dignidad", _candidatos_derecho("Art. 2 CP", "salud"))
        self.assertIn("Art. 48 CP", _candidatos_derecho("Art. 48 CP", "salud"))

    def test_derechos_se_seleccionan_y_deduplican(self):
        async def resolver(selector, label):
            return label if label in ("salud", "vida") else None

        bot = self._bot_con_select(resolver)
        bot._js_click = mock.AsyncMock()
        bot._cerrar_jconfirm = mock.AsyncMock()
        bot._esperar_select_ajax = mock.AsyncMock()
        log_derechos = mock.AsyncMock()
        with mock.patch.object(bot, "_log_opciones_derechos", new=log_derechos):
            n = asyncio.run(bot._paso_derechos(self._datos_salud))
        self.assertEqual(n, 1, "Art 48 y 49 mapean a 'salud': se deduplica y se agrega una sola vez")
        bot._js_click.assert_has_calls([mock.call("#btnAdd")])
        bot._js_click.assert_any_call("#RdbNoMedida")
        log_derechos.assert_not_awaited()

    def test_sin_opciones_matchea_aborta_con_error_y_dumpa_opciones(self):
        page = self._pagina_que_guarda_scripts()
        bot = self._bot_con_select(lambda sel, label: None)
        bot.page = page
        bot._js_click = mock.AsyncMock()
        bot._cerrar_jconfirm = mock.AsyncMock()
        bot._esperar_select_ajax = mock.AsyncMock()
        with self.assertRaises(ValueError):
            asyncio.run(bot._paso_derechos(self._datos_salud))
        self.assertTrue(any("DDLDerechos option" in s for s in self.scripts),
                        "Debe volcar las opciones del dropdown para diagnosticar")

    def test_sin_derechos_listados_no_selecciona_nada(self):
        bot = self._bot_con_select(lambda sel, label: "salud")
        bot._js_click = mock.AsyncMock()
        bot._cerrar_jconfirm = mock.AsyncMock()
        bot._esperar_select_ajax = mock.AsyncMock()
        with mock.patch.object(bot, "_log_opciones_derechos", new=mock.AsyncMock()), \
             self.assertRaises(ValueError):
            asyncio.run(bot._paso_derechos({"tipo": "salud", "derechos_vulnerados": []}))
        bot._js_click.assert_not_awaited()


class TestNavegadorEnviarValidaExito(unittest.TestCase):
    """Tras pulsar #enviar, el portal puede quedarse en un modal de validación
    (ej. 'debe seleccionar al menos un derecho') en vez de radicar: el bot NO
    debe declarar la tutela radicada. Si el portal muestra error → error."""

    class _Pagina:
        def __init__(self, numero="", overlay=""):
            self.numero = numero
            self.overlay = overlay

        async def wait_for_timeout(self, ms):
            return None

        async def evaluate(self, script):
            if "textContent" in script and "overlays" in script:
                return self.overlay
            return None

        async def query_selector(self, selector):
            if selector == "#numRadicado":
                return self._Texto(self.numero)
            return None

        class _Texto:
            def __init__(self, value):
                self._value = value

            async def text_content(self):
                return self._value

    def _enviar(self, numero="", overlay=""):
        bot = _make_bot(self._Pagina(numero=numero, overlay=overlay))
        with mock.patch.object(bot, "_cerrar_jconfirm", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_capturar_evidencia", new=mock.AsyncMock()), \
             mock.patch.object(bot, "tomar_screenshot", new=mock.AsyncMock(return_value="storage/constancia_x.png")):
            return asyncio.run(bot.enviar_y_descargar())

    def test_radicada_con_numero_no_es_error(self):
        r = self._enviar(numero="11001-2026-0009")
        self.assertNotIn("error", r)
        self.assertEqual(r.get("num_radicado"), "11001-2026-0009")

    def test_modal_de_validacion_no_declara_radicada(self):
        r = self._enviar(numero="", overlay="Debe seleccionar al menos un derecho")
        self.assertIn("error", r, "El modal de validación debe abortar la radicación")
        self.assertIn("derecho", r["error"].lower())
        self.assertIsNone(r.get("num_radicado"))

    def test_sin_modal_y_sin_numero_sigue_siendo_radicada(self):
        r = self._enviar(numero="", overlay="")
        self.assertNotIn("error", r)


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