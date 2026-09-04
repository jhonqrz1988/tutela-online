"""Tests de Fase E: calidad del PDF de tutela y flujo de pago.

Cubre:
1. normalizacion de datos "no se" -> vacio
2. el autocompletado EPS persiste NIT/correo/nombre canonico en `datos`
   (antes solo alimentaba el prompt local -> el PDF plantilla los omitia)
3. las citas verificadas (whitelist) se inyectan al prompt de generar_tutela
4. generar_pdf en modo IA inserta encabezado determinista con fecha espanola,
   secciones I/II reconstruidas desde `datos`, y elimina la fecha inventada
5. aplicar_extraccion no pisa los datos personales ya recolectados
6. verificar_citas devuelve texto_resumen y fundamentacion_juridica_extra
7. integracion: _generar_con_verificacion produce PDF con fundamentacion citada
8. whatsapp_service usa Graph API v25.0 unificada
9. el endpoint admin de regenerar PDF construye desde `datos` (no hechos sueltos)
"""
import asyncio
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

import fitz

from app.config import settings
from app.database import Base, SessionLocal, engine
from app.models.cita_legal import CitaLegal
from app.models.tutela import Tutela
from app.models.user import User
from app.services import ia_service, verificacion_service
from app.services.documento_service import generar_pdf
from app.services.ia_service import (
    aplicar_extraccion,
    generar_tutela,
    normalizar_dato_obligatorio,
)

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

TEXTO_IA = (
    "I. ENCABEZADO: Bogotá, 1 de enero de 2026\n"
    "Señor JUEZ CONSTITUCIONAL DE BOGOTÁ (REPARTO) E.S.D.\n\n"
    "II. ACCIONANTE: Juan Perez Gomez\n\n"
    "III. HECHOS: 1. El 10/01/2026 solicité cita médica.\n\n"
    "IV. DERECHOS FUNDAMENTALES VULNERADOS: se vulnera la salud.\n\n"
    "V. FUNDAMENTOS DE PROCEDIBILIDAD: la tutela es procedente.\n\n"
    "X. JURAMENTO: Manifiesto bajo juramento que no he interpuesto otra tutela.\n\n"
    "XI. NOTIFICACIONES: correo juan@correo.com"
)


def _texto_pdf(ruta: str) -> str:
    with fitz.open(ruta) as doc:
        return "\n".join(page.get_text() for page in doc)


class _FakeCompletions:
    def __init__(self, content: str):
        self.content = content
        self.last_messages = None

    async def create(self, **kwargs):
        self.last_messages = kwargs.get("messages")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class _FakeClient:
    def __init__(self, content: str):
        self.chat = SimpleNamespace(completions=_FakeCompletions(content))


class TestNormalizarDato(unittest.TestCase):
    def test_no_se_se_convierte_en_vacio(self):
        for valor in ["no sé", "No sé", "no se", "nose", "desconocido", "no sabe", "No sabe", "N/S"]:
            self.assertEqual(normalizar_dato_obligatorio(valor), "", f"deberia vacio: {valor}")

    def test_valores_reales_se_conservan(self):
        self.assertEqual(normalizar_dato_obligatorio("900156264-2"), "900156264-2")
        self.assertEqual(normalizar_dato_obligatorio("  900156264-2  "), "900156264-2")


class TestEpsAutocompletadoPersiste(unittest.TestCase):
    def test_escribe_nit_correo_y_nombre_canonico_en_datos(self):
        datos = dict(DATOS_BASE)
        fake = _FakeClient("TEXTO")
        with mock.patch.object(ia_service, "_get_client", return_value=fake):
            asyncio.run(generar_tutela(datos))
        self.assertEqual(datos["accionado_nit"], "900156264-2")
        self.assertEqual(datos["accionado_email"], "secretaria.general@nuevaeps.com.co")
        self.assertEqual(datos["accionado"], "NUEVA EPS")

    def test_no_sobrescribe_nit_ya_capturado(self):
        datos = dict(DATOS_BASE, accionado_nit="999999999-9")
        fake = _FakeClient("TEXTO")
        with mock.patch.object(ia_service, "_get_client", return_value=fake):
            asyncio.run(generar_tutela(datos))
        self.assertEqual(datos["accionado_nit"], "999999999-9")
        self.assertEqual(datos["accionado_email"], "secretaria.general@nuevaeps.com.co")

    def test_sin_match_eps_vacio_se_normaliza_a_vacio(self):
        datos = dict(DATOS_BASE, accionado="Alcaldía de Medellín", accionado_nit="no sé", accionado_email="no sé")
        fake = _FakeClient("TEXTO")
        with mock.patch.object(ia_service, "_get_client", return_value=fake):
            asyncio.run(generar_tutela(datos))
        self.assertEqual(datos["accionado_nit"], "")
        self.assertEqual(datos["accionado_email"], "")


