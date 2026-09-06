"""Tests para Fase G: confirmación real de datos personales y preguntas clínicas.

Cubre dos mejoras del flujo de recolección:

1. Confirmación de datos personales tras los 8 pasos:
   - Antes era imposible corregir una cédula mal digitada: la opción "corregir"
     de `revision_datos` volvía a la narración y `aplicar_extraccion` NUNCA
     tocaba CAMPOS_PERSONALES_GUARDADOS.
   - Ahora, al terminar los pasos, se entra a `confirmar_datos_personales` con
     resumen y botones; si el usuario corrige, se elige el campo por número y
     el nuevo valor se escribe REALMENTE en `datos`.

2. Preguntas clínicas del caso antes de las pruebas:
   - Se recogen tipo de afiliación, servicio negado y fechas con preguntas
     dirigidas para que la IA no invente esos datos en la narración.
"""
import asyncio
import json
import unittest
from unittest import mock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.tutela import Tutela
from app.models.user import User

from app.api import webhook_whatsapp

PASOS = webhook_whatsapp.DATOS_PERSONALES_STEPS
CAMPOS = [c for c, _ in PASOS]


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    return TestingSession()


def _datos_personales_completos():
    return {
        "tipo": "salud",
        "accionante_nombre": "Juan Perez Gomez",
        "accionante_tipo_doc": "CC",
        "accionante_cedula": "1020304050",
        "accionante_telefono": "3001112233",
        "accionante_email": "juan@correo.com",
        "ciudad": "Bogotá",
        "accionante_direccion": "Calle 1 # 2-3, Barrio Centro",
        "departamento": "Cundinamarca",
    }


class _FlujoMixin(unittest.TestCase):
    def _crear_usuario_tutela(self, session, estado, datos, tipo="salud"):
        user = User(telefono="573001112233", estado="activo", consentimiento=True)
        session.add(user)
        session.flush()
        tutela = Tutela(
            user_id=user.id,
            tipo=tipo,
            estado=estado,
            datos_json=json.dumps(datos),
        )
        session.add(tutela)
        session.commit()
        return user, tutela

    async def _procesar(self, session, telefono, body):
        with mock.patch.object(webhook_whatsapp, "enviar_texto", return_value=True), \
             mock.patch.object(webhook_whatsapp, "enviar_botones", return_value=True) as mock_b, \
             mock.patch.object(
                 webhook_whatsapp, "extraer_datos_caso",
                 new=mock.AsyncMock(return_value={"hechos": "caso", "accionado": "EPS"}),
             ) as mock_ext:
            resp = await webhook_whatsapp.procesar_mensaje(session, telefono, body, 0, "", False)
        return resp, mock_b, mock_ext


class TestConfirmarDatosPersonales(_FlujoMixin):
    def test_ultimo_paso_personal_lleva_a_confirmar_no_a_narracion(self):
        """Completar los 8 pasos NO pasa directo a narración: confirma primero."""
        session = _nueva_sesion()
        user, tutela = self._crear_usuario_tutela(
            session, "recogiendo_datos", {"tipo": "salud", "_step": len(PASOS) - 1,
                                          **{CAMPOS[i]: f"valor_{i}" for i in range(len(PASOS) - 1)}}
        )
        campo_final, _ = PASOS[-1]
        resp, mock_b, _ = asyncio.run(
            self._procesar(session, user.telefono, "Dato final del último campo")
        )
        self.session_tutela = session.execute(select(Tutela)).scalars().all()[0]
        self.assertEqual(self.session_tutela.estado, "confirmar_datos_personales")
        guardados = json.loads(self.session_tutela.datos_json)
        self.assertEqual(guardados[campo_final], "Dato final del último campo")
        # Se mostraron botones para confirmar o corregir
        self.assertTrue(mock_b.called)

    def test_confirmar_datos_correctos_pasa_a_narracion(self):
        session = _nueva_sesion()
        user, tutela = self._crear_usuario_tutela(
            session, "confirmar_datos_personales", _datos_personales_completos()
        )
        resp, mock_b, mock_ext = asyncio.run(
            self._procesar(session, user.telefono, "1")
        )
        tutela = session.execute(select(Tutela)).scalars().all()[0]
        self.assertEqual(tutela.estado, "narracion")
        self.assertFalse(mock_ext.called)  # aún no se extrae nada

    def test_corregir_solamente_escribe_el_valor_real(self):
        """Al elegir el campo y escribir el nuevo valor, `datos` cambia de verdad."""
        session = _nueva_sesion()
        datos = _datos_personales_completos()
        user, tutela = self._crear_usuario_tutela(
            session, "confirmar_datos_personales", datos
        )
        # Índice del campo cédula (tercer paso)
        idx = CAMPOS.index("accionante_cedula")

        # 1) Elige corregir → pide qué campo
        resp, _, _ = asyncio.run(self._procesar(session, user.telefono, "2"))
        tutela = session.execute(select(Tutela)).scalars().all()[0]
        self.assertEqual(tutela.estado, "corrigiendo_datos_personales")

        # 2) Elige el número de campo → pide el nuevo valor
        resp, _, _ = asyncio.run(self._procesar(session, user.telefono, str(idx + 1)))
        tutela = session.execute(select(Tutela)).scalars().all()[0]
        self.assertEqual(tutela.estado, "corrigiendo_datos_personales")

        # 3) Escribe el nuevo valor → se aplica y vuelve a confirmar
        resp, _, _ = asyncio.run(self._procesar(session, user.telefono, "9999999999"))
        tutela = session.execute(select(Tutela)).scalars().all()[0]
        guardados = json.loads(tutela.datos_json)
        self.assertEqual(guardados["accionante_cedula"], "9999999999")
        self.assertEqual(tutela.estado, "confirmar_datos_personales")

    def test_numero_de_campo_invalido_pide_de_nuevo(self):
        session = _nueva_sesion()
        user, tutela = self._crear_usuario_tutela(
            session, "corrigiendo_datos_personales", _datos_personales_completos()
        )
        resp, _, _ = asyncio.run(self._procesar(session, user.telefono, "99"))
        tutela = session.execute(select(Tutela)).scalars().all()[0]
        self.assertEqual(tutela.estado, "corrigiendo_datos_personales")


