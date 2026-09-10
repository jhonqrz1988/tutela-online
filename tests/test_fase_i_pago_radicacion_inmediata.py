"""Tests: la radicación arranca de inmediato al confirmarse el pago.

Cubre:
- Al confirmar el pago vía webhook de Mercado Pago (status approved) y estando
  en horario hábil, se programa la radicación inmediata en segundo plano.
- Fuera de horario hábil NO se fuerza: la tutela queda 'pago_confirmado' en
  cola para el scheduler.
- programar_radicacion_inmediata no duplica trabajo si ya hay una radicación
  en curso (por ejemplo, un scheduler que arrancó primero).
- El scheduler salta tutelas cuya radicación ya está en curso (evita abrir un
  segundo navegador mientras el bot radica).
"""
import json
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.database import Base, get_session
from app.main import app
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela
from app.models.user import User


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    return TestingSession()


def _crear_tutela_pagada(session, estado="esperando_pago", telefono="573009990002"):
    user = User(telefono=telefono, nombre="Lina Mora", consentimiento=True)
    session.add(user)
    session.flush()
    tutela = Tutela(user_id=user.id, tipo="salud", estado=estado, datos_json="{}")
    session.add(tutela)
    session.commit()
    return tutela.id, tutela


class TestProgramarRadicacionInmediata(unittest.TestCase):
    def test_programa_y_ejecuta_la_radicacion(self):
        """programar_radicacion_inmediata despacha la radicación al loop del bot."""
        from app.services import radicacion_service as svc

        session = _nueva_sesion()
        tutela_id, _ = _crear_tutela_pagada(session)
        # La tutela de un pago nuevo no tiene Radicacion todavía consultable
        # (el webhook crea la fila con estado 'pendiente'); sin fila → OK.
        llamado = {"id": None}
        llamadas = []

        def fake_despachar(tutela_id, **kwargs):
            llamado["id"] = tutela_id
            llamadas.append("despachar")
            return {"ok": True, "despachada": True}

        with mock.patch.object(svc, "SessionLocal", return_value=session), \
             mock.patch.object(svc, "despachar_radicacion", side_effect=fake_despachar):
            res = svc.programar_radicacion_inmediata(tutela_id)

        self.assertTrue(res.get("ok"), f"Debe programarse: {res}")
        self.assertEqual(llamado["id"], tutela_id, "Debe radicar ESTA tutela")

    def test_no_duplica_si_ya_hay_radicacion_en_curso(self):
        """Si la radicación ya arrancó (p.ej. el scheduler llegó primero) no abre otra."""
        from app.services import radicacion_service as svc

        session = _nueva_sesion()
        tutela_id, tutela = _crear_tutela_pagada(session, estado="pago_confirmado")
        session.add(Radicacion(tutela_id=tutela_id, estado="iniciando"))
        session.commit()
        llamado = {"veces": 0}

        def fake_despachar(tutela_id, **kwargs):
            llamado["veces"] += 1
            return {"ok": True, "despachada": True}

        with mock.patch.object(svc, "SessionLocal", return_value=session), \
             mock.patch.object(svc, "despachar_radicacion", side_effect=fake_despachar):
            res = svc.programar_radicacion_inmediata(tutela_id)

        self.assertFalse(res.get("ok"), f"No debe programarse con una radicación en curso: {res}")
        self.assertEqual(llamado["veces"], 0, "No debe arrancar un segundo navegador")

    def test_si_permitido_con_radicacion_esperando_codigo(self):
        """'esperando_codigo_email' es un estado retomable: se puede reprogramar."""
        from app.services import radicacion_service as svc

        session = _nueva_sesion()
        tutela_id, tutela = _crear_tutela_pagada(session, estado="esperando_codigo_email")
        session.add(Radicacion(tutela_id=tutela_id, estado="esperando_codigo_email"))
        session.commit()
        llamado = {"id": None}

        def fake_despachar(tutela_id, **kwargs):
            llamado["id"] = tutela_id
            return {"ok": True, "despachada": True}

        with mock.patch.object(svc, "SessionLocal", return_value=session), \
             mock.patch.object(svc, "despachar_radicacion", side_effect=fake_despachar):
            res = svc.programar_radicacion_inmediata(tutela_id)

        self.assertTrue(res.get("ok"), "Esperando código se puede reintentar")
        self.assertEqual(llamado["id"], tutela_id)