class TestCitasInyectadasEnPrompt(unittest.TestCase):
    def test_prompt_contiene_citas_verificadas(self):
        fake = _FakeClient("TEXTO")
        citas = [
            {"referencia": "Ley 1751 de 2015", "texto_resumen": "La salud es un derecho fundamental autónomo."},
            {"referencia": "Art. 49 Constitución Política de Colombia", "texto_resumen": "Atención de la salud."},
        ]
        with mock.patch.object(ia_service, "_get_client", return_value=fake):
            asyncio.run(generar_tutela(dict(DATOS_BASE), citas=citas))
        user_msg = "\n".join(m["content"] for m in fake.chat.completions.last_messages if m["role"] == "user")
        self.assertIn("Ley 1751 de 2015", user_msg)
        self.assertIn("Art. 49 Constitución", user_msg)
        self.assertIn("derecho fundamental autónomo", user_msg)

    def test_sin_citas_prompt_no_las_menciona(self):
        fake = _FakeClient("TEXTO")
        with mock.patch.object(ia_service, "_get_client", return_value=fake):
            asyncio.run(generar_tutela(dict(DATOS_BASE)))
        user_msg = "\n".join(m["content"] for m in fake.chat.completions.last_messages if m["role"] == "user")
        self.assertNotIn("Ley 1751 de 2015", user_msg)


class TestGenerarPdfEncabezadoFecha(unittest.TestCase):
    def _tmp(self):
        tmp = tempfile.TemporaryDirectory()
        patch_ = mock.patch.object(settings, "storage_dir", tmp.name)
        patch_.start()
        self.addCleanup(patch_.stop)
        self.addCleanup(tmp.cleanup)

    def test_modo_ia_fecha_espanola_y_datos_del_accionado(self):
        self._tmp()
        datos = dict(DATOS_BASE, accionado_nit="900156264-2", accionado_email="notif@nuevaeps.com.co")
        ruta = generar_pdf(datos, TEXTO_IA)
        texto = _texto_pdf(ruta)

        mes_es = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                  "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        now = datetime.now(UTC)
        self.assertIn(f"{now.day} de {mes_es[now.month - 1]} de {now.year}", texto)
        self.assertIn("JUEZ CONSTITUCIONAL DE BOGOTÁ", texto)
        self.assertNotIn("1 de enero de 2026", texto, "la fecha inventada por la IA no debe quedarse")
        self.assertIn("900156264-2", texto)
        self.assertIn("notif@nuevaeps.com.co", texto)
        self.assertIn("Calle 1 # 2-3, Barrio Centro", texto)
        self.assertIn("juan@correo.com", texto)

    def test_modo_plantilla_incluye_fecha_y_secciones(self):
        self._tmp()
        datos = dict(DATOS_BASE, accionado_nit="900156264-2", accionado_email="notif@nuevaeps.com.co")
        ruta = generar_pdf(datos, None)
        texto = _texto_pdf(ruta)
        self.assertIn("JUEZ CONSTITUCIONAL DE BOGOTÁ", texto)
        self.assertIn("I. ACCIONANTE", texto)
        self.assertIn("II. ACCIONADO", texto)
        self.assertIn("900156264-2", texto)
        self.assertNotIn("No se especificaron hechos", texto)

    def test_no_se_no_se_imprime_como_nit(self):
        self._tmp()
        datos = dict(DATOS_BASE, accionado_nit="no sé", accionado_email="")
        ruta = generar_pdf(datos, None)
        texto = _texto_pdf(ruta)
        self.assertNotIn("NIT: no sé", texto)
        self.assertNotIn("NIT: No sé", texto)


