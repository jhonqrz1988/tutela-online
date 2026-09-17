"""Tests for Fase A: verificación condicional de email (A9) y discapacidad (A8).

El portal de Rama Judicial NO siempre pide verificación de email (cuando el
correo ya está registrado no muestra #IdEmail1). El bot hoy fuerza la
verificación siempre (bug A9). Se testea que:
- Si #IdEmail1 NO es visible -> _paso_accionante retorna False (no requiere código)
- Si #IdEmail1 es visible -> retorna True (requiere código)
- El tipo de discapacidad se toma de datos.get('accionante_discapacidad')
  con fallback "No Aplica" (A8)
"""
import asyncio
import os
import tempfile
import unittest
from unittest import mock

from app.bot.navegador import (
    RadicadorBot,
    _es_error_correo,
    _info_archivo,
    _nombres_a_campos,
)


class FakeElement:
    def __init__(self, visible: bool):
        self._visible = visible

    async def is_visible(self) -> bool:
        return self._visible


class FakeContext:
    def __init__(self):
        self._cerrado = False

    async def close(self):
        self._cerrado = True


class FakePage:
    def __init__(self, cajon_abierto: bool = False):
        self.cajon_abierto = cajon_abierto
        self.tipodiscapacidad_argumento = None
        self.context = FakeContext()

    async def wait_for_function(self, script, **kwargs):
        if self.cajon_abierto:
            return True
        raise TimeoutError("No hay cajón de verificación")

    async def query_selector(self, selector: str):
        return FakeElement(True)

    async def evaluate(self, *args, **kwargs):
        return None

    async def select_option(self, *args, **kwargs):
        return None

    async def fill(self, *args, **kwargs):
        return None

    async def type(self, *args, **kwargs):
        return None

    async def wait_for_timeout(self, *args, **kwargs):
        return None


def _make_bot(page: FakePage):
    bot = RadicadorBot.__new__(RadicadorBot)
    bot.page = page
    return bot


class TestVerificacionEmailCondicional(unittest.TestCase):
    _datos = {"accionante_nombre": "Juan Perez Lopez", "accionante_email": "a@b.com"}

    def _ejecutar_paso_accionante(self, bot, datos) -> bool:
        return asyncio.run(bot._paso_accionante(datos))

    def test_sin_cajon_retorna_false(self):
        """Si el portal no abre el cajón del código (correo ya registrado),
        NO requiere código, pero de todas formas cierra el paso de confirmación
        del correo de forma forzada (queja de prod: "Debe Confirmar el correo
        electrónico" al enviar)."""
        bot = _make_bot(FakePage(cajon_abierto=False))
        confirmado = mock.AsyncMock(return_value=False)
        with mock.patch.object(bot, "_fijar_select_sin_postback", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_cerrar_jconfirm", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_confirmar_correo_forzado", new=confirmado):
            requiere = self._ejecutar_paso_accionante(bot, self._datos)
        self.assertFalse(requiere, "Si el portal no abre el cajón, debe retornar False")
        confirmado.assert_awaited_once_with("a@b.com")

    def test_con_cajon_retorna_true(self):
        """Si el portal abre el cajón de verificación, requiere código."""
        bot = _make_bot(FakePage(cajon_abierto=True))
        with mock.patch.object(bot, "_fijar_select_sin_postback", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_cerrar_jconfirm", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()):
            requiere = self._ejecutar_paso_accionante(bot, self._datos)
        self.assertTrue(requiere, "Si el portal abre el cajón del código, debe retornar True")


class TestCorreoForzado(unittest.TestCase):
    """El paso 'confirmar correo' del portal (queja de prod: "Debe Confirmar el
    correo electrónico") se cierra forzando #IdEmail1 por JS, y se re-detecta si
    con eso el portal pide el código."""

    def test_confirmar_correo_forzado_obliga_dos_ciclos_y_no_pide_codigo(self):
        bot = _make_bot(FakePage(cajon_abierto=False))
        llamadas = []

        async def fake_reingresar(email, forzar=False):
            llamadas.append((email, forzar))

        with mock.patch.object(bot, "_reingresar_email", new=fake_reingresar), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()):
            resultado = asyncio.run(bot._confirmar_correo_forzado("a@b.com"))
        self.assertFalse(resultado)
        self.assertEqual(llamadas, [("a@b.com", True), ("a@b.com", True)])

    def test_confirmar_correo_forzado_devuelve_true_si_el_portal_abre_cajon(self):
        """Si tras el re-ingreso forzado SÍ se abre el cajón del código, el paso
        4 debe reportar que requiere código (retorna True)."""
        class PageCajonTardio(FakePage):
            def __init__(self):
                super().__init__(cajon_abierto=False)
                self.chequeos = 0

            async def wait_for_function(self, script, **kwargs):
                self.chequeos += 1
                if self.chequeos >= 2:
                    return True
                raise TimeoutError("cajón aún no")

        bot = _make_bot(PageCajonTardio())
        with mock.patch.object(bot, "_reingresar_email", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()):
            resultado = asyncio.run(bot._confirmar_correo_forzado("a@b.com"))
        self.assertTrue(resultado)

    def test_reingresar_email_forzar_habilita_campo_por_js(self):
        """Con forzar=True el correo se escribe en #Email/#IdEmail1 aunque el
        portal los deje disabled: valor + eventos input/change (sin depender de
        page.fill que exige campo habilitado)."""
        class PageGraba(FakePage):
            def __init__(self):
                super().__init__()
                self.scripts = []

            async def evaluate(self, script, *args, **kwargs):
                self.scripts.append(script if isinstance(script, str) else str(script))
                return None

        bot = _make_bot(PageGraba())
        with mock.patch.object(bot, "_js_click", new=mock.AsyncMock()):
            asyncio.run(bot._reingresar_email("a@b.com", forzar=True))
        join = " ".join(bot.page.scripts)
        self.assertIn("el.value = texto", join)
        self.assertIn("removeAttribute('disabled')", join)
        self.assertIn("dispatchEvent(new Event('input'", join)