class TestPreguntasClinicas(_FlujoMixin):
    def test_confirmar_revision_entra_a_preguntas_clinicas(self):
        """Confirmar la revisión del caso ahora pregunta los datos clínicos."""
        session = _nueva_sesion()
        datos = {**_datos_personales_completos(),
                 "accionado": "Nueva EPS", "hechos": "Me negaron una cita.",
                 "derechos_vulnerados": ["Art. 49 CP"], "peticion": "Que autoricen la cita."}
        user, tutela = self._crear_usuario_tutela(session, "revision_datos", datos)
        resp, mock_b, _ = asyncio.run(self._procesar(session, user.telefono, "1"))
        tutela = session.execute(select(Tutela)).scalars().all()[0]
        self.assertEqual(tutela.estado, "preguntas_clinicas")
        # Pregunta el primer campo clínico (tipo de afiliación)
        ds = json.loads(tutela.datos_json)
        self.assertEqual(ds["_step_clinico"], 1)

    def test_responder_clinicos_los_guarda_y_llega_a_pruebas(self):
        session = _nueva_sesion()
        datos = {**_datos_personales_completos(),
                 "accionado": "Nueva EPS", "hechos": "Me negaron una cita.",
                 "derechos_vulnerados": ["Art. 49 CP"], "peticion": "Que autoricen la cita."}
        user, tutela = self._crear_usuario_tutela(session, "preguntas_clinicas", datos)

        # primeros 3 pasos clínicos
        for body in ("contributivo", "Cita de medicina general", "10/01/2026"):
            resp, _, _ = asyncio.run(self._procesar(session, user.telefono, body))
        tutela = session.execute(select(Tutela)).scalars().all()[0]
        guardados = json.loads(tutela.datos_json)
        self.assertEqual(guardados["tipo_afiliacion"], "contributivo")
        self.assertEqual(guardados["medicamentos_o_servicio"], "Cita de medicina general")
        self.assertEqual(guardados["fecha_solicitud"], "10/01/2026")

        # último paso clínico (fecha negativa) → pasa a pruebas_pendiente
        resp, _, _ = asyncio.run(self._procesar(session, user.telefono, "no recuerdo"))
        tutela = session.execute(select(Tutela)).scalars().all()[0]
        self.assertEqual(tutela.estado, "pruebas_pendiente")
        guardados = json.loads(tutela.datos_json)
        self.assertEqual(guardados["fecha_negativa"], "no recuerdo")
        self.assertNotIn("_step_clinico", guardados)


class TestMapeoClinicosAlPrompt(unittest.TestCase):
    def test_mapear_datos_caso_incluye_campos_clinicos(self):
        from app.services.ia_service import mapear_datos_caso

        mapeado = mapear_datos_caso({
            "tipo_afiliacion": "contributivo",
            "diagnostico": "colitis ulcerosa",
            "medicamentos_o_servicio": "prednisolona",
            "fecha_solicitud": "10/01/2026",
            "fecha_negativa": "12/01/2026",
            "riesgo_para_salud": "empeoramiento",
            "accionado": "Nueva EPS",
        })
        self.assertEqual(mapeado["tipo_afiliacion"], "contributivo")
        self.assertEqual(mapeado["diagnostico"], "colitis ulcerosa")
        self.assertEqual(mapeado["medicamentos_o_servicio"], "prednisolona")
        self.assertEqual(mapeado["fecha_solicitud"], "10/01/2026")
        self.assertEqual(mapeado["fecha_negativa"], "12/01/2026")
        self.assertEqual(mapeado["riesgo_para_salud"], "empeoramiento")


if __name__ == "__main__":
    unittest.main()