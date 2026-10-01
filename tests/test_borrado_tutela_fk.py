"""Borrar una tutela no debe chocar con las tablas que la referencian.

Bug reportado en produccion: el comando SALIR no hacia nada y el bot seguia
pidiendo el mismo dato, una y otra vez. Tambien se Rejo que el boton
"Desbloquear" no lo sacaba de la trampa.

CAUSA: ``tutelas.id`` es referenciado por cinco columnas y NINGUNA tiene
``ondelete``:

    cita_pendientes.tutela_id     (se borraba a mano)
    radicaciones.tutela_id        (se borraba con _borrar_radicaciones)
    pasos_radicacion.radicacion_id(idem, en cascada)
    mensajes_whatsapp.tutela_id   <-- NUNCA se liberaba
    envios_whatsapp.tutela_id     <-- NUNCA se liberaba

En PostgreSQL (produccion) eso es un ForeignKeyViolation: el DELETE de la tutela
explota, la transaccion se revierte entera y el reinicio no ocurre. El sintoma
es "no paso nada": el mensaje del usuario si se guarda, pero el estado no cambia.

PEOR: los mensajes del usuario (envio_estado) se perdian para siempre. El
reporte de entrega no podia explicar por que un numero no avanzo.

Por que los tests no lo detectaban: SQLite trae ``PRAGMA foreign_keys`` apagado
por defecto, asi que en la suite los DELETEdangueaban en silencio. Estos tests
lo encienden explicitamente con un event listener.
"""
import asyncio
import unittest
from unittest import mock

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import webhook_whatsapp
from app.database import Base
from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import EnvioWhatsApp, MensajeWhatsApp
from app.services import seguimiento_service


def _nueva_sesion_con_fk():
    """Como la de produccion: FKs ENCENDIDAS (PostgreSQL las tiene siempre)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _activar_fks(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _poblar_con_envios(session, telefono):
    """Usuario + tutela en curso + historial de mensajes y envios que la apuntan.

    Esto es lo que produce el bot en cuanto alguien habla: cada respuesta que
    manda queda con tutela_id, y cada mensaje entrante tambien.
    """
    user = User(telefono=telefono, estado="activo", consentimiento=True)
    session.add(user)
    session.commit()
    tutela = Tutela(
        user_id=user.id, tipo="salud", estado="recogiendo_datos",
        datos_json='{"tipo":"salud","_step":3}',
    )
    session.add(tutela)
    session.commit()
    session.add(MensajeWhatsApp(
        from_number=telefono, body="hola", tipo_mensaje="texto", tutela_id=tutela.id,
        envio_estado="entregado",
    ))
    session.add(EnvioWhatsApp(
        wamid="wamid-PRUEBA-1", from_number=telefono, tutela_id=tutela.id, estado="entregado",
    ))
    session.commit()
    return user, tutela


class TestReiniciarFlujoConFK(unittest.TestCase):
    def setUp(self):
        self.session = _nueva_sesion_con_fk()
        p1 = mock.patch.object(webhook_whatsapp, "enviar_texto", return_value=True)
        p2 = mock.patch.object(webhook_whatsapp, "enviar_botones", return_value=True)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def _enviar(self, telefono, texto):
        return asyncio.run(
            webhook_whatsapp.procesar_mensaje(
                self.session, telefono, texto, 0, "", False,
            )
        )

    def test_salir_reinicia_aunque_haya_mensajes_y_envios(self):
        """REPRODUCE EL BUG: antes esto reventaba con ForeignKeyViolation."""
        _poblar_con_envios(self.session, "573000000001")

        self._enviar("573000000001", "salir")

        # El flujo se reinicio de verdad: no queda ninguna tutela.
        self.assertEqual(
            self.session.execute(select(Tutela)).scalars().all(), [],
            "la tutela debe quedar borrada",
        )

    def test_salir_conserva_el_historial_para_el_reporte(self):
        """Los mensajes no se borran (se necesita saber por que no avanzo)."""
        _poblar_con_envios(self.session, "573000000002")
        self._enviar("573000000002", "salir")

        mensajes = self.session.execute(
            select(MensajeWhatsApp).order_by(MensajeWhatsApp.id)
        ).scalars().all()
        # El "hola" original + el propio "salir" que acaba de llegar.
        self.assertEqual(len(mensajes), 2, "los mensajes del usuario deben sobrevivir")
        self.assertEqual(mensajes[0].body, "hola")
        self.assertEqual(mensajes[1].body, "salir")
        # Y quedan desanclados para no romper el borrado de la tutela.
        for mensaje in mensajes:
            self.assertIsNone(mensaje.tutela_id)

    def test_salir_conserva_los_envios_para_medir_entrega(self):
        """envios_whatsapp es el unico rastro de si Meta entrego el mensaje."""
        _poblar_con_envios(self.session, "573000000003")
        self._enviar("573000000003", "SALIR")

        envios = self.session.execute(select(EnvioWhatsApp)).scalars().all()
        self.assertEqual(len(envios), 1, "el envio no se debe perder")
        self.assertEqual(envios[0].estado, "entregado")
        self.assertIsNone(envios[0].tutela_id, "debe quedar desanclado")

    def test_salir_responde_al_usuario(self):
        """Si reventara la excepcion, tampoco habria respuesta."""
        _poblar_con_envios(self.session, "573000000004")
        resultado = self._enviar("573000000004", "salir")
        unido = " ".join(resultado["respuestas"])
        self.assertIn("reiniciado", unido.lower())

    def test_salir_con_tildes_y_puntuacion_tambien(self):
        _poblar_con_envios(self.session, "573000000005")
        self._enviar("573000000005", "  Sálir.  ")
        self.assertEqual(self.session.execute(select(Tutela)).scalars().all(), [])

    def test_no_toca_las_tutelas_de_otros(self):
        _poblar_con_envios(self.session, "573000000006")
        otro = User(telefono="573000000007", estado="activo", consentimiento=True)
        self.session.add(otro)
        self.session.commit()
        self.session.add(Tutela(user_id=otro.id, tipo="salud", estado="narracion", datos_json="{}"))
        self.session.commit()

        self._enviar("573000000006", "salir")

        restantes = self.session.execute(select(Tutela)).scalars().all()
        self.assertEqual(len(restantes), 1, "solo se borra la del que salio")
        self.assertEqual(restantes[0].user_id, otro.id)


class TestDesbloquearConFK(unittest.TestCase):
    def setUp(self):
        self.session = _nueva_sesion_con_fk()

    def test_desbloquear_tambien_reventaba(self):
        """El boton del panel sufria el mismo FK violation."""
        user, tutela = _poblar_con_envios(self.session, "573000000010")

        resultado = seguimiento_service.desbloquear_usuario(self.session, user.id)

        self.assertTrue(resultado["ok"], f"no deberia fallar: {resultado.get('error')}")
        self.assertEqual(self.session.execute(select(Tutela)).scalars().all(), [])
        # El historial del reporte se conserva.
        self.assertEqual(
            len(self.session.execute(select(MensajeWhatsApp)).scalars().all()), 1
        )
        self.assertEqual(
            len(self.session.execute(select(EnvioWhatsApp)).scalars().all()), 1
        )

    def test_desbloquear_preserva_el_envio_para_medir_entrega(self):
        user, _ = _poblar_con_envios(self.session, "573000000011")
        seguimiento_service.desbloquear_usuario(self.session, user.id)
        envios = self.session.execute(select(EnvioWhatsApp)).scalars().all()
        self.assertIsNone(envios[0].tutela_id)


if __name__ == "__main__":
    unittest.main()