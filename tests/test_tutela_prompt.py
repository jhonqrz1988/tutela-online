"""Tests de la integración del módulo tutela_prompt en generar_tutela.

Cubre:
1. mapear_datos_caso traduce el dict del flujo al esquema de build_user_prompt
2. generar_tutela usa SYSTEM_PROMPT del módulo + build_user_prompt + temperature 0.3
3. las citas verificadas se siguen inyectando al prompt de usuario
4. EPS autocompletado sigue persistiendo NIT/correo en `datos`
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from app.services import ia_service
from app.services.ia_service import generar_tutela, mapear_datos_caso
from app.services.tutela_prompt import SYSTEM_PROMPT, build_user_prompt

DATOS_BASE = {
    "accionante_nombre": "Juan Perez Gomez",
    "accionante_tipo_doc": "CC",
    "accionante_cedula": "1020304050",
    "accionante_telefono": "3001112233",
    "accionante_email": "juan@correo.com",
    "accionante_direccion": "Calle 1 # 2-3, Barrio Centro",
    "ciudad": "Bogotá",
    "departamento": "Cundinamarca",
    "accionado": "Nueva EPS",
    "accionado_tipo": "juridica",
    "accionado_nit": "",
    "accionado_email": "",
    "hechos": "1. [10/01/2026] - Pido cita de medicina general.",
    "derechos_vulnerados": ["Art. 49 CP", "Art. 11 CP"],
    "peticion": "Ordenar a Nueva EPS autorizar la cita en 48 horas.",
}


class _FakeCompletions:
    def __init__(self, content: str):
        self.content = content
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class _FakeClient:
    def __init__(self, content: str):
        self.chat = SimpleNamespace(completions=_FakeCompletions(content))


class TestMapearDatosCaso(unittest.TestCase):
    def test_mapea_campos_del_flujo_al_esquema_del_modulo(self):
        mapeado = mapear_datos_caso(dict(DATOS_BASE))
        self.assertEqual(mapeado["nombre"], "Juan Perez Gomez")
        self.assertEqual(mapeado["cedula"], "1020304050")
        self.assertEqual(mapeado["direccion"], "Calle 1 # 2-3, Barrio Centro")
        self.assertEqual(mapeado["telefono"], "3001112233")
        self.assertEqual(mapeado["correo"], "juan@correo.com")
        self.assertEqual(mapeado["entidad_accionada"], "Nueva EPS")
        self.assertEqual(mapeado["nit_entidad"], "")
        self.assertEqual(mapeado["correo_notificacion_entidad"], "")
        self.assertEqual(mapeado["descripcion_negativa"], DATOS_BASE["hechos"])
        self.assertEqual(mapeado["ciudad_radicacion"], "Bogotá")

    def test_campos_no_recolectados_quedan_vacios(self):
        mapeado = mapear_datos_caso({})
        self.assertEqual(mapeado["ciudad_expedicion"], "")
        self.assertEqual(mapeado["tipo_afiliacion"], "")
        self.assertEqual(mapeado["diagnostico"], "")
        self.assertEqual(mapeado["medicamentos_o_servicio"], "")
        self.assertEqual(mapeado["nombre"], "")

    def test_no_inventa_campos_ausentes(self):
        mapeado = mapear_datos_caso({})
        self.assertNotIn("no se", mapeado["nombre"].lower())
        self.assertNotIn("desconocido", mapeado["nombre"].lower())

    def test_build_user_prompt_marca_faltantes(self):
        prompt = build_user_prompt(mapear_datos_caso({}))
        self.assertIn("[NO PROPORCIONADO]", prompt)


class TestGenerarTutelaUsaNuevoPrompt(unittest.TestCase):
    def test_system_prompt_es_del_modulo(self):
        fake = _FakeClient("TEXTO")
        with mock.patch.object(ia_service, "_get_client", return_value=fake):
            asyncio.run(generar_tutela(dict(DATOS_BASE)))
        kwargs = fake.chat.completions.last_kwargs
        system = kwargs["messages"][0]
        self.assertEqual(system["role"], "system")
        self.assertEqual(system["content"], SYSTEM_PROMPT)

    def test_prompt_usuario_viene_de_build_user_prompt_con_datos(self):
        fake = _FakeClient("TEXTO")
        with mock.patch.object(ia_service, "_get_client", return_value=fake):
            asyncio.run(generar_tutela(dict(DATOS_BASE)))
        kwargs = fake.chat.completions.last_kwargs
        user = "\n".join(m["content"] for m in kwargs["messages"] if m["role"] == "user")
        self.assertIn("DATOS DEL CASO", user)
        self.assertIn("Juan Perez Gomez", user)
        self.assertIn("1. [10/01/2026] - Pido cita de medicina general.", user)
        self.assertIn("NUEVA EPS", user)

    def test_temperature_es_0_3(self):
        fake = _FakeClient("TEXTO")
        with mock.patch.object(ia_service, "_get_client", return_value=fake):
            asyncio.run(generar_tutela(dict(DATOS_BASE)))
        self.assertEqual(fake.chat.completions.last_kwargs["temperature"], 0.3)

    def test_citas_verificadas_se_inyectan(self):
        fake = _FakeClient("TEXTO")
        citas = [
            {"referencia": "Ley 1751 de 2015", "texto_resumen": "La salud es un derecho fundamental autónomo."},
            {"referencia": "Art. 49 Constitución Política de Colombia", "texto_resumen": "Atención de la salud."},
        ]
        with mock.patch.object(ia_service, "_get_client", return_value=fake):
            asyncio.run(generar_tutela(dict(DATOS_BASE), citas=citas))
        kwargs = fake.chat.completions.last_kwargs
        user = "\n".join(m["content"] for m in kwargs["messages"] if m["role"] == "user")
        self.assertIn("Ley 1751 de 2015", user)
        self.assertIn("Art. 49 Constitución", user)
        self.assertIn("derecho fundamental autónomo", user)

    def test_sin_citas_no_menciona_ley(self):
        fake = _FakeClient("TEXTO")
        with mock.patch.object(ia_service, "_get_client", return_value=fake):
            asyncio.run(generar_tutela(dict(DATOS_BASE)))
        kwargs = fake.chat.completions.last_kwargs
        user = "\n".join(m["content"] for m in kwargs["messages"] if m["role"] == "user")
        self.assertNotIn("Ley 1751 de 2015", user)

    def test_instruye_omitir_datos_faltantes(self):
        fake = _FakeClient("TEXTO")
        with mock.patch.object(ia_service, "_get_client", return_value=fake):
            asyncio.run(generar_tutela(dict(DATOS_BASE)))
        kwargs = fake.chat.completions.last_kwargs
        user = "\n".join(m["content"] for m in kwargs["messages"] if m["role"] == "user")
        self.assertIn("OMITEN", user)
        self.assertIn("No listar archivos", user)

    def test_system_prompt_omite_no_marca_pendiente_masivo(self):
        self.assertIn("OMÍTELO", SYSTEM_PROMPT)
        self.assertIn("NUNCA entregas una plantilla", SYSTEM_PROMPT)

    def test_eps_autocompletado_persiste(self):
        datos = dict(DATOS_BASE)
        fake = _FakeClient("TEXTO")
        with mock.patch.object(ia_service, "_get_client", return_value=fake):
            asyncio.run(generar_tutela(datos))
        self.assertEqual(datos["accionado_nit"], "900156264-2")
        self.assertEqual(datos["accionado_email"], "secretaria.general@nuevaeps.com.co")
        self.assertEqual(datos["accionado"], "NUEVA EPS")


if __name__ == "__main__":
    unittest.main()