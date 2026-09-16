"""Tests para el pre-vuelo de radicación (parte 1).

Antes de abrir Playwright, `iniciar_radicacion` debe validar que la tutela
tiene los datos que el bot necesita y que el PDF existe. Si falla:
- No se arranca el navegador (no gasta 2Captcha ni intentos).
- El registro Radicacion queda en 'fallida' con motivo claro en `ultimo_error`.
- `intentos` NO se incrementa (no es un fallo reintentable, es dato malo).
- Si faltan `derechos_vulnerados`, se rellena el default salud/vida (tutelas de
  salud): la gente no siempre sabe qué derechos le vulneraron.
"""
import asyncio
import json
import tempfile
import unittest
from unittest import mock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela
from app.models.user import User
from app.services import radicacion_service
from app.services.radicacion_service import (
    DERECHOS_DEFAULT_SALUD,
    _pre_flight_radicacion,
)


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    return TestingSession()


def _datos_completos() -> dict:
    return {
        "tipo": "salud",
        "accionante_nombre": "Ana López",
        "accionante_tipo_doc": "CC",
        "accionante_cedula": "1030241555",
        "accionante_telefono": "3001234567",
        "accionante_email": "ana@correo.com",
        "ciudad": "Bogotá",
        "departamento": "Cundinamarca",
        "accionante_direccion": "Calle 1 # 2-3",
        "accionado": "Nueva EPS",
        "accionado_tipo": "juridica",
        "hechos": "Me negaron un medicamento.",
        "derechos_vulnerados": ["Salud"],
    }


class TestPreFlightRadicacion(unittest.TestCase):
    def test_datos_completos_sin_errores(self):
        """Todo presente y PDF real → no hay errores."""
        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf:
            pdf.write(b"%PDF-1.4")
            pdf.flush()
            errores = _pre_flight_radicacion(_datos_completos(), pdf.name)
        self.assertEqual(errores, [])

    def test_falta_campo_obligatorio(self):
        """Campos que el bot escribe en el portal no pueden faltar."""
        datos = _datos_completos()
        datos["accionado"] = ""
        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf:
            pdf.write(b"%PDF-1.4")
            pdf.flush()
            errores = _pre_flight_radicacion(datos, pdf.name)
        self.assertTrue(any("accionado" in e for e in errores))

    def test_pdf_inexistente(self):
        """PDF ausente → error claro, no se intenta abrir el portal."""
        errores = _pre_flight_radicacion(_datos_completos(), "/no/existe/tutela.pdf")
        self.assertTrue(any("PDF" in e for e in errores))

    def test_pdf_vacio(self):
        """PDF de 0 bytes → error, sería rechazado por el portal igual."""
        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf:
            errores = _pre_flight_radicacion(_datos_completos(), pdf.name)
        self.assertTrue(any("vacío" in e for e in errores))

    def test_email_invalido(self):
        """Email mal formado se detecta antes de abrir el navegador."""
        datos = _datos_completos()
        datos["accionante_email"] = "ana.sincorreo"
        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf:
            pdf.write(b"%PDF-1.4")
            pdf.flush()
            errores = _pre_flight_radicacion(datos, pdf.name)
        self.assertTrue(any("accionante_email" in e for e in errores))

    def test_derechos_vacios_se_rellenan_salud_vida(self):
        """Tutelas de salud: si no hay derechos, se usa salud/vida (default)."""
        datos = _datos_completos()
        del datos["derechos_vulnerados"]
        with tempfile.NamedTemporaryFile(suffix=".pdf") as pdf:
            pdf.write(b"%PDF-1.4")
            pdf.flush()
            errores = _pre_flight_radicacion(datos, pdf.name)
        self.assertEqual(errores, [])
        self.assertEqual(datos["derechos_vulnerados"], list(DERECHOS_DEFAULT_SALUD))


class TestGuardIniciarRadicacion(unittest.TestCase):
    """El guard dentro de `iniciar_radicacion` no necesita el navegador."""

    def setUp(self):
        self.session = _nueva_sesion()

    def tearDown(self):
        self.session.close()

    def _crear_tutela(self, datos: dict, pdf_path: str = "/no/existe/guard.pdf"):
        user = User(telefono="573009990002", estado="activo", consentimiento=True)
        self.session.add(user)
        self.session.flush()
        tutela = Tutela(
            user_id=user.id,
            tipo="salud",
            estado="pago_confirmado",
            datos_json=json.dumps(datos),
            pdf_path=pdf_path,
        )
        self.session.add(tutela)
        self.session.commit()
        return tutela

    def test_falla_temprano_con_datos_incompletos(self):
        """Sin datos completos: fallida temprana, sin tocar el bot."""
        tutela = self._crear_tutela({"tipo": "salud"})
        with mock.patch.object(
            radicacion_service, "SessionLocal", return_value=self.session
        ), mock.patch.object(
            radicacion_service, "_get_bot"
        ) as get_bot:
            resultado = asyncio.run(
                radicacion_service.iniciar_radicacion(tutela.id, forzar=True)
            )
        get_bot.assert_not_called()  # nunca se abrió Playwright

        self.assertFalse(resultado["ok"])
        self.assertIn("Pre-vuelo", resultado.get("error", ""))

        rad = self.session.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela.id)
        ).scalar_one_or_none()
        self.assertIsNotNone(rad)
        self.assertEqual(rad.estado, "fallida")
        self.assertIn("Pre-vuelo", rad.ultimo_error or "")
        self.assertEqual(rad.intentos, 0, "Un dato malo no debe quemar reintentos")


if __name__ == "__main__":
    unittest.main()