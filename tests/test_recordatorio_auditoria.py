"""Auditoría de recordatorios: ¿a quién se le mandó más de una vez y por qué?

Sirve para responder, sobre datos reales, la pregunta "¿el job automático llegó
a mandar un tercer recordatorio?".

La clave está en ``origen``. El código anterior marcaba los envíos manuales con
``recordatorio_estado = "prueba_manual"``, un valor que nunca es un estado real
de tutela. Ese valor es la huella forense de los envíos hechos desde el panel.
El código arreglado ya no lo escribe, así que las filas que lo tienen son
históricas.

El job automático no puede pasar de 2 por silencio (4 h, luego 14 h; a las 24 h
ya se sale de la ventana de Meta), así que "3 veces" exige al menos un envío
manual.
"""
import contextlib
import datetime
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database
from app.database import Base
from app.main import app
from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import MensajeWhatsApp

AHORA = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


@contextlib.contextmanager
def _db_temporal():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    original = database.SessionLocal
    database.SessionLocal = maker
    try:
        yield maker
    finally:
        database.SessionLocal = original
        engine.dispose()


def _alta(session, telefono, marca, estado_tutela="recogiendo_datos", horas_silencio=2.0):
    user = User(telefono=telefono, estado="activo", consentimiento=True)
    if marca is not None:
        user.recordatorio_estado = marca
        user.recordatorio_enviado_at = AHORA
    session.add(user)
    session.commit()
    tutela = Tutela(
        user_id=user.id, tipo="salud", estado=estado_tutela,
        datos_json="{}", created_at=AHORA - datetime.timedelta(days=1),
    )
    session.add(tutela)
    session.commit()
    if horas_silencio is not None:
        session.add(MensajeWhatsApp(
            from_number=telefono, body="hola", es_recibido=True,
            created_at=AHORA - datetime.timedelta(hours=horas_silencio),
        ))
        session.commit()
    return user, tutela


def _cliente_admin():
    from app.api.admin import SESSION_COOKIE, _crear_sesion
    return TestClient(app), {SESSION_COOKIE: _crear_sesion()}


class TestAuditoriaDeRecordatorios(unittest.TestCase):
    def setUp(self):
        self._ctx = _db_temporal()
        self.maker = self._ctx.__enter__()
        self.session = self.maker()
        self.client, self.cookies = _cliente_admin()

    def tearDown(self):
        self.session.close()
        self._ctx.__exit__(None, None, None)

    def _auditar(self):
        r = self.client.get(
            "/admin/api/recordatorios/auditoria", cookies=self.cookies
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_requiere_login(self):
        r = TestClient(app).get("/admin/api/recordatorios/auditoria")
        self.assertIn(r.status_code, (401, 303), "no puede ser público")

    def test_detecta_el_envio_manual_por_la_marca_heredada(self):
        """'prueba_manual' no es un estado real: delata el clic del panel."""
        _alta(self.session, "573000000001", "prueba_manual")
        datos = self._auditar()
        fila = datos["filas"][0]
        self.assertEqual(fila["origen"], "manual")
        self.assertEqual(datos["resumen"]["manuales"], 1)

    def test_un_envio_automatico_se_marca_con_el_estado_real(self):
        _alta(self.session, "573000000002", "recogiendo_datos")
        datos = self._auditar()
        self.assertEqual(datos["filas"][0]["origen"], "automatico")
        self.assertEqual(datos["resumen"]["automaticos"], 1)

    def test_techo_de_automaticos_por_silencio(self):
        """Silencio corto -> 0; largo -> hasta 2. Nunca 3."""
        # 2 h de silencio: fuera de la ventana de 4 h
        _alta(self.session, "573000000003", "prueba_manual", horas_silencio=2.0)
        # 6 h: alcanza 1 automático
        _alta(self.session, "573000000004", "prueba_manual", horas_silencio=6.0)
        # 20 h: alcanza el techo de 2
        _alta(self.session, "573000000005", "prueba_manual", horas_silencio=20.0)
        # 40 h: ya fuera de la ventana, no cabe ni uno más
        _alta(self.session, "573000000006", "prueba_manual", horas_silencio=40.0)

        datos = self._auditar()
        techos = {f["telefono"]: f["automatico_techo"] for f in datos["filas"]}
        self.assertEqual(techos["573000000003"], 0)
        self.assertEqual(techos["573000000004"], 1)
        self.assertEqual(techos["573000000005"], 2)
        self.assertEqual(techos["573000000006"], 2)
        self.assertLessEqual(max(techos.values()), 2)

    def test_senala_riesgo_de_tercero_solo_si_hubo_manual(self):
        """El riesgo real: un manual + al menos un automático."""
        # Manual, y el silencio daba para 2 automáticos: aquí pudo llegar a 3.
        _alta(self.session, "573000000007", "prueba_manual", horas_silencio=20.0)
        # Manual hace nada: aunque quedara un auto pendiente, no se solaparon.
        _alta(self.session, "573000000008", "prueba_manual", horas_silencio=0.5)
        # Solo automático: nunca puede llegar a 3.
        _alta(self.session, "573000000009", "recogiendo_datos", horas_silencio=20.0)

        datos = self._auditar()
        por_tel = {f["telefono"]: f for f in datos["filas"]}
        self.assertTrue(por_tel["573000000007"]["riesgo_ternero"])
        self.assertFalse(por_tel["573000000008"]["riesgo_ternero"])
        self.assertFalse(por_tel["573000000009"]["riesgo_ternero"])
        self.assertEqual(datos["resumen"]["riesgo_ternero"], 1)

    def test_no_devuelve_usuarios_sin_recordatorio(self):
        _alta(self.session, "573000000010", None)
        datos = self._auditar()
        self.assertEqual(datos["filas"], [])
        self.assertEqual(datos["resumen"]["marcados"], 0)


if __name__ == "__main__":
    unittest.main()
