"""Un recordatorio no debe repetirse por error (y debe quedar registrado).

SINTOMA REPORTADO: a algunos usuarios les llego 3 veces el mensaje de "puedes
continuar tu tutela".

El job automatico NO puede hacer eso por si solo: la ventana es de 4 a 24 h de
silencio con enfriamiento de 10 h por estado, asi que como maximo manda 2
mensajes mientras el estado no cambie (a las ~4 h y a las ~14 h; a las 24 h ya
esta fuera de la ventana de Meta).

La causa era el envio MANUAL (el boton "Enviar recordatorio" y la caja de
prueba del panel):

1. MARCA INVALIDA: el envio manual marcaba ``recordatorio_estado =
   "prueba_manual"``, un valor que nunca es igual al estado real de una tutela.
   Como el enfriamiento compara ``recordatorio_estado == tutela.estado``, NUNCA
   coincidia: ni frenaba un segundo clic ni blindaba al usuario frente al job
   automatico. Manual + automatico = 3 mensajes.

2. SIN CANDADO DE SERVIDOR: cada clic enviaba. El boton se deshabilita en el
   navegador, pero el servidor aceptaba cuantos POSTES llegaran.

3. SIN WAMID: los envios iniciados desde el panel no quedaban en
   ``envios_whatsapp`` (el accumulate ``_WAMIDS`` solo se vuelca a la base dentro
   del webhook). Por eso no aparecian en el reporte de entrega y era imposible
   saber cuantos se habian mandado desde el panel.
"""
import contextlib
import datetime
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import EnvioWhatsApp
from app.services import recordatorio_service as rs

AHORA = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def _sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _alta(session, telefono, estado="recogiendo_datos"):
    user = User(telefono=telefono, estado="activo", consentimiento=True)
    session.add(user)
    session.commit()
    tutela = Tutela(user_id=user.id, tipo="salud", estado=estado, datos_json="{}")
    session.add(tutela)
    session.commit()
    return user, tutela


class TestElAutomaticoNoSeRepiteTresVeces(unittest.TestCase):
    """Techo matematico del job: 2 envios por silencio, no 3."""

    def setUp(self):
        self.session = _sesion()

    @patch("app.services.whatsapp_service.enviar_botones", return_value=True)
    def test_como_maximo_dos_antes_de_quedarse_sin_ventana(self, mock):
        user, _ = _alta(self.session, "573000000001")
        enviados = 0
        # Simula el paso del tiempo: el job corre cada hora durante 24 h.
        for hora in range(1, 25):
            with patch.object(rs, "_utc_naive", return_value=AHORA + datetime.timedelta(hours=hora)):
                r = rs.enviar_recordatorios(self.session, horas=4, ventana_horas=24)
            enviados += r["enviados"]
        self.assertLessEqual(enviados, 2, "el job automatico no debe pasar de 2 mensajes")

    @patch("app.services.whatsapp_service.enviar_botones", return_value=True)
    def test_manual_marca_el_estado_real_y_blinda_al_job(self, mock):
        """El fallo principal: 'prueba_manual' nunca coincidia con el estado."""
        user, tutela = _alta(self.session, "573000000002")

        rs.enviar_recordatorio(self.session, user.telefono, "prueba_manual")

        user_reload = self.session.get(User, user.id)
        self.assertEqual(
            user_reload.recordatorio_estado, tutela.estado,
            "debe quedar el estado real para que el enfriamiento compare bien",
        )

    @patch("app.services.whatsapp_service.enviar_botones", return_value=True)
    def test_manual_dentro_del_enfriamiento_no_reenvia(self, mock):
        user, _ = _alta(self.session, "573000000003")
        rs.enviar_recordatorio(self.session, user.telefono, "prueba_manual")
        segunda = rs.enviar_recordatorio(self.session, user.telefono, "prueba_manual")
        self.assertFalse(segunda, "no se puede reenviar dentro del enfriamiento")
        self.assertEqual(mock.call_count, 1)

    @patch("app.services.whatsapp_service.enviar_botones", return_value=True)
    def test_manual_con_fuerza_si_reenvia(self, mock):
        """El dueño puede forzar (pruebas), pero tiene que pedirlo explícito."""
        user, _ = _alta(self.session, "573000000004")
        rs.enviar_recordatorio(self.session, user.telefono, "prueba_manual")
        forzada = rs.enviar_recordatorio(
            self.session, user.telefono, "prueba_manual", forzar=True
        )
        self.assertTrue(forzada)
        self.assertEqual(mock.call_count, 2)

    @patch("app.services.whatsapp_service.enviar_botones", return_value=True)
    def test_el_job_no_reenvia_despues_de_un_envio_manual(self, mock):
        """Manual + automatico ya no se apilan."""
        user, _ = _alta(self.session, "573000000005")
        rs.enviar_recordatorio(self.session, user.telefono, "prueba_manual")
        resultado = rs.enviar_recordatorios(self.session, horas=4, enfriamiento_horas=10)
        self.assertEqual(resultado["enviados"], 0, "el manual ya conto como aviso")