class TestAplicarExtraccion(unittest.TestCase):
    def test_no_pisa_datos_personales_recolectados(self):
        datos = dict(DATOS_BASE)
        extraccion = {
            "accionante_nombre": "OTRO NOMBRE",
            "accionante_email": "mal@hacker.com",
            "accionante_cedula": "000",
            "hechos": "1. [10/01/2026] - Pido cita.",
            "derechos_vulnerados": ["Art. 49 CP"],
            "accionado": "EPS Sanitas",
            "peticion": "Que ordene la cita.",
        }
        resultado = aplicar_extraccion(datos, extraccion)
        self.assertEqual(resultado["accionante_nombre"], "Juan Perez Gomez")
        self.assertEqual(resultado["accionante_email"], "juan@correo.com")
        self.assertEqual(resultado["accionante_cedula"], "1020304050")
        self.assertEqual(resultado["hechos"], "1. [10/01/2026] - Pido cita.")
        self.assertEqual(resultado["accionado"], "EPS Sanitas")
        self.assertEqual(resultado["peticion"], "Que ordene la cita.")

    def test_no_roba_direccion_ni_ciudad(self):
        datos = dict(DATOS_BASE)
        extraccion = {"accionante_direccion": "Robada", "ciudad": "Otro", "accionante_discapacidad": "MENTAL"}
        resultado = aplicar_extraccion(datos, extraccion)
        self.assertEqual(resultado["accionante_direccion"], "Calle 1 # 2-3, Barrio Centro")
        self.assertEqual(resultado["ciudad"], "Bogotá")
        self.assertEqual(resultado["accionante_discapacidad"], "MENTAL")

    def test_no_inventa_personales_si_faltan(self):
        datos = dict(DATOS_BASE)
        del datos["accionante_nombre"]
        extraccion = {"accionante_nombre": "Inventado"}
        resultado = aplicar_extraccion(datos, extraccion)
        self.assertNotIn("accionante_nombre", resultado)


class TestVerificarCitasFundamentacion(unittest.TestCase):
    def test_validas_incluyen_texto_resumen(self):
        cita = CitaLegal(
            tipo="ley", referencia="Ley 1751 de 2015", referencia_normalizada="ley 1751 de 2015",
            titulo_corto="Ley 1751/2015", texto_resumen="La salud es un derecho fundamental autónomo.",
            url_fuente="https://x", aplica_a="salud", vigente=True,
        )
        session = SessionLocal()
        try:
            session.add(cita)
            session.commit()
            resultado = verificacion_service.verificar_citas(
                [{"referencia_textual": "Ley 1751 de 2015", "contexto": "con texto"}], session
            )
            self.assertEqual(len(resultado["validas"]), 1)
            self.assertEqual(resultado["validas"][0]["texto_resumen"], "La salud es un derecho fundamental autónomo.")
            self.assertEqual(resultado["validas"][0]["titulo_corto"], "Ley 1751/2015")
            cita_id = cita.id
        finally:
            session.close()
        session = SessionLocal()
        try:
            session.query(CitaLegal).filter(CitaLegal.id == cita_id).delete()
            session.commit()
        finally:
            session.close()

    def test_fundamentacion_extra_cita_las_referencias(self):
        validas = [
            {"referencia": "Ley 1751 de 2015", "texto_resumen": "La salud es un derecho fundamental autónomo."},
        ]
        bloque = verificacion_service.fundamentacion_juridica_extra(validas)
        self.assertIn("FUNDAMENTACIÓN JURÍDICA", bloque)
        self.assertIn("Ley 1751 de 2015", bloque)
        self.assertIn("derecho fundamental autónomo", bloque)

    def test_insertar_fundamentacion_antes_del_juramento(self):
        texto = "FUNDAMENTOS...\n\nX. JURAMENTO: bajo juramento...\n\nXI. NOTIFICACIONES"
        resultado = verificacion_service.insertar_fundamentacion(texto, "FUNDAMENTACIÓN JURÍDICA\n1. Ley 1751 de 2015.")
        idx_jura = resultado.index("X. JURAMENTO")
        idx_fund = resultado.index("FUNDAMENTACIÓN JURÍDICA")
        self.assertLess(idx_fund, idx_jura)


