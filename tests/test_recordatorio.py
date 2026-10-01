"""Recordatorios a usuarios a la espera dentro de la ventana de 24 h de Meta."""
import contextlib
import datetime
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import MensajeWhatsApp
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


def _alta(session, telefono, estado="recogiendo_datos", consentimiento=True,
          silencio_horas=0, **kwargs):
    """Crea usuario + tutela abierta y mensajes para simular 'silencio'."""
    user = User(
        telefono=telefono, estado="activo", consentimiento=consentimiento, **kwargs
    )
    session.add(user)
    session.commit()
    tutela = Tutela(
        user_id=user.id, tipo="salud", estado=estado, datos_json="{}",
        created_at=AHORA - datetime.timedelta(hours=silencio_horas + 1),
    )
    session.add(tutela)
    session.commit()
    if silencio_horas:
        session.add(MensajeWhatsApp(
            from_number=telefono, body="hola", tipo_mensaje="texto",
            created_at=AHORA - datetime.timedelta(hours=silencio_horas),
        ))
    return user, tutela


class TestCandidatos(unittest.TestCase):
    def setUp(self):
        self.session = _sesion()

    def test_el_que_lleva_4h_parado_es_candidato(self):
        _alta(self.session, "573000000001", silencio_horas=5)
        cand = rs.candidatos(self.session, horas=4)
        self.assertEqual([c["telefono"] for c in cand], ["573000000001"])

    def test_el_que_acaba_de_escribir_no(self):
        _alta(self.session, "573000000002", silencio_horas=1)
        self.assertEqual(rs.candidatos(self.session, horas=4), [])

    def test_fuera_de_la_ventana_de_meta_no_se_manda(self):
        """Pasadas 24 h sin escribir, Meta no entrega un mensaje normal."""
        _alta(self.session, "573000000003", silencio_horas=30)
        self.assertEqual(rs.candidatos(self.session, horas=4, ventana_horas=24), [])

    def test_no_manda_a_quien_no_consintio(self):
        _alta(self.session, "573000000004", consentimiento=False, silencio_horas=5)
        self.assertEqual(rs.candidatos(self.session, horas=4), [])

    def test_no_manda_a_quien_pidio_detener(self):
        _alta(self.session, "573000000005", silencio_horas=5, no_mensajes_proactivos=True)
        self.assertEqual(rs.candidatos(self.session, horas=4), [])

    def test_no_manda_si_la_tutela_ya_termino(self):
        for i, estado in enumerate(["radicada", "completado", "fallida"]):
            _alta(self.session, f"5731111111{i}", estado=estado, silencio_horas=5)
        self.assertEqual(rs.candidatos(self.session, horas=4), [])

    def test_enfriamiento_no_repite_mismo_estado(self):
        user, _ = _alta(self.session, "573000000006", silencio_horas=5)
        user.recordatorio_enviado_at = AHORA - datetime.timedelta(hours=1)
        user.recordatorio_estado = "recogiendo_datos"
        self.session.commit()
        # Solo 1h desde el recordatorio: enfriamiento de 10h, no repetir.
        self.assertEqual(rs.candidatos(self.session, horas=4, enfriamiento_horas=10), [])

    def test_si_cambio_de_estado_vuelve_a_ser_candidato(self):
        """Avanzó en el flujo: es acreedor de un nuevo recordatorio."""
        user, tutela = _alta(self.session, "573000000007", silencio_horas=5)
        user.recordatorio_enviado_at = AHORA - datetime.timedelta(hours=1)
        user.recordatorio_estado = "recogiendo_datos"
        tutela.estado = "narracion"
        self.session.commit()
        cand = rs.candidatos(self.session, horas=4, enfriamiento_horas=10)
        self.assertEqual([c["estado"] for c in cand], ["narracion"])

    def test_ordenado_por_mas_silencio(self):
        _alta(self.session, "573000000010", silencio_horas=5)
        _alta(self.session, "573000000011", silencio_horas=12)
        _alta(self.session, "573000000012", silencio_horas=8)
        cand = rs.candidatos(self.session, horas=4)
        self.assertEqual(
            [c["telefono"] for c in cand],
            ["573000000011", "573000000012", "573000000010"],
        )


