"""Personas atrapadas en el flujo y desbloqueo manual.

Contexto: con el comando SALIR ya arreglado (commit 99236ca), los usuarios que
quedaron atrapados ANTES del fix siguen bloqueados: su bot quedó pidiendo un dato
que ya no pueden responder, y la única forma de desbloquearlos es reiniciar su
flujo manualmente desde el panel.

"Atrapado" = tiene una tutela en curso, NO llegó a un estado terminal (radicada /
completado / fallida) y su última interacción fue hace más de N horas (ya está
fuera de la ventana de 24 h de Meta, así que un mensaje normal ni siquiera
llegaría). Estos son los que necesitan intervención.
"""
import datetime
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.tutela import Tutela
from app.models.user import User
from app.services import seguimiento_service

AHORA = datetime.datetime.utcnow()


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


class TestListarAtrapados(unittest.TestCase):
    def setUp(self):
        self.session = _nueva_sesion()

    def _crear(self, telefono, estado_tutela="recogiendo_datos", horas=48):
        user = User(telefono=telefono, estado="activo", consentimiento=True)
        self.session.add(user)
        self.session.commit()
        tutela = Tutela(
            user_id=user.id, tipo="salud", estado=estado_tutela,
            datos_json="{}",
            created_at=AHORA - datetime.timedelta(hours=horas),
        )
        self.session.add(tutela)
        self.session.commit()
        return user, tutela

    def test_detecta_usuario_atrapado(self):
        self._crear("573000000001", "recogiendo_datos", horas=48)
        atrapados = seguimiento_service.listar_atrapados(self.session, horas_inactivo=24)
        self.assertEqual(len(atrapados), 1)
        self.assertEqual(atrapados[0]["telefono"], "573000000001")
        self.assertEqual(atrapados[0]["estado"], "recogiendo_datos")

    def test_no_marca_usuario_reciente(self):
        """Si acaba de escribir, está activo: no es un atrapado."""
        self._crear("573000000002", "recogiendo_datos", horas=2)
        atrapados = seguimiento_service.listar_atrapados(self.session, horas_inactivo=24)
        self.assertEqual(atrapados, [])

    def test_no_marca_estados_terminales(self):
        """Radicada/completado/fallida NO están atrapados: el proceso terminó."""
        for i, estado in enumerate(["radicada", "completado", "fallida"]):
            self._crear(f"5731111111{i}", estado, horas=48)
        atrapados = seguimiento_service.listar_atrapados(self.session, horas_inactivo=24)
        self.assertEqual(atrapados, [])

    def test_ordena_por_mas_inactivo_primero(self):
        self._crear("573000000010", "recogiendo_datos", horas=24)
        self._crear("573000000011", "narracion", horas=72)
        self._crear("573000000012", "preguntas_clinicas", horas=48)
        atrapados = seguimiento_service.listar_atrapados(self.session, horas_inactivo=24)
        self.assertEqual(atrapados[0]["telefono"], "573000000011")
        self.assertEqual(atrapados[-1]["telefono"], "573000000010")

    def test_ultima_actividad_usa_el_mensaje_mas_reciente(self):
        """Un usuario que escribió hace 1h no está atrapado aunque su tutela sea vieja."""
        from app.models.whatsapp import MensajeWhatsApp

        user, _ = self._crear("573000000020", "recogiendo_datos", horas=100)
        # Mensaje muy reciente (ayer no, hace 1 hora).
        self.session.add(MensajeWhatsApp(
            from_number="573000000020", body="hola", tipo_mensaje="texto",
            created_at=AHORA - datetime.timedelta(hours=1),
        ))
        self.session.commit()

        atrapados = seguimiento_service.listar_atrapados(self.session, horas_inactivo=24)
        self.assertEqual(atrapados, [], "escribió hace 1h: no está atrapado")


class TestDesbloquear(unittest.TestCase):
    def setUp(self):
        self.session = _nueva_sesion()

    def _crear_atrapado(self):
        user = User(telefono="573000000030", estado="activo", consentimiento=True)
        self.session.add(user)
        self.session.commit()
        tutela = Tutela(
            user_id=user.id, tipo="salud", estado="recogiendo_datos",
            datos_json='{"tipo":"salud","_step":2}',
        )
        self.session.add(tutela)
        self.session.commit()
        return user, tutela

    def test_desbloquear_reinicia_el_flujo(self):
        user, tutela = self._crear_atrapado()
        resultado = seguimiento_service.desbloquear_usuario(self.session, user.id)

        self.assertTrue(resultado["ok"])
        # Tutela borrada.
        restantes = self.session.execute(select(Tutela)).scalars().all()
        self.assertEqual(restantes, [], "debe borrar la tutela en curso")
        # Usuario devuelto a nuevo.
        user_reload = self.session.execute(
            select(User).where(User.id == user.id)
        ).scalar_one()
        self.assertEqual(user_reload.estado, "nuevo")
        self.assertFalse(user_reload.consentimiento)

    def test_desbloquear_es_idempotente(self):
        """Desbloquear dos veces no debe fallar."""
        user, _ = self._crear_atrapado()
        seguimiento_service.desbloquear_usuario(self.session, user.id)
        segundo = seguimiento_service.desbloquear_usuario(self.session, user.id)
        self.assertTrue(segundo["ok"])

    def test_desbloquear_no_existe(self):
        resultado = seguimiento_service.desbloquear_usuario(self.session, 999999)
        self.assertFalse(resultado["ok"])

    def test_desbloquear_preserva_otros_usuarios(self):
        from app.models.user import User as U

        _, tutela1 = self._crear_atrapado()
        user2 = U(telefono="573000000031", estado="activo", consentimiento=True)
        self.session.add(user2)
        self.session.commit()
        tutela2 = Tutela(user_id=user2.id, tipo="salud", estado="narracion", datos_json="{}")
        self.session.add(tutela2)
        self.session.commit()

        seguimiento_service.desbloquear_usuario(self.session, tutela1.user_id)

        # Solo se borró la tutela del usuario 1.
        restantes = self.session.execute(select(Tutela)).scalars().all()
        self.assertEqual(len(restantes), 1)
        self.assertEqual(restantes[0].user_id, user2.id)


if __name__ == "__main__":
    unittest.main()