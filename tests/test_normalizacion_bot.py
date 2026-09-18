import asyncio
import unittest
from unittest import mock

from app.bot.navegador import RadicadorBot
from app.bot.normalizacion import (
    TIPO_DOCUMENTO_EQUIVALENCIAS,
    _MODO_NORMALIZACION,
    etiqueta_portal_tipo_doc,
    normalizar_campo,
    normalizar_email,
    normalizar_numero,
    normalizar_telefono,
    normalizar_texto,
    normalizar_tipo_doc,
    quitar_acentos,
    tipo_doc_equivale,
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

    async def fill(self, selector, texto, **kwargs):
        self.valores[selector] = str(texto or "")

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

        with mock.patch.object(self.bot, "_type_existing", new=type_fake), \
             mock.patch.object(self.bot, "_capturar_evidencia", new=mock.AsyncMock()):
            # El portal "pisa" el primer valor: escribimos vacío para luego
            # re-escribirlo bien en la segunda pasada.
            self.page.valores["#Campo"] = "otro"
            asyncio.run(self.bot._verificar_valor("#Campo", "esperado", "nombre"))
        self.assertEqual(len(escrituras), 1, "Se re-escribe una vez si el portal no quedó con el valor")

    def test_reescribe_limpiando_campo_no_acumulando(self):
        """El re-write NO suma al contenido previo: limpia (fill) y re-escribe."""
        orden = []

        async def fill_fake(selector, texto, **kwargs):
            orden.append(("fill", texto))
            self.page.valores[selector] = str(texto or "")

        async def type_fake(selector, texto, **kwargs):
            orden.append(("type", texto))
            self.page.valores[selector] = str(texto or "")

        self.page.valores["#Campo"] = "E"
        with mock.patch.object(self.bot, "_type_existing", new=type_fake), \
             mock.patch.object(self.bot, "_capturar_evidencia", new=mock.AsyncMock()):
            asyncio.run(self.bot._verificar_valor("#Campo", "EPS SURA", "nombre del accionado"))

        self.assertEqual(orden, [("type", "EPS SURA")])
        self.assertEqual(self.page.valores["#Campo"], "EPS SURA")

    def test_falla_con_error_preciso_tras_reescribir(self):
        self.page.valores["#Campo"] = "siempre mal"

        with mock.patch.object(self.bot, "_type_existing", new=mock.AsyncMock()), \
             mock.patch.object(self.bot, "_capturar_evidencia", new=mock.AsyncMock()), \
             self.assertRaises(ValueError) as ctx:
            asyncio.run(self.bot._verificar_valor("#Campo", "esperado", "nombre"))

        self.assertIn("Portal no aceptó nombre", str(ctx.exception))
        self.assertIn("esperado 'esperado'", str(ctx.exception))

    def test_captura_evidencia_al_fallar(self):
        self.page.valores["#Campo"] = "nunca"

        with mock.patch.object(self.bot, "_type_existing", new=mock.AsyncMock()), \
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


class TestTablaTipoDocumento(unittest.TestCase):
    """La tabla de equivalencias TIPO DOCUMENTO es la fuente de verdad entre lo
    que pide el flujo (texto libre: 'CC', 'Pasaporte', 'Cédula') y las opciones
    del dropdown del portal (etiquetas con mayúsculas, guiones y tildes)."""

    def test_sinonimos_resuelven_a_clave_canonica(self):
        casos = {
            "CC": ("CC", "cc", "C.C.", "Cédula", "cedula de ciudadania", "Ciudadanía"),
            "CE": ("CE", "Cédula de extranjería", "extranjeria"),
            "TI": ("TI", "Tarjeta de Identidad", "tarjeta"),
            "PA": ("PA", "Pasaporte", "pas"),
            "PEP": ("PEP", "permiso especial de permanencia"),
            "RAMV": ("RAMV", "ramv"),
            "SC": ("SC", "Salvo Conducto", "s.c."),
            "PPT": ("PPT", "proteccion temporal", "permiso por protección temporal"),
        }
        for clave, sinonimos in casos.items():
            for valor in sinonimos:
                self.assertEqual(normalizar_tipo_doc(valor), clave, f"{valor!r} -> {clave}")

    def test_valores_desconocidos_devuelven_none(self):
        self.assertIsNone(normalizar_tipo_doc("Seleccione..."))
        self.assertIsNone(normalizar_tipo_doc(""))
        self.assertIsNone(normalizar_tipo_doc("no sé"))

    def test_etiqueta_portal_usa_etiqueta_exacta_del_portal(self):
        self.assertEqual(etiqueta_portal_tipo_doc("CC"), "CÉDULA DE CIUDADANÍA")
        self.assertEqual(etiqueta_portal_tipo_doc("Pasaporte"), "PASAPORTE")
        self.assertEqual(etiqueta_portal_tipo_doc("SALVO CONDUCTO"), "SALVO CONDUCTO")
        # https://github.com/USER/opencode/blob/... las etiquetas vienen del
        # dropdown real del portal (html_tipo_doc capturado en prod).

    def test_etiqueta_portal_default_cc_si_desconocido(self):
        self.assertEqual(etiqueta_portal_tipo_doc("xyr"), "CÉDULA DE CIUDADANÍA")
        self.assertEqual(etiqueta_portal_tipo_doc(""), "CÉDULA DE CIUDADANÍA")

    def test_tipo_doc_equivale_lee_etiqueta_del_portal(self):
        # El select devuelve la etiqueta (con tildes y mayúsculas); nosotros
        # enviamos el código corto del flujo. Deben equivaler.
        self.assertTrue(tipo_doc_equivale("CÉDULA DE CIUDADANÍA", "CC"))
        self.assertTrue(tipo_doc_equivale("cedula de ciudadania", "C.C."))
        self.assertTrue(tipo_doc_equivale("PASAPORTE", "Pasaporte"))
        self.assertFalse(tipo_doc_equivale("CÉDULA DE EXTRANJERÍA", "CC"))
        self.assertFalse(tipo_doc_equivale("Seleccione...", "CC"))

    def test_todas_las_etiquetas_del_portal_resuelven(self):
        """Cada opción real del dropdown tiene fila en la tabla (evita el bug
        'siempre es el mismo error': opción del portal sin mapear)."""
        portal = [
            "CÉDULA DE CIUDADANÍA", "CÉDULA DE EXTRANJERÍA", "TARJETA DE IDENTIDAD",
            "PASAPORTE", "PERMISO ESPECIAL DE PERMANENCIA",
            "PERMISO ESPECIAL DE PERMANENCIA - RAMV", "SALVO CONDUCTO",
            "PERMISO POR PROTECCIÓN TEMPORAL",
        ]
        for opcion in portal:
            self.assertIsNotNone(normalizar_tipo_doc(opcion), f"opción del portal sin mapear: {opcion}")

    def test_etiquetas_portal_sin_duplicados(self):
        claves = list(TIPO_DOCUMENTO_EQUIVALENCIAS)
        self.assertEqual(len(claves), len(set(claves)))

        portales = [info["portal"] for info in TIPO_DOCUMENTO_EQUIVALENCIAS.values()]
        self.assertEqual(len(portales), len(set(normalizar_texto(p) for p in portales)),
                         "Dos filas apuntan a la misma etiqueta del portal")


if __name__ == "__main__":
    unittest.main()