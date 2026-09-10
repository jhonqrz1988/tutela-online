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
import unittest
from unittest import mock

from app.bot.navegador import RadicadorBot


class FakeElement:
    def __init__(self, visible: bool):
        self._visible = visible

    async def is_visible(self) -> bool:
        return self._visible


class FakePage:
    def __init__(self, cajon_abierto: bool = False):
        self.cajon_abierto = cajon_abierto
        self.tipodiscapacidad_argumento = None

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


if __name__ == "__main__":
    unittest.main()
