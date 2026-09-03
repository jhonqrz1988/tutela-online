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
    def __init__(self, idemail_visible: bool = False):
        self.idemail_visible = idemail_visible
        self.tipodiscapacidad_argumento = None

    async def query_selector(self, selector: str):
        if selector == "#IdEmail1":
            if self.idemail_visible:
                return FakeElement(True)
            return None
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

    def test_sin_campo_idemail_retorna_false(self):
        """Si #IdEmail1 no es visible (correo ya registrado), NO requiere código."""
        bot = _make_bot(FakePage(idemail_visible=False))
        with mock.patch.object(bot, "_seleccionar_select", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_cerrar_jconfirm", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()):
            requiere = self._ejecutar_paso_accionante(bot, self._datos)
        self.assertFalse(requiere, "Si el portal no pide verificación, debe retornar False")

    def test_con_campo_idemail_retorna_true(self):
        """Si #IdEmail1 es visible (portal pide verificación), requiere código."""
        bot = _make_bot(FakePage(idemail_visible=True))
        with mock.patch.object(bot, "_seleccionar_select", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_cerrar_jconfirm", new=mock.AsyncMock()), \
             mock.patch.object(bot, "_js_click", new=mock.AsyncMock()):
            requiere = self._ejecutar_paso_accionante(bot, self._datos)
        self.assertTrue(requiere, "Si el portal pide verificación, debe retornar True")


class TestDiscapacidad(unittest.TestCase):
    _datos = {"accionante_nombre": "Juan Perez Lopez", "accionante_email": "a@b.com"}

    def _capturar_discapacidad(self, datos, idemail_visible=True) -> list:
        bot = _make_bot(FakePage(idemail_visible=idemail_visible))
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