class TestRegistroDeWamid(unittest.TestCase):
    """Los envios del panel también deben verse en el reporte de entrega."""

    def setUp(self):
        self.session = _sesion()

    @patch("app.services.whatsapp_service.enviar_botones", return_value=True)
    def test_si_falla_el_wamid_no_se_pierde_el_enfriamiento(self, mock):
        """La contabilidad del wamid es best-effort, pero la marca no.

        Si el registro del wamid revienta y se pierde la marca, el usuario vuelve
        a ser candidato y le llega OTRO recordatorio: exactamente el bug.
        """
        user, tutela = _alta(self.session, "573000000010")
        with patch(
            "app.services.recordatorio_service._registrar_envio",
            side_effect=RuntimeError("bd caida"),
        ):
            ok = rs.enviar_recordatorio(self.session, user.telefono, "prueba_manual")

        self.assertTrue(ok, "el mensaje sí salió")
        user_reload = self.session.get(User, user.id)
        self.assertIsNotNone(user_reload.recordatorio_enviado_at)
        self.assertEqual(user_reload.recordatorio_estado, tutela.estado)
        # Y no debe poder repetirse justo después.
        segunda = rs.enviar_recordatorio(self.session, user.telefono, "prueba_manual")
        self.assertFalse(segunda)

    @patch("app.services.whatsapp_service.enviar_botones", return_value=True)
    def test_guarda_el_wamid_devuelto_por_meta(self, mock):
        user, tutela = _alta(self.session, "573000000011")

        def _falso(correo, texto, botones):
            # Simula que Meta devuelve un wamid y que el accumulate lo captura.
            from app.services import whatsapp_service as ws

            ws._notificar_envio_aceptado({"messages": [{"id": "wamid.TEST-1"}]})
            return True

        mock.side_effect = _falso
        with contextlib.ExitStack():
            rs.enviar_recordatorio(self.session, user.telefono, "prueba_manual")

        envios = self.session.execute(select(EnvioWhatsApp)).scalars().all()
        self.assertEqual(len(envios), 1, "el envio del panel debe quedar registrado")
        self.assertEqual(envios[0].wamid, "wamid.TEST-1")
        self.assertEqual(envios[0].from_number, user.telefono)
        self.assertEqual(envios[0].tutela_id, tutela.id)

    @patch("app.services.whatsapp_service.enviar_botones", return_value=False)
    def test_no_registra_si_el_proveedor_rechaza(self, mock):
        user, _ = _alta(self.session, "573000000012")
        ok = rs.enviar_recordatorio(self.session, user.telefono, "prueba_manual")
        self.assertFalse(ok)
        self.assertEqual(self.session.execute(select(EnvioWhatsApp)).scalars().all(), [])


if __name__ == "__main__":
    unittest.main()