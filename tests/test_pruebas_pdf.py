"""Tests para el manejo de pruebas PDF.

Los PDFs de soporte NO son material que debamos "interpretar": se fusionan
intactos al final del PDF de la tutela y nunca pasan por la visión de la IA
(que antes rasterizaba las páginas a imagen y, peor, interpretaba el PDF
como si fuera una imagen, "inventando" fotos).

Cubre:
1. recibiendo_pruebas NO llama analizar_imagen para un PDF; sí para una imagen
2. generar_pdf fusiona el PDF de soporte conservando su texto (no rasterizado)
3. una imagen de prueba sigue incrustándose como anexo (regresión de Fase E)
"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

import fitz
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models.tutela import Tutela
from app.models.user import User

from app.api import webhook_whatsapp
from app.services.documento_service import generar_pdf


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    return TestingSession()


def _crear_pdf_texto(ruta: str, texto: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), texto, fontsize=12)
    doc.save(ruta)
    doc.close()


class TestRecibiendoPruebas(unittest.TestCase):
    def _crear_usuario_tutela(self, session, estado, datos):
        user = User(telefono="573001112233", estado="activo", consentimiento=True)
        session.add(user)
        session.flush()
        tutela = Tutela(
            user_id=user.id,
            tipo="salud",
            estado=estado,
            datos_json=json.dumps(datos),
        )
        session.add(tutela)
        session.commit()
        return user, tutela

    async def _procesar(self, session, telefono, body, num_media=1, media_url="https://cdn.x/img1.jpg"):
        with mock.patch.object(webhook_whatsapp, "enviar_texto", return_value=True), \
             mock.patch.object(webhook_whatsapp, "enviar_botones", return_value=True) as mock_b:
            resp = await webhook_whatsapp.procesar_mensaje(
                session, telefono, body, num_media, media_url, False
            )
        return resp, mock_b

    def test_pdf_no_se_analiza_con_vision(self):
        """Un PDF solo se descarga y guarda: analizar_imagen NO debe llamarse."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ruta_pdf = os.path.join(tmp.name, "soporte.pdf")
        _crear_pdf_texto(ruta_pdf, "INFORME MEDICO DE PRUEBA")

        session = _nueva_sesion()
        user, tutela = self._crear_usuario_tutela(
            session, "recibiendo_pruebas", {"tipo": "salud"}
        )

        with mock.patch.object(
            webhook_whatsapp, "_descargar_prueba",
            new=mock.AsyncMock(return_value=ruta_pdf),
        ), mock.patch.object(
            webhook_whatsapp, "analizar_imagen",
            new=mock.AsyncMock(return_value="analisis inventado"),
        ) as mock_ia:
            asyncio.run(self._procesar(session, user.telefono, "", num_media=1,
                                       media_url="https://cdn.x/123"))
        self.assertFalse(mock_ia.await_count, "un PDF no debe pasar por vision")
        tutela_guardada = session.execute(select(Tutela)).scalars().all()[0]
        datos = json.loads(tutela_guardada.datos_json)
        self.assertEqual(datos.get("pruebas_paths"), [ruta_pdf])
        self.assertNotIn("pruebas_analizadas", datos)

    def test_imagen_si_se_analiza_con_vision(self):
        """Las fotos sí pasan por vision para generar la descripcion del anexo."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ruta_img = os.path.join(tmp.name, "foto.jpg")
        with open(ruta_img, "wb") as f:
            f.write(b"JPEG")

        session = _nueva_sesion()
        user, tutela = self._crear_usuario_tutela(
            session, "recibiendo_pruebas", {"tipo": "salud"}
        )

        with mock.patch.object(
            webhook_whatsapp, "_descargar_prueba",
            new=mock.AsyncMock(return_value=ruta_img),
        ), mock.patch.object(
            webhook_whatsapp, "analizar_imagen",
            new=mock.AsyncMock(return_value="Historia clinica visible"),
        ) as mock_ia:
            asyncio.run(self._procesar(session, user.telefono, "", num_media=1,
                                       media_url="https://cdn.x/456"))
        self.assertEqual(mock_ia.await_count, 1)
        tutela_guardada = session.execute(select(Tutela)).scalars().all()[0]
        datos = json.loads(tutela_guardada.datos_json)
        self.assertEqual(datos.get("pruebas_paths"), [ruta_img])
        self.assertEqual(datos.get("pruebas_analizadas"), ["Historia clinica visible"])


class TestGenerarPdfConPdfSoporte(unittest.TestCase):
    def _tmp(self):
        tmp = tempfile.TemporaryDirectory()
        patch_ = mock.patch.object(settings, "storage_dir", tmp.name)
        patch_.start()
        self.addCleanup(patch_.stop)
        self.addCleanup(tmp.cleanup)

    DATOS = {
        "accionante_nombre": "Juan Perez Gomez",
        "accionante_tipo_doc": "CC",
        "accionante_cedula": "1020304050",
        "accionante_telefono": "3001112233",
        "accionante_email": "juan@correo.com",
        "accionante_direccion": "Calle 1 # 2-3, Barrio Centro",
        "ciudad": "Bogotá",
        "departamento": "Cundinamarca",
        "accionado": "Nueva EPS",
        "accionado_nit": "900156264-2",
        "hechos": "1. [10/01/2026] - Pido cita de medicina general.",
    }

    def test_pdf_soporte_se_fusiona_intacto_con_su_texto(self):
        """El texto del PDF original debe ser seleccionable en el PDF final."""
        self._tmp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ruta_pdf = os.path.join(tmp.name, "soporte.pdf")
        _crear_pdf_texto(ruta_pdf, "DICTAMEN MEDICO 2026-8812")

        datos = dict(self.DATOS, pruebas_paths=[ruta_pdf], pruebas_analizadas=[])
        ruta = generar_pdf(datos, None)

        with fitz.open(ruta) as doc:
            texto = "".join(page.get_text() for page in doc)
        self.assertIn("DICTAMEN MEDICO 2026-8812", texto,
                      "el PDF de soporte debe fusionarse conservando su texto")

    def test_pdf_soporte_anade_paginas_sin_rasterizar(self):
        """generar_pdf no debe convertir el PDF de soporte en imagen (no PNG)."""
        self._tmp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ruta_pdf = os.path.join(tmp.name, "soporte.pdf")
        _crear_pdf_texto(ruta_pdf, "TEXTO SELECCIONABLE")

        datos = dict(self.DATOS, pruebas_paths=[ruta_pdf], pruebas_analizadas=[])
        ruta = generar_pdf(datos, None)

        with fitz.open(ruta) as doc:
            self.assertGreaterEqual(doc.page_count, 2)
            todas = "".join(page.get_text() for page in doc)
        self.assertIn("TEXTO SELECCIONABLE", todas)


class TestGenerarPdfConImagen(unittest.TestCase):
    def _tmp(self):
        tmp = tempfile.TemporaryDirectory()
        patch_ = mock.patch.object(settings, "storage_dir", tmp.name)
        patch_.start()
        self.addCleanup(patch_.stop)
        self.addCleanup(tmp.cleanup)

    DATOS = {
        "accionante_nombre": "Juan Perez Gomez",
        "accionante_tipo_doc": "CC",
        "accionante_cedula": "1020304050",
        "accionante_telefono": "3001112233",
        "accionante_email": "juan@correo.com",
        "accionante_direccion": "Calle 1 # 2-3, Barrio Centro",
        "ciudad": "Bogotá",
        "departamento": "Cundinamarca",
        "accionado": "Nueva EPS",
        "accionado_nit": "900156264-2",
        "hechos": "1. [10/01/2026] - Pido cita de medicina general.",
    }

    def test_imagen_sigue_incrustandose_como_anexo(self):
        self._tmp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ruta_img = os.path.join(tmp.name, "foto.png")
        from PIL import Image
        Image.new("RGB", (40, 40), (255, 0, 0)).save(ruta_img)

        datos = dict(self.DATOS, pruebas_paths=[ruta_img], pruebas_analizadas=["Historia"])
        ruta = generar_pdf(datos, None)

        with fitz.open(ruta) as doc:
            self.assertGreaterEqual(doc.page_count, 2)
            texto = "".join(page.get_text() for page in doc)
        self.assertIn("Historia", texto)


class TestLimiteTamanioPrueba(unittest.TestCase):
    def test_prueba_demasiado_grande_recibe_mensaje_con_limite(self):
        """Al rechazar un PDF > límite, el bot dice cuánto es el máximo, no solo
        'No pude descargar' (soporte: PDFs de 7+ MB quedaban sin explicación)."""
        from app.api import webhook_whatsapp

        session = _nueva_sesion()
        user = User(telefono="573001115566", estado="activo", consentimiento=True)
        session.add(user)
        session.flush()
        tutela = Tutela(user_id=user.id, tipo="salud", estado="recibiendo_pruebas", datos_json=json.dumps({"tipo": "salud"}))
        session.add(tutela)
        session.commit()

        async def _rechaza_por_tamanio(url):
            webhook_whatsapp._ultimo_error_descarga = (
                "El archivo pesa más de 15 MB, y WhatsApp no acepta "
                "documentos más grandes. Comprímelo o reduce su tamaño y envíalo de nuevo."
            )
            return None

        async def _procesar_():
            with mock.patch.object(webhook_whatsapp, "enviar_texto", return_value=True), \
                 mock.patch.object(webhook_whatsapp, "enviar_botones", return_value=True), \
                 mock.patch.object(
                     webhook_whatsapp, "_descargar_prueba",
                     new=mock.AsyncMock(side_effect=_rechaza_por_tamanio),
                 ):
                return await webhook_whatsapp.procesar_mensaje(
                    session, user.telefono, "", 1, "https://cdn.x/big.pdf", False
                )

        resp = asyncio.run(_procesar_())

        cuerpo = "\n".join(resp.get("respuestas", []))
        self.assertIn("15 MB", cuerpo, "El mensaje debe decir el límite máximo")
        self.assertIn("envíalo de nuevo", cuerpo)
        tutela_guardada = session.execute(select(Tutela)).scalars().all()[0]
        self.assertNotIn("pruebas_paths", json.loads(tutela_guardada.datos_json))

    def test_descarga_rechaza_archivo_mayor_a_15_megas(self):
        """_descargar_prueba rechaza un archivo > MAX_PRUEBA_BYTES y deja el motivo."""
        from types import SimpleNamespace

        from app.api import webhook_whatsapp

        async def _fake_get(self, url, headers=None, auth=None):
            return SimpleNamespace(
                status_code=200,
                content=b"x" * (webhook_whatsapp.MAX_PRUEBA_BYTES + 1),
                url=url,
                text="",
            )

        class _FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            get = _fake_get

        with mock.patch.object(webhook_whatsapp, "_permite_descargar", return_value=True), \
             mock.patch.object(webhook_whatsapp, "path_prueba", return_value="soporte.pdf"), \
             mock.patch.object(webhook_whatsapp.httpx, "AsyncClient", _FakeClient):
            ruta = asyncio.run(webhook_whatsapp._descargar_prueba("https://px.gob.co/doc.pdf"))

        self.assertIsNone(ruta, "El archivo mayor al límite no debe descargarse")
        self.assertIn("15 MB", webhook_whatsapp._ultimo_error_descarga)