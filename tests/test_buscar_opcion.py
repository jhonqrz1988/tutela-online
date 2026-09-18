"""Tests del matcher de opciones de <select> del portal (tutela 50 fallida).

El bug de prod: `_JS_BUSCAR_OPTION_SELECT` devolvía la PRIMERA coincidencia
parcial en orden DOM. Al buscar "Santander" en #DdlDepartamento, la opción
"N. DE SANTANDER" (20) aparece antes que "SANTANDER" (23) y ganaba el match;
se cargaban las ciudades de Norte de Santander (Cúcuta...), "Bucaramanga" no
estaba, la ciudad de envío no quedaba y la sección de hechos
(#DdlDepartamentoHechos) nunca se hacía visible -> timeout select_option.

El fix: matching en dos pasadas que PRIORIZA la coincidencia exacta
(texto/normalizado/alias) sobre la parcial.
"""
import unittest

from app.bot.navegador import _buscar_valor_option


class TestBuscarValorOpcion(unittest.TestCase):
    def test_santander_gana_a_norte_de_santander(self):
        """REGRESIÓN tutela 50: buscar 'Santander' debe elegir 'SANTANDER' (23),
        no 'N. DE SANTANDER' (20) por ser la primera coincidencia parcial."""
        opciones = [
            ("20", "N. DE SANTANDER"),
            ("23", "SANTANDER"),
        ]
        self.assertEqual(_buscar_valor_option(opciones, "Santander"), "23")

    def test_ciudad_exacta_case_insensitive(self):
        opciones = [("884", "BUCARAMANGA")]
        self.assertEqual(_buscar_valor_option(opciones, "bucaramanga"), "884")

    def test_alias_cc_cedeula_de_ciudadania(self):
        opciones = [("3", "CÉDULA DE EXTRANJERÍA"), ("2", "CÉDULA DE CIUDADANÍA")]
        self.assertEqual(_buscar_valor_option(opciones, "CC"), "2")

    def test_normalizado_sin_puntos_ni_espacios(self):
        opciones = [("4", "C.C."), ("5", "C.E.")]
        self.assertEqual(_buscar_valor_option(opciones, "cc"), "4")

    def test_exacto_supera_a_parcial_normalizado(self):
        opciones = [
            ("20", "N. DE SANTANDER"),
            ("23", "SANTANDER"),
        ]
        self.assertEqual(_buscar_valor_option(opciones, "SANTANDER"), "23")

    def test_parcial_como_fallback(self):
        """Si no hay match exacto, el parcial sigue sirviendo (ej. 'Valle' -> 'VALLE DEL CAUCA')."""
        opciones = [("76", "VALLE DEL CAUCA")]
        self.assertEqual(_buscar_valor_option(opciones, "Valle"), "76")

    def test_sin_match_devuelve_none(self):
        self.assertIsNone(_buscar_valor_option([("1", "BOGOTÁ")], "MEDELLÍN"))
        self.assertIsNone(_buscar_valor_option([], "Santander"))
        self.assertIsNone(_buscar_valor_option(None, "Santander"))

    def test_alias_expandido_normalizado(self):
        """'TI' expande a 'tarjeta de identidad' (normalizado sin tildes)."""
        opciones = [("7", "Tarjeta de identidad")]
        self.assertEqual(_buscar_valor_option(opciones, "TI"), "7")

    def test_pep_permiso_especial(self):
        opciones = [("9", "Permiso especial de permanencia")]
        self.assertEqual(_buscar_valor_option(opciones, "PEP"), "9")


if __name__ == "__main__":
    unittest.main()