"""Tests para el arranque resiliente: `init_db` con reconexión.

En Render, Postgres puede recién reiniciarse justo al desplegar y la primera
conexión SSL falla ("SSL connection has been closed unexpectedly") ->
`_crear_tablas_con_reintentos` reintenta con backoff hasta que la base responde.

Si tras agotar los reintentos sigue caída, **no** propaga: el arranque degrada.
Antes propagaba y uvicorn salía con status 3 -> Render reiniciaba el contenedor
en bucle y el servicio quedaba muerto sin que nadie lo viera (incidente del
2026-10-28, con ~2 h de bot mudo y 8 reinicios fallidos).
"""
import unittest
from unittest import mock

import sqlalchemy.exc

from app.database import DB_REINTENTOS, _migrar_esquema, init_db


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
            arranco = init_db()

        self.assertTrue(arranco)
        self.assertEqual(llamadas["n"], 3)
        crear.assert_called_once()
        dormir.assert_called()

    def test_agota_reintentos_y_degrada_sin_lanzar(self):
        """Si la base sigue caída tras los reintentos, NO se cae el arranque.

        Devuelve False y loguea el error para que /health lo reporte; si
        lanzara, uvicorn saldría con status 3 y Render entraría en bucle de
        reinicios con el bot mudo (bug de producción del 2026-10-28).
        """
        with mock.patch("app.database.engine") as motor, \
             mock.patch("app.database.time.sleep"), \
             self.assertLogs("app.database", level="ERROR") as logs:
            motor.connect.side_effect = _error_operacional()
            arranco = init_db()  # no debe lanzar

        self.assertFalse(arranco)
        self.assertEqual(motor.connect.call_count, DB_REINTENTOS)
        self.assertTrue(
            any("DEGRADADO" in linea for linea in logs.output),
            "debe quedar constancia en el log del arranque degradado",
        )

    def test_base_sana_no_reintenta(self):
        """Con la base en línea se crean las tablas al primer intento."""
        with mock.patch("app.database.engine") as motor, \
             mock.patch("app.database.time.sleep") as dormir, \
             mock.patch("app.database.Base.metadata.create_all") as crear:
            arranco = init_db()

        self.assertTrue(arranco)
        motor.connect.assert_called_once()
        crear.assert_called_once()
        dormir.assert_not_called()


class TestMigrarEsquemaCommitea(unittest.TestCase):
    """La migración debe persistir el DDL con commit explícito.

    En SQLite el DDL se autocomitea (por eso funcionaba local), pero en
    PostgreSQL los ALTER TABLE dentro de una transacción se revierten al cierre
    de la conexión si no se hace commit — lo que dejaba el dashboard admin sin
    ``es_bot``/``user_agent`` -> 500 al cargar.
    """

    def _conn_mock(self):
        return mock.MagicMock()

    def _inspector(self, con, tablas, columnas):
        insp = mock.MagicMock()
        insp.get_table_names.return_value = tablas
        insp.get_columns.return_value = [{"name": c} for c in columnas]
        return insp

    def test_agrega_columnas_faltantes_y_hace_commit(self):
        conn = self._conn_mock()
        insp = self._inspector(conn, ["visitas_landing"], ["id", "fuente"])
        with mock.patch("app.database.inspect", return_value=insp):
            _migrar_esquema(conn)
        alter = [c.args[0] for c in conn.execute.call_args_list]
        self.assertEqual(len(alter), 2)
        self.assertTrue(any("user_agent" in str(s) for s in alter))
        self.assertTrue(any("es_bot" in str(s) for s in alter))
        conn.commit.assert_called_once()

    def test_no_agrega_si_ya_existen(self):
        conn = self._conn_mock()
        insp = self._inspector(conn, ["visitas_landing"], ["id", "es_bot", "user_agent"])
        with mock.patch("app.database.inspect", return_value=insp):
            _migrar_esquema(conn)
        conn.execute.assert_not_called()
        conn.commit.assert_called_once()

    def test_error_en_inspect_no_propaga(self):
        conn = self._conn_mock()
        with mock.patch("app.database.inspect", side_effect=Exception("boom")):
            _migrar_esquema(conn)  # no debe lanzar
        conn.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()