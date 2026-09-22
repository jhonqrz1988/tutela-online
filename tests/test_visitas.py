"""Tests del registro de visitas a la landing (medición de pauta)."""
import unittest
from datetime import datetime, timezone
from unittest import mock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.visita import VisitaLanding
from app.services import visitas_service


class TestRegistroVisitas(unittest.TestCase):
    def _motor(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        return engine

    def _registrar(self, S, query_string):
        with mock.patch.object(visitas_service, "SessionLocal", S):
            visitas_service.registrar_visita_landing(query_string)

    def test_visita_directa_sin_parametros(self):
        S = sessionmaker(bind=self._motor(), expire_on_commit=False)
        self._registrar(S, "")
        session = S()
        v = session.execute(select(VisitaLanding)).scalar_one()
        self.assertEqual(v.fuente, "directo")
        self.assertFalse(v.es_pauta)
        session.close()

    def test_visita_de_pauta_facebook(self):
        S = sessionmaker(bind=self._motor(), expire_on_commit=False)
        qs = ("fbclid=IwcGRvZgFleHRuA2FlbQEwAGFkaWQBqzhx9bm9U3NydGMGYXBwX2lkDDM1MDY4NTUzMTcyOAAB"
              "&utm_medium=paid&utm_source=fb&utm_id=120251876760560563&utm_campaign=120251876760560563")
        self._registrar(S, qs)
        session = S()
        v = session.execute(select(VisitaLanding)).scalar_one()
        self.assertEqual(v.fuente, "fb")
        self.assertEqual(v.medio, "paid")
        self.assertTrue(v.es_pauta, "con fbclid debe marcarse como pauta")
        session.close()

    def test_utm_solo_tambien_es_pauta(self):
        S = sessionmaker(bind=self._motor(), expire_on_commit=False)
        self._registrar(S, "utm_source=google&utm_medium=cpc&utm_campaign=branding")
        session = S()
        v = session.execute(select(VisitaLanding)).scalar_one()
        self.assertEqual(v.fuente, "google")
        self.assertTrue(v.es_pauta)
        session.close()

    def test_error_de_bd_no_propaga(self):
        def siempre_falla(*args, **kwargs):
            raise RuntimeError("bd caida")

        with mock.patch.object(visitas_service, "SessionLocal", siempre_falla):
            # No debe lanzar excepción hacia la landing
            visitas_service.registrar_visita_landing("utm_source=fb")


class TestNombreFuente(unittest.TestCase):
    def test_mapeo_de_nombres_conocidos(self):
        casos = {
            "directo": "Directo",
            "fb": "Facebook",
            "ig": "Instagram",
            "an": "Anuncios",
            "google": "Google",
            "chatgpt.com": "ChatGPT",
        }
        for crudo, esperado in casos.items():
            with self.subTest(fuente=crudo):
                self.assertEqual(visitas_service.nombre_fuente(crudo), esperado)

    def test_fuente_desconocida_mantiene_crudo(self):
        self.assertEqual(visitas_service.nombre_fuente("noticias.elcolo"), "noticias.elcolo")

    def test_vacio_y_none_devuelven_directo(self):
        self.assertEqual(visitas_service.nombre_fuente(""), "Directo")
        self.assertEqual(visitas_service.nombre_fuente(None), "Directo")


class TestAgruparCortes(unittest.TestCase):
    def _v(self, iso):
        """Visita simple con created_at en UTC."""
        return {"created_at": datetime.fromisoformat(iso), "es_pauta": False}

    def test_corte_mensual_ordena_descendente(self):
        visitas = [
            self._v("2026-01-10T15:00:00"),
            self._v("2026-02-20T15:00:00"),
            self._v("2026-02-25T15:00:00"),
            self._v("2026-03-05T15:00:00"),
        ]
        meses = visitas_service.agrupar_por_periodo(visitas, "mes")
        self.assertEqual([m["clave"] for m in meses], ["2026-03", "2026-02", "2026-01"])
        self.assertEqual(meses[0]["n"], 1)
        self.assertEqual(meses[1]["n"], 2)
        self.assertEqual(meses[0]["etiqueta"], "Marzo 2026")

    def test_corte_semanal_agrupa_por_semana(self):
        # Jueves 2026-01-01 cae en la última semana de 2025 (iso)
        visitas = [
            self._v("2026-01-01T15:00:00"),
            self._v("2026-01-05T15:00:00"),  # lunes siguiente
            self._v("2026-01-08T15:00:00"),
        ]
        semanas = visitas_service.agrupar_por_periodo(visitas, "semana")
        claves = {s["clave"] for s in semanas}
        self.assertEqual(len(claves), 2, "deben agruparse en dos semanas distintas")
        total = sum(s["n"] for s in semanas)
        self.assertEqual(total, 3)

    def test_corte_incluye_visitas_pauta(self):
        visitas = [
            {"created_at": datetime(2026, 5, 10, 15, 0, 0, tzinfo=timezone.utc), "es_pauta": True},
            {"created_at": datetime(2026, 5, 11, 15, 0, 0, tzinfo=timezone.utc), "es_pauta": False},
        ]
        meses = visitas_service.agrupar_por_periodo(visitas, "mes")
        self.assertEqual(meses[0]["n"], 2)
        self.assertEqual(meses[0]["pauta"], 1)

    def test_corte_mensual_tutelas_y_radicadas(self):
        tutelas = [
            {"created_at": datetime(2026, 4, 2, 15, 0, 0, tzinfo=timezone.utc), "estado": "radicada"},
            {"created_at": datetime(2026, 4, 9, 15, 0, 0, tzinfo=timezone.utc), "estado": "fallida"},
            {"created_at": datetime(2026, 5, 3, 15, 0, 0, tzinfo=timezone.utc), "estado": "radicada"},
        ]
        meses = visitas_service.agrupar_tutelas_por_periodo(tutelas, "mes")
        self.assertEqual([m["clave"] for m in meses], ["2026-05", "2026-04"])
        self.assertEqual(meses[0]["tutelas"], 1)
        self.assertEqual(meses[0]["radicadas"], 1)
        self.assertEqual(meses[1]["tutelas"], 2)
        self.assertEqual(meses[1]["radicadas"], 1)


if __name__ == "__main__":
    unittest.main()