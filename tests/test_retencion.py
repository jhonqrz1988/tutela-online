"""Retención de datos de tráfico (visitas y clics antiguos).

Cubre:
1. La purga borra solo lo que está fuera de la ventana configurada.
2. ``0`` días desactiva la purga de esa tabla (no borra nada).
3. El borrado respeta el tamaño de lote y el máximo por corrida (en Postgres un
   DELETE masivo bloquea la tabla: Supabase Free es una instancia Micro).
4. La purga NUNCA lanza: si la BD falla, devuelve el error en el resultado.
5. El job de retención vive en el scheduler pero NO depende del switch de
   radicación del panel admin.
"""
import datetime
import unittest
from unittest import mock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models.clic import ClicWhatsApp
from app.models.visita import VisitaLanding
from app.services import visitas_service
from app.tasks import scheduler as sched

AHORA = datetime.datetime.utcnow()


def _nueva_fabrica():
    """Fabrica de sesiones sobre una BD SQLite en memoria (StaticPool)."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _hace_dias(dias: int) -> datetime.datetime:
    return AHORA - datetime.timedelta(days=dias)


class TestPurga(unittest.TestCase):
    def setUp(self):
        self.Fabrica = _nueva_fabrica()
        self.session = self.Fabrica()
        self.settings_originales = {
            "retencion_visitas_dias": settings.retencion_visitas_dias,
            "retencion_clics_dias": settings.retencion_clics_dias,
            "retencion_lote": settings.retencion_lote,
            "retencion_max_por_corrida": settings.retencion_max_por_corrida,
        }

    def tearDown(self):
        for clave, valor in self.settings_originales.items():
            setattr(settings, clave, valor)
        self.session.close()

    def _visita(self, dias):
        self.session.add(VisitaLanding(fuente="fb", created_at=_hace_dias(dias)))

    def _clic(self, dias):
        self.session.add(ClicWhatsApp(fuente="fb", created_at=_hace_dias(dias)))

    def _purgar(self, **kwargs):
        """Ejecuta la purga contra la BD de prueba (no la real)."""
        with mock.patch.object(visitas_service, "SessionLocal", self.Fabrica):
            return visitas_service.purgar_antiguos(**kwargs)

    def _cuenta(self, modelo):
        return self.session.execute(select(modelo)).scalars().all()

    def test_borra_lo_que_esta_fuera_de_la_ventana(self):
        self._visita(120)   # fuera: se borra
        self._visita(1)     # dentro: se queda
        self._clic(200)     # fuera: se borra
        self._clic(2)       # dentro: se queda
        self.session.commit()

        resultado = self._purgar(dias_visitas=90, dias_clics=90, lote=100, maximo=1000)

        self.assertEqual(resultado["visitas"], 1)
        self.assertEqual(resultado["clics"], 1)
        self.assertIsNone(resultado["error"])
        self.assertEqual(len(self._cuenta(VisitaLanding)), 1)
        self.assertEqual(len(self._cuenta(ClicWhatsApp)), 1)
        self.assertIn("visitas", resultado["cortes"])

    def test_dias_cero_desactiva_la_purga(self):
        self._visita(999)
        self._clic(999)
        self.session.commit()

        resultado = self._purgar(dias_visitas=0, dias_clics=0)

        self.assertEqual(resultado["visitas"], 0)
        self.assertEqual(resultado["clics"], 0)
        self.assertEqual(len(self._cuenta(VisitaLanding)), 1)
        self.assertEqual(len(self._cuenta(ClicWhatsApp)), 1)

    def test_respeta_lote_y_maximo_por_corrida(self):
        for dias in (100, 110, 120, 130, 140):
            self._visita(dias)
        self.session.commit()

        resultado = self._purgar(dias_visitas=90, dias_clics=90, lote=2, maximo=3)

        self.assertEqual(resultado["visitas"], 3, "no debe pasar del máximo por corrida")
        self.assertEqual(len(self._cuenta(VisitaLanding)), 2, "el resto queda para la siguiente")

    def test_usa_la_configuracion_por_defecto(self):
        settings.retencion_visitas_dias = 30
        settings.retencion_clics_dias = 30
        settings.retencion_lote = 100
        settings.retencion_max_por_corrida = 1000
        self._visita(60)
        self._visita(3)
        self.session.commit()

        resultado = self._purgar()

        self.assertEqual(resultado["visitas"], 1)
        self.assertEqual(len(self._cuenta(VisitaLanding)), 1)

    def test_nunca_lanza_si_la_base_falla(self):
        def _explota():
            raise RuntimeError("base caida")

        with mock.patch.object(visitas_service, "SessionLocal", _explota):
            resultado = visitas_service.purgar_antiguos(dias_visitas=90, dias_clics=90)

        self.assertEqual(resultado["visitas"], 0)
        self.assertIn("base caida", resultado["error"])


class TestJobRetencionEnScheduler(unittest.TestCase):
    def setUp(self):
        self.original = (settings.enable_scheduler, settings.enable_retencion)

    def tearDown(self):
        settings.enable_scheduler, settings.enable_retencion = self.original

    def test_registra_purga_diaria_a_las_3_bogota(self):
        settings.enable_retencion = True
        with mock.patch.object(sched, "scheduler") as sched_mock:
            sched_mock.get_job.return_value = None
            sched._agregar_job_retencion()
            args, kwargs = sched_mock.add_job.call_args
        self.assertEqual(kwargs["id"], "retencion_datos")
        self.assertEqual(kwargs["hour"], 3)
        self.assertEqual(kwargs["timezone"], "America/Bogota")

    def test_no_registra_si_esta_desactivada(self):
        settings.enable_retencion = False
        with mock.patch.object(sched, "scheduler") as sched_mock:
            sched_mock.get_job.return_value = None
            sched._agregar_job_retencion()
            sched_mock.add_job.assert_not_called()

    def test_arranca_el_hilo_aunque_la_radicacion_este_apagada(self):
        """La retención no debe depender del switch de radicación."""
        settings.enable_scheduler = False
        settings.enable_retencion = True
        with mock.patch.object(sched, "scheduler") as sched_mock:
            sched_mock.running = False
            sched_mock.get_job.return_value = None
            sched.iniciar_scheduler()
            sched_mock.start.assert_called_once()
            sched_mock.add_job.assert_called_once()

    def test_sin_radicacion_ni_retencion_no_crea_hilos(self):
        settings.enable_scheduler = False
        settings.enable_retencion = False
        with mock.patch.object(sched, "scheduler") as sched_mock:
            sched.iniciar_scheduler()
            sched_mock.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
