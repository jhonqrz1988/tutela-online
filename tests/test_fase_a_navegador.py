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

from app.bot.navegador import RadicadorBot, _info_archivo, _nombres_a_campos


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
        NO requiere código."""
        bot = _make_bot(FakePage(cajon_abierto=False))
        with mock.patch.object(bot, "_seleccionar_select", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_cerrar_jconfirm", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()):
            requiere = self._ejecutar_paso_accionante(bot, self._datos)
        self.assertFalse(requiere, "Si el portal no abre el cajón, debe retornar False")

    def test_con_cajon_retorna_true(self):
        """Si el portal abre el cajón de verificación, requiere código."""
        bot = _make_bot(FakePage(cajon_abierto=True))
        with mock.patch.object(bot, "_seleccionar_select", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_cerrar_jconfirm", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()):
            requiere = self._ejecutar_paso_accionante(bot, self._datos)
        self.assertTrue(requiere, "Si el portal abre el cajón del código, debe retornar True")


class TestDiscapacidad(unittest.TestCase):
    _datos = {"accionante_nombre": "Juan Perez Lopez", "accionante_email": "a@b.com"}

    def _capturar_discapacidad(self, datos, cajon_abierto=True) -> list:
        bot = _make_bot(FakePage(cajon_abierto=cajon_abierto))
        llamadas = []

        async def fake_select(selector, label):
            llamadas.append((selector, label))

        with mock.patch.object(bot, "_seleccionar_select", new=fake_select), \
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

        with mock.patch.object(bot, "_seleccionar_select", new=mock.AsyncMock()), \
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

        with mock.patch.object(bot, "_seleccionar_select", new=fake_select), \
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

        async def fake_select(selector, label):
            selecciones.append((selector, label))

        with mock.patch.object(bot, "_seleccionar_select", new=fake_select), \
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
            len(veces_cc), 1, "El tipo de documento debe seleccionarse (y re-seleccionarse al final)"
        )
        self.assertIn(("#DDlTipodiscapacidad", "No Aplica"), selecciones)

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


if __name__ == "__main__":
    unittest.main()
