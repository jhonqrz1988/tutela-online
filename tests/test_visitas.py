"""Tests del registro de visitas a la landing (medición de pauta)."""
import unittest
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


if __name__ == "__main__":
    unittest.main()