class TestWebhookPagoActivaRadicacion(unittest.TestCase):
    def _cliente(self, session):
        client = TestClient(app)
        client.__enter__()

        def _override_get_session():
            yield session

        app.dependency_overrides[get_session] = _override_get_session
        return client

    def _post_webhook(self, session, tutela_id, en_horario=True):
        from app.api import pagos as pagos_mod

        client = self._cliente(session)
        try:
            with mock.patch.object(pagos_mod, "verificar_firma", return_value=True), \
                 mock.patch.object(
                     pagos_mod, "consultar_pago",
                     new=mock.AsyncMock(return_value={"status": "approved", "external_reference": f"TUT-{tutela_id}"}),
                 ), \
                 mock.patch.object(pagos_mod, "enviar_texto"), \
                 mock.patch.object(pagos_mod, "es_horario_habil", return_value=en_horario), \
                 mock.patch.object(pagos_mod, "programar_radicacion_inmediata") as programar:
                resp = client.post(
                    "/webhook/mercadopago",
                    content=json.dumps({"type": "payment", "data": {"id": "123456789"}}),
                    headers={"x-signature": "ts=1,v1=firma", "Content-Type": "application/json"},
                )
        finally:
            app.dependency_overrides.pop(get_session, None)
            client.__exit__(None, None, None)
            client.close()
        return resp, programar

    def test_pago_aprobado_en_horario_programa_radicacion(self):
        session = _nueva_sesion()
        tutela_id, _ = _crear_tutela_pagada(session, estado="esperando_pago")

        resp, programar = self._post_webhook(session, tutela_id, en_horario=True)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(programar.call_count, 1, "Debe programar la radicación inmediata")
        self.assertEqual(programar.call_args[0][0], tutela_id, "Debe radicar la tutela pagada")

        tutela = session.get(Tutela, tutela_id)
        self.assertEqual(tutela.estado, "pago_confirmado")
        rad = session.execute(
            __import__("sqlalchemy").select(Radicacion).where(Radicacion.tutela_id == tutela_id)
        ).scalar_one_or_none()
        self.assertIsNotNone(rad, "El webhook debe crear el registro de radicación")

    def test_pago_aprobado_fuera_de_horario_queda_en_cola(self):
        """Fuera de horario no se fuerza el bot: la cola del scheduler lo tomará."""
        session = _nueva_sesion()
        tutela_id, _ = _crear_tutela_pagada(session, estado="esperando_pago")

        resp, programar = self._post_webhook(session, tutela_id, en_horario=False)

        self.assertEqual(resp.status_code, 200)
        programar.assert_not_called()
        tutela = session.get(Tutela, tutela_id)
        self.assertEqual(tutela.estado, "pago_confirmado",
                         "Sin horario hábil queda en cola para el scheduler")


class TestSchedulerSaltaTutelaEnCurso(unittest.TestCase):
    def test_no_radica_dos_veces_la_misma_tutela(self):
        """Si ya hay radicación en curso, el scheduler no abre un segundo navegador."""
        from app.tasks import jobs as jobs_mod

        session = _nueva_sesion()
        tutela_id, tutela = _crear_tutela_pagada(session, estado="pago_confirmado")
        session.add(Radicacion(tutela_id=tutela_id, estado="iniciando"))
        session.commit()
        procesadas = []

        def fake_despachar(tutela_id, **kwargs):
            procesadas.append(tutela_id)
            return {"ok": True, "despachada": True}

        with mock.patch.object(jobs_mod, "es_horario_habil", return_value=True), \
             mock.patch.object(jobs_mod, "SessionLocal", return_value=session), \
             mock.patch.object(jobs_mod, "despachar_radicacion", side_effect=fake_despachar):
            jobs_mod.procesar_cola_radicacion()

        self.assertEqual(procesadas, [], "No debe reprocesar una tutela con radicación en curso")


if __name__ == "__main__":
    unittest.main()