class TestEnvio(unittest.TestCase):
    def setUp(self):
        self.session = _sesion()

    @patch("app.services.whatsapp_service.enviar_botones", return_value=True)
    def test_envia_con_el_boton_continuar(self, mock_botones):
        _alta(self.session, "573000000020", silencio_horas=5)
        resultado = rs.enviar_recordatorios(self.session, horas=4)
        self.assertEqual(resultado["enviados"], 1)
        mock_botones.assert_called_once()
        args = mock_botones.call_args[0]
        self.assertEqual(args[0], "573000000020")
        self.assertEqual(args[1], rs.TEXTO_RECORDATORIO)
        self.assertEqual(args[2], [("continuar", "Continuar mi tutela")])

    @patch("app.services.whatsapp_service.enviar_botones", return_value=True)
    def test_marca_el_envio_para_no_repetir(self, mock_botones):
        user, _ = _alta(self.session, "573000000021", silencio_horas=5)
        rs.enviar_recordatorios(self.session, horas=4)
        user_reload = self.session.get(User, user.id)
        self.assertIsNotNone(user_reload.recordatorio_enviado_at)
        self.assertEqual(user_reload.recordatorio_estado, "recogiendo_datos")
        # Segunda corrida: ya no es candidato.
        segunda = rs.enviar_recordatorios(self.session, horas=4)
        self.assertEqual(segunda["enviados"], 0)
        self.assertEqual(mock_botones.call_count, 1)

    @patch("app.services.whatsapp_service.enviar_botones", return_value=False)
    def test_si_falla_el_envio_no_marca(self, mock_botones):
        user, _ = _alta(self.session, "573000000022", silencio_horas=5)
        resultado = rs.enviar_recordatorios(self.session, horas=4)
        self.assertEqual(resultado["fallidos"], 1)
        user_reload = self.session.get(User, user.id)
        self.assertIsNone(user_reload.recordatorio_enviado_at, "no debe marcar si no se envió")

    @patch("app.services.whatsapp_service.enviar_botones", return_value=True)
    def test_respeta_el_tope_por_corrida(self, mock_botones):
        for i in range(5):
            _alta(self.session, f"5730000000{i}3", silencio_horas=5)
        resultado = rs.enviar_recordatorios(self.session, horas=4, maximo=2)
        self.assertEqual(resultado["enviados"], 2)

    @patch("app.services.whatsapp_service.enviar_botones", return_value=True)
    def test_un_fallo_no_corta_la_corrida(self, mock_botones):
        mock_botones.side_effect = [True, RuntimeError("boom"), True]
        for i in range(3):
            _alta(self.session, f"5730000000{i}4", silencio_horas=5)
        resultado = rs.enviar_recordatorios(self.session, horas=4)
        self.assertEqual(resultado["enviados"] + resultado["fallidos"], 3)


class TestTexto(unittest.TestCase):
    def test_texto_aprobado(self):
        self.assertEqual(
            rs.TEXTO_RECORDATORIO,
            "Hola \U0001F44B Tu solicitud de TutelApp qued\u00f3 a la espera de un dato. "
            "Si quieres retomarla, escr\u00edbenos por aqu\u00ed. Seguimos ayud\u00e1ndote con tu tutela.",
        )

    def test_el_boton_cabe_en_20_caracteres(self):
        self.assertLessEqual(len(rs.BOTON_CONTINUAR[1]), 20)

    def test_el_recordatorio_no_promete_precios(self):
        """El texto no lleva oferta comercial: si un día se manda fuera de la
        ventana de 24 h habr\u00eda que ser plantilla de marketing, que Meta
        rechaza. As\u00ed el texto se mantiene solo."""
        texto = (rs.TEXTO_RECORDATORIO + rs.BOTON_CONTINUAR[1]).lower()
        for prohibido in ("29.000", "29000", "$", "radicamos por ti", "gratis"):
            self.assertNotIn(prohibido, texto)


class TestJob(unittest.TestCase):
    """El job del scheduler no debe romper nunca el ciclo."""

    @patch("app.services.whatsapp_service.enviar_botones", return_value=True)
    def test_job_del_scheduler(self, mock_botones):
        from app.tasks import jobs as jobs_mod

        with patch.object(jobs_mod, "SessionLocal") as mock_sesion:
            mock_sesion.return_value = _sesion()
            resultado = jobs_mod.enviar_recordatorios_inactivos()
        self.assertIn("enviados", resultado)
        self.assertIn("candidatos", resultado)

    @patch("app.services.whatsapp_service.enviar_botones", return_value=True)
    def test_job_sobrevive_a_un_error(self, mock_botones):
        from app.tasks import jobs as jobs_mod

        with patch.object(jobs_mod, "SessionLocal") as mock_sesion:
            sesion = _sesion()
            sesion.execute = Mock(side_effect=RuntimeError("bd caida"))
            mock_sesion.return_value = sesion
            resultado = jobs_mod.enviar_recordatorios_inactivos()
        # No lanza: devuelve el resumen vacío.
        self.assertEqual(resultado["enviados"], 0)


class TestJobRegistrado(unittest.TestCase):
    def test_el_job_de_recordatorios_esta_registrado(self):
        from app.tasks import scheduler as sched

        sched._automatico_enabled = True
        try:
            sched._agregar_job()
            job = sched.scheduler.get_job("recordatorios_inactivos")
            self.assertIsNotNone(job, "el job de recordatorios debe existir")
        finally:
            for jid in ("radicacion_automatica", "recordatorios_inactivos"):
                with contextlib.suppress(Exception):
                    sched.scheduler.remove_job(jid)
            sched._automatico_enabled = False


if __name__ == "__main__":
    unittest.main()