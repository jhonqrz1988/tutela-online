"""Tests para el arranque resiliente: `init_db` con reconexión.

En Render, Postgres puede recién reiniciarse justo al desplegar y la primera
conexión SSL falla ("SSL connection has been closed unexpectedly") -> uvicorn
abortaba el boot con status 3. `_crear_tablas_con_reintentos` reintenta con
backoff hasta que la base responde, y solo propaga el error si agota todos.
"""
import unittest
from unittest import mock

import sqlalchemy.exc

from app.database import DB_REINTENTOS, init_db


def _error_operacional():
    return sqlalchemy.exc.OperationalError(
        "SELECT 1", {}, Exception("SSL connection has been closed unexpectedly")
    )


class TestInitDbConReintentos(unittest.TestCase):
    def test_reintenta_de_forma_transitoria_y_eventualmente_arranca(self):
        """Si la base cae las 2 primeras veces y responde a la 3ª, arranca."""
        llamadas = {"n": 0}

        def fake_connect():
            llamadas["n"] += 1
            if llamadas["n"] < 3:
                raise _error_operacional()
            return mock.MagicMock()

        with mock.patch("app.database.engine") as motor, \
             mock.patch("app.database.time.sleep") as dormir, \
             mock.patch("app.database.Base.metadata.create_all") as crear:
            motor.connect.side_effect = fake_connect
            init_db()

        self.assertEqual(llamadas["n"], 3)
        crear.assert_called_once()
        dormir.assert_called()

    def test_agota_reintentos_y_propaga(self):
        """Si la base sigue caída tras los reintentos, el error se propaga
        (la app no debe arrancar sin base de datos)."""
        with mock.patch("app.database.engine") as motor, \
             mock.patch("app.database.time.sleep"):
            motor.connect.side_effect = _error_operacional()
            with self.assertRaises(sqlalchemy.exc.OperationalError):
                init_db()
        self.assertEqual(motor.connect.call_count, DB_REINTENTOS)

    def test_base_sana_no_reintenta(self):
        """Con la base en línea se crean las tablas al primer intento."""
        with mock.patch("app.database.engine") as motor, \
             mock.patch("app.database.time.sleep") as dormir, \
             mock.patch("app.database.Base.metadata.create_all") as crear:
            init_db()

        motor.connect.assert_called_once()
        crear.assert_called_once()
        dormir.assert_not_called()


if __name__ == "__main__":
    unittest.main()