"""Tests para la validación de datos personales en la entrada de WhatsApp.

Cubre la parte 1: el bot no debe escribir datos mal formados en el portal.
- `validar_campo_personal` decide si cédula/teléfono/email/nombres/apellidos son aceptables.
- `normalizar_campo` limpia el valor antes de guardarlo (puntos, espacios, mayúsculas).
- `procesar_campo_personal` orquesta entrada por paso: avanza, reintenta (con
  tope) o acepta con aviso sin bloquear al usuario.
"""
import unittest

from app.utils.validacion import (
    MAX_REINTENTOS_VALIDACION,
    normalizar_campo,
    procesar_campo_personal,
    validar_campo_personal,
)


class TestValidarCampoPersonal(unittest.TestCase):
    def test_nombre_compuesto_no_se_valida(self):
        """El campo compuesto `accionante_nombre` (legado) siempre pasa."""
        self.assertIsNone(validar_campo_personal("accionante_nombre", "Ana López"))
        self.assertIsNone(validar_campo_personal("ciudad", "Bogotá"))
        self.assertIsNone(validar_campo_personal("accionado", "Nueva EPS"))

    def test_cedula_valida(self):
        self.assertIsNone(validar_campo_personal("accionante_cedula", "1030241555"))

    def test_cedula_demasiado_corta(self):
        self.assertIsNotNone(validar_campo_personal("accionante_cedula", "123"))

    def test_cedula_con_letras(self):
        self.assertIsNotNone(validar_campo_personal("accionante_cedula", "abc123"))

    def test_telefono_valido_con_movil(self):
        self.assertIsNone(validar_campo_personal("accionante_telefono", "3001234567"))

    def test_telefono_valido_con_prefijo(self):
        self.assertIsNone(validar_campo_personal("accionante_telefono", "+573001234567"))

    def test_telefono_demasiado_corto(self):
        self.assertIsNotNone(validar_campo_personal("accionante_telefono", "123"))

    def test_email_valido(self):
        self.assertIsNone(validar_campo_personal("accionante_email", "ana@correo.com"))

    def test_email_sin_arroba(self):
        self.assertIsNotNone(validar_campo_personal("accionante_email", "ana.correo.com"))


class TestNombresApellidos(unittest.TestCase):
    def test_nombres_validos_con_acentos(self):
        self.assertIsNone(validar_campo_personal("accionante_nombres", "María Fernanda"))
        self.assertIsNone(validar_campo_personal("accionante_apellidos", "Pérez Gómez"))

    def test_nombre_simple_valido(self):
        self.assertIsNone(validar_campo_personal("accionante_nombres", "Ana"))
        self.assertIsNone(validar_campo_personal("accionante_apellidos", "López"))

    def test_apellido_compuesto_con_guion_valido(self):
        self.assertIsNone(validar_campo_personal("accionante_apellidos", "Mora-Pinto"))

    def test_apellido_con_particula_valido(self):
        self.assertIsNone(validar_campo_personal("accionante_apellidos", "de la Cruz"))

    def test_nombre_con_letra_sola_invalido(self):
        self.assertIsNotNone(validar_campo_personal("accionante_nombres", "a"))

    def test_nombre_con_numeros_invalido(self):
        self.assertIsNotNone(validar_campo_personal("accionante_nombres", "Juan 123"))
        self.assertIsNotNone(validar_campo_personal("accionante_apellidos", "Pérez2"))

    def test_demasiadas_palabras_invalido(self):
        self.assertIsNotNone(
            validar_campo_personal("accionante_apellidos", "Pérez Gómez De La Otra Casa")
        )

    def test_normaliza_espacios_internos_de_nombre(self):
        self.assertEqual(
            normalizar_campo("accionante_nombres", "María   Fernanda"),
            "María Fernanda",
        )
        self.assertEqual(
            normalizar_campo("accionante_apellidos", "  Pérez  Gómez "),
            "Pérez Gómez",
        )

    def test_procesar_nombres_validos_avanza(self):
        datos = {}
        valor, accion = procesar_campo_personal(datos, "accionante_nombres", " María  Fernanda ")
        self.assertEqual(valor, "María Fernanda")
        self.assertEqual(accion, "ok")
        self.assertNotIn("_val_accionante_nombres", datos)


class TestNormalizarCampo(unittest.TestCase):
    def test_cedula_quita_puntos_y_espacios(self):
        self.assertEqual(
            normalizar_campo("accionante_cedula", " 1.030.241.555 "),
            "1030241555",
        )

    def test_telefono_quita_espacios_mantiene_mas(self):
        self.assertEqual(
            normalizar_campo("accionante_telefono", "+57 300 123 4567"),
            "+573001234567",
        )

    def test_email_minusculas_y_sin_espacios(self):
        self.assertEqual(
            normalizar_campo("accionante_email", "  Ana@Correo.com "),
            "ana@correo.com",
        )


class TestProcesarCampoPersonal(unittest.TestCase):
    def test_campo_valido_avanza(self):
        """Valor válido → se guarda normalizado y se avanza (accion 'ok')."""
        datos = {}
        valor, accion = procesar_campo_personal(
            datos, "accionante_cedula", " 1.030.241.555 "
        )
        self.assertEqual(valor, "1030241555")
        self.assertEqual(accion, "ok")
        self.assertNotIn("_val_accionante_cedula", datos)

    def test_campo_invalido_reintenta_sin_avanzar(self):
        """Valor inválido → reintenta (accion 'reintento'), no avanza el paso."""
        datos = {}
        valor, accion = procesar_campo_personal(
            datos, "accionante_email", "sin-arroba"
        )
        self.assertEqual(accion, "reintento")
        self.assertEqual(valor, "sin-arroba")
        self.assertEqual(datos.get("_val_accionante_email"), 1)

    def test_campo_invalido_se_acepta_tras_tope(self):
        """Tras superar el tope se acepta con aviso (no bloquea al usuario)."""
        datos = {"_val_accionante_email": MAX_REINTENTOS_VALIDACION}
        valor, accion = procesar_campo_personal(
            datos, "accionante_email", "sigue-mal"
        )
        self.assertEqual(accion, "aceptado")
        self.assertEqual(datos["_val_accionante_email"], MAX_REINTENTOS_VALIDACION + 1)

    def test_el_tope_no_se_excede(self):
        """Aumenta el contador en cada intento, nunca revienta el flujo."""
        datos = {}
        for i in range(1, MAX_REINTENTOS_VALIDACION + 2):
            _, accion = procesar_campo_personal(
                datos, "accionante_telefono", "abc"
            )
            self.assertEqual(datos["_val_accionante_telefono"], i)
            self.assertIn(accion, ("reintento", "aceptado"))


if __name__ == "__main__":
    unittest.main()