class TestErrorCorreo(unittest.TestCase):
    def test_detecta_el_rechazo_por_correo(self):
        for texto in (
            "×Debe Confirmar el correo electrónico.Continuar",
            "Debe confirmar el correo electrónico",
        ):
            self.assertTrue(_es_error_correo(texto), texto)

    def test_no_confunde_con_dialogo_normal(self):
        for texto in (
            "Confirmar DatosLugar donde se interpone la tutela...",
            "tu tutela ha sido recibida con éxito con el número 11001-2026-00009",
            "A través de este portal solo se recibe la acción...",
        ):
            self.assertFalse(_es_error_correo(texto), texto)


class TestDiscapacidad(unittest.TestCase):
    _datos = {"accionante_nombre": "Juan Perez Lopez", "accionante_email": "a@b.com"}

    def _capturar_discapacidad(self, datos, cajon_abierto=True) -> list:
        bot = _make_bot(FakePage(cajon_abierto=cajon_abierto))
        llamadas = []

        async def fake_select(selector, label):
            llamadas.append((selector, label))

        with mock.patch.object(bot, "_fijar_select_sin_postback", new=fake_select), \
             mock.patch.object(bot, "_cerrar_jconfirm", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()):
            asyncio.run(bot._paso_accionante(datos))
        return llamadas

    def test_discapacidad_de_datos(self):
        """Si datos trae accionante_discapacidad, se usa ese valor."""
        llamadas = self._capturar_discapacidad({"accionante_discapacidad": "MENTAL", **self._datos})
        self.assertIn(("#DDlTipodiscapacidad", "MENTAL"), llamadas)

    def test_discapacidad_fallback_no_aplica(self):
        """Si no hay accionante_discapacidad, fallback a 'No Aplica'."""
        llamadas = self._capturar_discapacidad(dict(self._datos))
        self.assertIn(("#DDlTipodiscapacidad", "No Aplica"), llamadas)


