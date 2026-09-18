import asyncio
import unittest
from unittest import mock

from app.bot.navegador import RadicadorBot
from app.bot.normalizacion import (
    _MODO_NORMALIZACION,
    normalizar_campo,
    normalizar_email,
    normalizar_numero,
    normalizar_telefono,
    normalizar_texto,
    quitar_acentos,
    verificar_igual,
)


class TestNormalizarModulos(unittest.TestCase):
    """El normalizador de la capa Playwright convierte valores dispares del
    portal (mayúsculas, tildes, puntos, +57) a una forma determinista."""

    def test_quitar_acentos(self):
        self.assertEqual(quitar_acentos("María Ramírez"), "Maria Ramirez")

    def test_normalizar_texto_colapsa_espacios_y_minusculas(self):
        self.assertEqual(normalizar_texto("  María   Fernanda  "), "maria fernanda")

    def test_normalizar_numero_solo_digitos(self):
        self.assertEqual(normalizar_numero("1.036.929.537"), "1036929537")

    def test_normalizar_telefono_quita_mas_espacios_guiones(self):
        self.assertEqual(normalizar_telefono("+57 (601) 234-567"), "57601234567")

    def test_normalizar_email_minusculas(self):
        self.assertEqual(normalizar_email("  CONTACTO@EPS-SANITAS.COM "), "contacto@eps-sanitas.com")

    def test_normalizar_campo_segun_tipo(self):
        self.assertEqual(normalizar_campo("accionante_cedula", "1036.929 537"), "1036929537")
        self.assertEqual(normalizar_campo("accionado_nit", "890.123.456"), "890123456")
        self.assertEqual(normalizar_campo("accionante_nombres", "  María   Fernanda  "), "maria fernanda")
        self.assertEqual(normalizar_campo("accionado_email", "A@B.COM"), "a@b.com")

    def test_mapa_campos_incluye_campos_verificados(self):
        for campo in ("accionante_cedula", "accionante_telefono", "accionante_email",
                      "accionado_nit", "accionado_telefono", "accionado_email"):
            self.assertIn(campo, _MODO_NORMALIZACION)


class TestVerificarIgual(unittest.TestCase):
    """verificar_igual es la comparación tolerante que usa _verificar_valor."""

    def test_texto_ignora_tildes_mayusculas_y_espacios(self):
        self.assertTrue(verificar_igual("Ramírez", "RAMIREZ", "texto"))
        self.assertTrue(verificar_igual("EPS Sanitas", "  eps  sanitas ", "texto"))

    def test_texto_diferencias_reales_fallan(self):
        self.assertFalse(verificar_igual("Ramirez", "Ramirez2", "texto"))

    def test_numero_quita_puntos_espacios(self):
        self.assertTrue(verificar_igual("1036.929 537", "1036929537", "numero"))
        self.assertFalse(verificar_igual("1036929537", "103692953", "numero"))

    def test_telefono_tolera_prefijo_57(self):
        self.assertTrue(verificar_igual("+57 301 234 5678", "3012345678", "telefono"))
        self.assertTrue(verificar_igual("573012345678", "3012345678", "telefono"))
        self.assertFalse(verificar_igual("3012345678", "3012345679", "telefono"))

    def test_email_insensible_a_mayusculas(self):
        self.assertTrue(verificar_igual("A@B.COM", "a@b.com", "email"))


class FakePageMemoria:
    """Página falsa que recuerda lo escrito por selector y lo devuelve al leer
    (patrón de `_leer_valor_input`). Permite ejercitar _verificar_valor."""

    def __init__(self):
        self.valores = {}

    async def type(self, selector, texto, **kwargs):
        self.valores[selector] = str(texto or "")

    async def evaluate(self, script, argumento=None, **kwargs):
        if argumento and isinstance(argumento, list) and len(argumento) == 1 and "querySelector(" in str(script):
            return self.valores.get(argumento[0], "")
        return None

    async def wait_for_timeout(self, *args, **kwargs):
        return None


def _make_bot(page):
    bot = RadicadorBot.__new__(RadicadorBot)
    bot.page = page
    bot.on_paso = None
    bot._screenshot_dir = None
    return bot


class TestVerificarValor(unittest.TestCase):
    """_verificar_valor: llenar -> leer -> comparar con el normalizador."""

    def setUp(self):
        self.page = FakePageMemoria()
        self.bot = _make_bot(self.page)

    def test_acepta_campo_que_quedo_igual(self):
        self.page.valores["#Campo"] = "Maria"
        asyncio.run(self.bot._verificar_valor("#Campo", "Maria", "nombre"))
        # Sin re-escritura: el valor ya era el esperado

    def test_acepta_valor_tolerado_por_normalizador(self):
        self.page.valores["#Cedula"] = "1036929537"
        asyncio.run(self.bot._verificar_valor("#Cedula", "1.036.929.537", "cédula", "numero"))

    def test_reescribe_una_vez_y_acepta(self):
        escrituras = []

        async def type_fake(selector, texto, **kwargs):
            escrituras.append((selector, texto))
            self.page.valores[selector] = str(texto or "")

        with mock.patch.object(self.bot, "_type", new=type_fake), \
             mock.patch.object(self.bot, "_capturar_evidencia", new=mock.AsyncMock()):
            # El portal "pisa" el primer valor: escribimos vacío para luego
            # re-escribirlo bien en la segunda pasada.
            self.page.valores["#Campo"] = "otro"
            asyncio.run(self.bot._verificar_valor("#Campo", "esperado", "nombre"))
        self.assertEqual(len(escrituras), 1, "Se re-escribe una vez si el portal no quedó con el valor")

    def test_falla_con_error_preciso_tras_reescribir(self):
        self.page.valores["#Campo"] = "siempre mal"

        with mock.patch.object(self.bot, "_type", new=mock.AsyncMock()), \
             mock.patch.object(self.bot, "_capturar_evidencia", new=mock.AsyncMock()), \
             self.assertRaises(ValueError) as ctx:
            asyncio.run(self.bot._verificar_valor("#Campo", "esperado", "nombre"))

        self.assertIn("Portal no aceptó nombre", str(ctx.exception))
        self.assertIn("esperado 'esperado'", str(ctx.exception))

    def test_captura_evidencia_al_fallar(self):
        self.page.valores["#Campo"] = "nunca"

        with mock.patch.object(self.bot, "_type", new=mock.AsyncMock()), \
             mock.patch.object(self.bot, "_capturar_evidencia", new=mock.AsyncMock()) as captura, \
             self.assertRaises(ValueError):
            asyncio.run(self.bot._verificar_valor("#Campo", "esperado", "nombre del campo"))

        captura.assert_called_once()
        tag = captura.call_args.args[0]
        self.assertIn("campo_", tag)

    def test_lee_con_evaluate_y_selector(self):
        """Es la lectura real: _leer_valor_input consulta el DOM por selector."""
        self.page.valores["#Telefono"] = "573012345678"
        asyncio.run(self.bot._verificar_valor("#Telefono", "3012345678", "teléfono", "telefono"))


if __name__ == "__main__":
    unittest.main()