class TestIntegracionGenerarConVerificacion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.session = SessionLocal()

    @classmethod
    def tearDownClass(cls):
        cls.session.close()

    def test_webhook_genera_pdf_con_fundamentacion_y_fecha(self):
        from app.api import webhook_whatsapp

        temp = tempfile.TemporaryDirectory()
        usuario = User(telefono="573999888777", nombre="Juan Perez Gomez")
        self.session.add(usuario)
        self.session.flush()
        cita = CitaLegal(
            tipo="ley", referencia="Ley 1751 de 2015", referencia_normalizada="ley 1751 de 2015",
            titulo_corto="Ley 1751/2015", texto_resumen="La salud es un derecho fundamental autónomo.",
            aplica_a="salud", vigente=True,
        )
        self.session.add(cita)
        self.session.flush()
        tutela = Tutela(user_id=usuario.id, tipo="salud", estado="narracion",
                        datos_json=json.dumps(DATOS_BASE))
        self.session.add(tutela)
        self.session.commit()
        try:
            with mock.patch.object(settings, "storage_dir", temp.name), \
                 mock.patch.object(webhook_whatsapp, "generar_tutela",
                                   AsyncMock(return_value=TEXTO_IA)), \
                 mock.patch.object(webhook_whatsapp, "extraer_citas",
                                   AsyncMock(return_value=[{"referencia_textual": "Ley 1751 de 2015",
                                                            "tipo": "ley", "contexto": "sin enfermedad"}])) as ex_citas, \
                 mock.patch.object(webhook_whatsapp, "enviar_documento", return_value=True) as env_doc:

                ruta = asyncio.run(webhook_whatsapp._generar_con_verificacion(
                    self.session, tutela, dict(DATOS_BASE), "573999888777", []
                ))
                self.session.refresh(tutela)
                self.assertEqual(tutela.estado_verificacion, "verificada")
                self.assertEqual(tutela.estado, "esperando_decision_radicacion")
                env_doc.assert_called_once()
                ex_citas.assert_awaited_once()
                texto = _texto_pdf(ruta)
                self.assertIn("FUNDAMENTACIÓN JURÍDICA", texto)
                self.assertIn("Ley 1751 de 2015", texto)
                self.assertIn("derecho fundamental autónomo", texto)
                self.assertIn("JUEZ CONSTITUCIONAL DE BOGOTÁ", texto)
                self.assertNotIn("1 de enero de 2026", texto)
        finally:
            temp.cleanup()
            self.session.query(Tutela).filter(Tutela.id == tutela.id).delete()
            self.session.query(CitaLegal).filter(CitaLegal.id == cita.id).delete()
            self.session.query(User).filter(User.id == usuario.id).delete()
            self.session.commit()


class TestWhatsappGraphV25(unittest.TestCase):
    def test_upload_y_send_usan_v25(self):
        from app.services import whatsapp_service

        with tempfile.TemporaryDirectory() as tmp:
            ruta_pdf = os.path.join(tmp, "tutela_1.pdf")
            with open(ruta_pdf, "wb") as f:
                f.write(b"%PDF-1.4 test")
            fake_resp_up = SimpleNamespace(status_code=200, json=lambda: {"id": "media_123"}, text="")
            fake_resp_send = SimpleNamespace(is_success=True, status_code=200, text="")
            llamadas = []

            def _fake_post(url, **kwargs):
                llamadas.append(url)
                if "/media" in url:
                    return fake_resp_up
                return fake_resp_send

            with mock.patch.object(whatsapp_service.httpx, "post", side_effect=_fake_post), \
                 mock.patch.object(settings, "meta_access_token", "token-test"), \
                 mock.patch.object(settings, "meta_phone_number_id", "123456"), \
                 mock.patch.object(settings, "whatsapp_provider", "meta"):
                whatsapp_service._enviar_documento_meta("57300111222", ruta_pdf, "tutela_1.pdf")
            for url in llamadas:
                self.assertIn("v25.0", url, f"debe usar Graph v25.0: {url}")


class TestRegenerateAdminUsaPlantilla(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings.admin_password = "test-password"
        settings.secret_key = "test-key-fijo"

    def test_endpoint_regenera_pdf_desde_datos(self):
        from starlette.testclient import TestClient

        from app.api.admin import SESSION_COOKIE, _crear_sesion
        from app.main import app

        session = SessionLocal()
        try:
            usuario = User(telefono="57300111999", nombre="Ana Ruiz")
            session.add(usuario)
            session.flush()
            tutela = Tutela(user_id=usuario.id, tipo="salud", estado="pdf_generado",
                            datos_json=json.dumps(dict(DATOS_BASE, accionado_nit="800251440-6",
                                                       accionado_email="notif@sanitas.com")))
            session.add(tutela)
            session.commit()
            tutela_id = tutela.id
            user_id = usuario.id
        finally:
            session.close()

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(settings, "storage_dir", tmp), \
             mock.patch.object(settings, "admin_password", "test-password"):
            client = TestClient(app)
            client.__enter__()
            try:
                client.cookies.set(SESSION_COOKIE, _crear_sesion())
                resp = client.post(f"/api/v1/tutelas/{tutela_id}/generar-pdf")
                self.assertEqual(resp.status_code, 200)
                ruta = resp.json()["pdf_path"]
                texto = _texto_pdf(ruta)
                self.assertIn("I. ACCIONANTE", texto)
                self.assertIn("800251440-6", texto)
                self.assertIn("notif@sanitas.com", texto)
                self.assertIn("JUEZ CONSTITUCIONAL DE BOGOTÁ", texto)
                self.assertNotIn("1. [10/01/2026] - Pido cita de medicina general.\n" +
                                 "1. [10/01/2026] - Pido cita de medicina general", texto)
            finally:
                client.__exit__(None, None, None)
                client.close()

        session = SessionLocal()
        try:
            session.query(Tutela).filter(Tutela.id == tutela_id).delete()
            session.query(User).filter(User.id == user_id).delete()
            session.commit()
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()