class TestNombresACampos(unittest.TestCase):
    """Reparto de los 2 campos estructurados (nombres/apellidos) a los 4 del portal."""

    def test_dos_nombres_dos_apellidos(self):
        resultado = _nombres_a_campos("María Fernanda", "Pérez Gómez")
        self.assertEqual(resultado, {
            "primer_nombre": "María",
            "segundo_nombre": "Fernanda",
            "primer_apellido": "Pérez",
            "segundo_apellido": "Gómez",
        })

    def test_un_solo_nombre(self):
        resultado = _nombres_a_campos("Ana", "López Mora")
        self.assertEqual(resultado["primer_nombre"], "Ana")
        self.assertEqual(resultado["segundo_nombre"], "")
        self.assertEqual(resultado["primer_apellido"], "López")
        self.assertEqual(resultado["segundo_apellido"], "Mora")

    def test_campos_vacios(self):
        resultado = _nombres_a_campos("", "")
        self.assertEqual(resultado["primer_nombre"], "")
        self.assertEqual(resultado["primer_apellido"], "")

    def test_el_paso_accionante_prefiere_campos_estructurados(self):
        """Con accionante_nombres/apellidos, se escribe la partición exacta al portal."""
        bot = _make_bot(FakePage())
        campos_escritos = {}

        async def fake_type(selector, value):
            campos_escritos[selector] = value

        with mock.patch.object(bot, "_fijar_select_sin_postback", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_cerrar_jconfirm", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_type_existing", new=fake_type):
            asyncio.run(bot._paso_accionante({
                "accionante_nombres": "María Fernanda",
                "accionante_apellidos": "Pérez Gómez",
                "accionante_email": "a@b.com",
            }))
        self.assertEqual(campos_escritos["#PrimerNombre"], "María")
        self.assertEqual(campos_escritos["#SegundoNombre"], "Fernanda")
        self.assertEqual(campos_escritos["#PrimerApellido"], "Pérez")
        self.assertEqual(campos_escritos["#SegundoApellido"], "Gómez")

    def test_el_paso_accionante_reaplica_identidad_al_final(self):
        """El portal puede re-renderizar los campos de identidad al resolver el
        AJAX del tipo de documento (queja de prod: queda en 'Seleccione...' y
        nombres en blanco/autocompletados). El paso vuelve a aplicar tipo doc +
        cédula + nombres justo antes del readback."""
        bot = _make_bot(FakePage())
        selecciones = []
        veces_primer_nombre = []

        async def fake_select(selector, label):
            selecciones.append((selector, label))

        async def fake_type(selector, value):
            if selector == "#PrimerNombre":
                veces_primer_nombre.append(value)

        with mock.patch.object(bot, "_fijar_select_sin_postback", new=fake_select), \
             mock.patch.object(bot, "_cerrar_jconfirm", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_type_existing", new=fake_type):
            asyncio.run(bot._paso_accionante({
                "accionante_nombres": "María Fernanda",
                "accionante_apellidos": "Pérez Gómez",
                "accionante_email": "a@b.com",
            }))

        veces_cc = [s for s in selecciones if s == ("#DDlTipodocumento", "CC")]
        self.assertGreaterEqual(
            len(veces_cc), 2, "El tipo de documento debe re-aplicarse al final"
        )
        self.assertGreaterEqual(
            len(veces_primer_nombre), 2, "Los nombres se re-escriben al final"
        )

    def test_reaplicar_identidad_no_reescribe_la_cedula(self):
        """Re-escribir la cédula relanza el autofill del portal (nombres
        resueltos por cédula) y vuelve a pisar los nombres — la causa de que
        el readback de prod siguiera viendo 'E  Ramirez Montoya'/'Seleccione...'
        tras la re-aplicación. La re-aplicación escribe nombres/teléfono/email
        pero NUNCA la cédula."""
        bot = _make_bot(FakePage())
        escritos = {}
        selecciones = []

        async def fake_type(selector, value):
            escritos[selector] = value

        async def fake_fijar(selector, label):
            selecciones.append((selector, label))

        with mock.patch.object(bot, "_fijar_select_sin_postback", new=fake_fijar), \
             mock.patch.object(bot, "_type_existing", new=fake_type):
            asyncio.run(bot._aplicar_identidad_accionante({
                "accionante_nombres": "María Fernanda",
                "accionante_apellidos": "Pérez Gómez",
                "accionante_telefono": "31174598",
                "accionante_email": "harold0.1@hotmail.com",
            }))

        self.assertNotIn(
            "#NumeroDocumento", escritos,
            "Re-escribir la cédula relanza el autofill y pisa los nombres",
        )
        self.assertEqual(escritos["#PrimerNombre"], "María")
        self.assertEqual(escritos["#SegundoNombre"], "Fernanda")
        self.assertEqual(escritos["#PrimerApellido"], "Pérez")
        self.assertEqual(escritos["#SegundoApellido"], "Gómez")
        self.assertEqual(escritos["#Telefono"], "31174598")
        self.assertEqual(escritos["#Email"], "harold0.1@hotmail.com")
        veces_cc = [s for s in selecciones if s == ("#DDlTipodocumento", "CC")]
        self.assertGreaterEqual(
            len(veces_cc), 1, "El tipo de documento debe fijarse (y re-fijarse al final) sin postback"
        )
        self.assertIn(("#DDlTipodiscapacidad", "No Aplica"), selecciones)

    def test_fijar_select_sin_postback_nunca_usa_select_option(self):
        """#DDlTipodocumento dispara __doPostBack por un listener DELEGADO
        (NO inline: quitarlo no sirve, evidencia del dump HTML de prod). Por eso
        el fijador SOLO usa value+selectedIndex directo SIN eventos: `select_option`
        (o cualquier change real) relanza el postback y borra toda la sección.
        Si el portal re-renderiza tras fijar, se re-sincroniza hasta estabilizar."""
        class PageSelectValor(FakePage):
            def __init__(self):
                super().__init__()
                self.eventos = []
                self.resyncs = 0

            async def evaluate(self, script, *args, **kwargs):
                if "ALIASES" in script:
                    self.eventos.append("match")
                    return "2"
                if "options[i]" in script:
                    self.resyncs += 1
                    # El primer fijado queda 'Seleccione...' (un postback en vuelo
                    # lo pisó); el resync lo deja bien.
                    return "Seleccione..." if self.resyncs == 1 else "CÉDULA DE CIUDADANÍA"
                return None

            async def select_option(self, *args, **kwargs):
                self.eventos.append("select_option")

        bot = _make_bot(PageSelectValor())
        value = asyncio.run(bot._fijar_select_sin_postback("#DDlTipodocumento", "CC"))
        self.assertEqual(value, "2")
        self.assertNotIn("select_option", bot.page.eventos, "NUNCA debe usarse select_option: dispara el postback")
        self.assertIn("match", bot.page.eventos)
        self.assertGreaterEqual(bot.page.resyncs, 2, "El resync debe re-fijar si un postback pisó el select")

    def test_fijar_select_sin_postback_devuelve_none_si_widget_no_sincroniza(self):
        """Si ni el fijado directo ni los resyncs quedan reflejados (el select
        sigue mostrando 'Seleccione...'), devuelve None para que el flujo lo
        diagnóstique en lugar de asumir que quedó bien."""
        class PageSelectWidget(FakePage):
            def __init__(self):
                super().__init__()

            async def evaluate(self, script, *args, **kwargs):
                if "ALIASES" in script:
                    return "2"
                if "options[i]" in script:
                    return "Seleccione..."
                return None

            async def select_option(self, *args, **kwargs):
                return None

        bot = _make_bot(PageSelectWidget())
        value = asyncio.run(bot._fijar_select_sin_postback("#DDlTipodocumento", "CC"))
        self.assertIsNone(value, "Si el select quedó en 'Seleccione...' no debe afirmar éxito")

    def test_completar_post_codigo_reaplica_identidad_tras_verificar_email(self):
        """El postback de #btnValidar (y el re-ingreso del correo) re-renderiza
        la sección del accionante desde el servidor y borra lo escrito. Antes
        del paso 5 (accionado) se debe re-aplicar la identidad del accionante."""
        bot = _make_bot(FakePage())
        bot.on_paso = None
        reaplicado = []

        async def fake_reaplicar(datos):
            reaplicado.append(datos)

        with mock.patch("app.bot.navegador.settings.simulate_bot", False), \
             mock.patch.object(bot, "_aplicar_identidad_accionante", new=fake_reaplicar), \
             mock.patch.object(bot, "_paso_accionado", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_paso_derechos", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_paso_archivos", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_paso_juramento", new=mock.AsyncMock()):
            resultado = asyncio.run(bot.completar_post_codigo({"cedula": "1029345"}, "ruta.pdf"))

        self.assertTrue(resultado.get("ok"))
        self.assertEqual(len(reaplicado), 1, "La identidad debe re-aplicarse tras la verificación del email")
        self.assertEqual(reaplicado[0], {"cedula": "1029345"})

    def test_el_accionado_siempre_es_juridica(self):
        """El accionado de la tutela SIEMPRE es una EPS (persona jurídica):
        el paso selecciona 'Jurídica' en #DDlTipoSujeto + NIT aunque los datos
        del chat digan 'natural'."""
        bot = _make_bot(FakePage())
        selecciones = []

        async def fake_select(selector, label):
            selecciones.append((selector, label))

        with mock.patch.object(bot, "_seleccionar_select", new=fake_select), \
             mock.patch.object(bot, "_cerrar_jconfirm", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()):
            asyncio.run(bot._paso_accionado({
                "accionado_tipo": "natural",
                "accionado": "EPS Sanitas",
                "accionado_nit": "890123456",
            }))

        self.assertEqual(selecciones[0], ("#DDlTipoSujeto", "Jurídica"))
        self.assertIn(("#DDlTipodocumentoAccionado", "NIT"), selecciones)


class TestCerrarContexto(unittest.TestCase):
    def test_cerrar_cierra_el_contexto_completo_no_solo_la_pagina(self):
        """Cada intento abre su propio contexto (BrowserManager.new_context); si
        cerrar() no lo libera, se acumulan contextos huérfanos en el chromium
        singleton entre reintentos (memoria en Render)."""
        page = FakePage()
        bot = _make_bot(page)
        with mock.patch("app.bot.navegador.settings.simulate_bot", False):
            asyncio.run(bot.cerrar())
        self.assertTrue(page.context._cerrado)
        self.assertIsNone(bot.page)


class TestInfoArchivo(unittest.TestCase):
    def test_entrega_identidad_del_archivo(self):
        """El sha1/basename permiten verificar qué PDF exacto se subió al portal."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"contenido-tutela-demo")
            ruta = f.name
        try:
            info = _info_archivo(ruta)
            self.assertEqual(info["basename"], os.path.basename(ruta))
            self.assertEqual(info["bytes"], 21)
            self.assertNotEqual(info["sha1"], "")
            # Determinista y distinto para otro contenido
            self.assertEqual(_info_archivo(ruta)["sha1"], info["sha1"])
        finally:
            os.remove(ruta)

    def test_archivo_inexistente_no_rompe(self):
        info = _info_archivo("no/existe.pdf")
        self.assertIn("error", info)


class TestDiagnosticoAccionante(unittest.TestCase):
    """Cuando el readback del accionante sale vacío, se vuelca el estado de la
    sección (opciones del select + campos) y se toma screenshot, para saber de
    dónde viene la queja de prod sin depender del panel."""

    def _pagina_con_readback_vacio(self):
        class PageReadbackVacio(FakePage):
            async def evaluate(self, script, *args, **kwargs):
                if isinstance(script, str) and "primer_nombre" in script:
                    return {
                        "tipo_doc": "Seleccione...",
                        "numero": "",
                        "primer_nombre": "",
                        "segundo_nombre": "",
                        "primer_apellido": "",
                        "segundo_apellido": "",
                        "telefono": "",
                        "email": "",
                    }
                return None

        return PageReadbackVacio(cajon_abierto=False)

    def test_el_readback_vacio_dispara_diagnostico_y_screenshot(self):
        bot = _make_bot(self._pagina_con_readback_vacio())
        with mock.patch.object(bot, "_seleccionar_select", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_cerrar_jconfirm", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_log_diagnostico_accionante", new=mock.AsyncMock()) as m_diag, \
             mock.patch.object(bot, "_capturar_evidencia", new=mock.AsyncMock()) as m_cap, \
             mock.patch.object(bot, "_type_existing", new=mock.AsyncMock()):
            asyncio.run(bot._paso_accionante({
                "accionante_nombre": "Ester Ramirez Montoya",
                "accionante_email": "harold0.1@hotmail.com",
            }))

        m_diag.assert_awaited()
        m_cap.assert_awaited_once()
        self.assertEqual(m_cap.await_args.args[0], "accionante_vacio")

    def test_el_diagnostico_escribe_logs_sin_romper(self):
        bot = _make_bot(FakePage())
        asyncio.run(bot._log_diagnostico_accionante())

    def test_readback_accionante_no_rompe_sin_pagina(self):
        """La sonda de readback intermedio nunca rompe el flujo aunque el
        evaluate falle (página cerrada / elemento ausente)."""
        bot = _make_bot(None)
        bot.page = FakePage()
        readback = asyncio.run(bot._readback_accionante("accionante_1_tras_cedula"))
        self.assertIsNone(readback)


if __name__ == "__main__":
    unittest.main()
