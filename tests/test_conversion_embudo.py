"""Tests de regresión para los fixes de conversión y bugs de producción."""
import asyncio
import datetime
import json
import re
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.api import clics as clics_api
from app.api import tiktok as tiktok_api
from app.api import webhook_whatsapp
from app.config import settings
from app.database import Base
from app.main import app
from app.models.tutela import Tutela
from app.models.user import User
from app.models.visita import VisitaLanding
from app.services import ia_service, visitas_service


def _nueva_sesion_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


class TestLandingUserAgentYTracking(unittest.TestCase):
    def setUp(self):
        self.engine, self.S = _nueva_sesion_factory()
        clics_api._LIMIT_POR_IP.clear()
        tiktok_api._LIMIT_POR_IP.clear()

    def tearDown(self):
        clics_api._LIMIT_POR_IP.clear()
        tiktok_api._LIMIT_POR_IP.clear()
        self.engine.dispose()

    def test_get_landing_marca_crawler_meta_como_bot(self):
        with mock.patch.object(visitas_service, "SessionLocal", self.S), TestClient(app) as client:
            resp = client.get(
                "/?utm_source=fb&fbclid=test123",
                headers={"User-Agent": "facebookexternalhit/1.1"},
            )
        self.assertEqual(resp.status_code, 200)
        session = self.S()
        try:
            visita = session.execute(select(VisitaLanding)).scalar_one()
            self.assertTrue(visita.es_bot)
            self.assertIn("facebookexternalhit", visita.user_agent or "")
        finally:
            session.close()

    def test_get_landing_marca_navegador_movil_como_humano_clasificado(self):
        ua_movil = (
            "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
            "(KHTML, like Gecko) FBAV/438.0.0.22.106 Mobile Safari/537.36"
        )
        with mock.patch.object(visitas_service, "SessionLocal", self.S), TestClient(app) as client:
            resp = client.get(
                "/?utm_source=meta&fbclid=human123",
                headers={"User-Agent": ua_movil},
            )
        self.assertEqual(resp.status_code, 200)
        session = self.S()
        try:
            visita = session.execute(select(VisitaLanding)).scalar_one()
            self.assertFalse(visita.es_bot)
            self.assertEqual(visita.user_agent, ua_movil[:500])

            ahora = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
            clasificadas = visitas_service.visitas_clasificadas(
                session,
                ahora - datetime.timedelta(hours=1),
                ahora + datetime.timedelta(hours=1),
            )
            self.assertGreater(clasificadas, 0)
        finally:
            session.close()

    def test_click_wa_respeta_x_forwarded_for_para_rate_limit(self):
        with mock.patch.object(visitas_service, "SessionLocal", self.S), TestClient(app) as client:
            for _ in range(clics_api._LIMITE_POR_MINUTO):
                r = client.post(
                    "/api/click-wa",
                    json={"query": "", "ubicacion": "btn-hero"},
                    headers={"X-Forwarded-For": "198.51.100.10, 10.0.0.1"},
                )
                self.assertEqual(r.status_code, 200)

            r_bloqueado = client.post(
                "/api/click-wa",
                json={"query": "", "ubicacion": "btn-hero"},
                headers={"X-Forwarded-For": "198.51.100.10, 10.0.0.1"},
            )
            self.assertEqual(r_bloqueado.status_code, 429)

            r_otro_usuario = client.post(
                "/api/click-wa",
                json={"query": "", "ubicacion": "btn-hero"},
                headers={"X-Forwarded-For": "198.51.100.20, 10.0.0.1"},
            )
            self.assertEqual(r_otro_usuario.status_code, 200)

    def test_tiktok_track_respeta_x_forwarded_for_para_rate_limit(self):
        with TestClient(app) as client:
            for i in range(tiktok_api._LIMITE_POR_MINUTO):
                r = client.post(
                    "/api/tiktok/track",
                    json={"event": "Contact", "event_id": f"ev-{i}"},
                    headers={"X-Forwarded-For": "203.0.113.5, 10.0.0.2"},
                )
                self.assertEqual(r.status_code, 200)

            r_bloqueado = client.post(
                "/api/tiktok/track",
                json={"event": "Contact", "event_id": "ev-extra"},
                headers={"X-Forwarded-For": "203.0.113.5, 10.0.0.2"},
            )
            self.assertEqual(r_bloqueado.status_code, 429)

            r_otro_usuario = client.post(
                "/api/tiktok/track",
                json={"event": "Contact", "event_id": "ev-otro"},
                headers={"X-Forwarded-For": "203.0.113.6, 10.0.0.2"},
            )
            self.assertEqual(r_otro_usuario.status_code, 200)

    def test_landing_html_tiene_text_en_todos_los_enlaces_wa_y_lead_pixel(self):
        html = Path("app/templates/landing.html").read_text(encoding="utf-8")
        enlaces = re.findall(r'href="(https://wa\.me/573106386975[^"]*)"', html)
        self.assertEqual(len(enlaces), 7)
        for url in enlaces:
            self.assertIn("?text=", url, f"Falta ?text= pre-llenado en {url}")
        self.assertIn("fbq('track', 'Lead'", html)


class TestModeloWhisperGroq(unittest.TestCase):
    def setUp(self):
        self.orig_provider = settings.ai_provider
        self.orig_whisper = settings.ai_whisper_model

    def tearDown(self):
        settings.ai_provider = self.orig_provider
        settings.ai_whisper_model = self.orig_whisper

    def test_groq_con_default_openai_resuelve_whisper_large_v3_turbo(self):
        settings.ai_provider = "groq"
        settings.ai_whisper_model = "whisper-1"
        self.assertEqual(ia_service._modelo_whisper(), "whisper-large-v3-turbo")

    def test_openai_conserva_whisper_1(self):
        settings.ai_provider = "openai"
        settings.ai_whisper_model = "whisper-1"
        self.assertEqual(ia_service._modelo_whisper(), "whisper-1")

    def test_groq_con_modelo_explicito_lo_respeta(self):
        settings.ai_provider = "groq"
        settings.ai_whisper_model = "whisper-large-v3"
        self.assertEqual(ia_service._modelo_whisper(), "whisper-large-v3")


class TestFlujoConversionWhatsApp(unittest.TestCase):
    def setUp(self):
        self.engine, self.S = _nueva_sesion_factory()
        self.session = self.S()
        self.enviados: list[str] = []
        self.p_txt = mock.patch.object(
            webhook_whatsapp,
            "enviar_texto",
            side_effect=lambda tel, msg, *a, **kw: self.enviados.append(msg) or True,
        )
        self.p_btn = mock.patch.object(
            webhook_whatsapp,
            "enviar_botones",
            side_effect=lambda tel, txt, btns, *a, **kw: self.enviados.append(f"[BOTONES] {txt} {btns}") or True,
        )
        self.p_txt.start()
        self.p_btn.start()

    def tearDown(self):
        self.p_txt.stop()
        self.p_btn.stop()
        self.session.close()
        self.engine.dispose()

    def _enviar(self, telefono: str, body: str, num_media: int = 0, media_url: str = "", es_audio: bool = False):
        return asyncio.run(
            webhook_whatsapp.procesar_mensaje(
                self.session, telefono, body, num_media, media_url, es_audio
            )
        )

    def test_consentimiento_acepta_variantes_naturales_y_puntuacion(self):
        variantes = ["Sí, acepto", "Acepto.", "Autorizo", "Claro", "1"]
        for i, entrada in enumerate(variantes):
            with self.subTest(entrada=entrada):
                tel = f"5730011100{i:02d}"
                self._enviar(tel, "Hola, necesito hacer una tutela de salud")
                resp = self._enviar(tel, entrada)
                user = self.session.execute(
                    select(User).where(User.telefono == tel)
                ).scalar_one()
                self.assertTrue(user.consentimiento, f"No aceptó con {entrada!r}")
                self.assertEqual(user.estado, "activo")
                tutela = self.session.execute(
                    select(Tutela).where(Tutela.user_id == user.id)
                ).scalar_one()
                self.assertEqual(tutela.estado, "recogiendo_datos")
                self.assertTrue(any("Consentimiento registrado" in r for r in resp["respuestas"]))

    def test_usuario_rechazado_se_desbloquea_con_hola_y_puede_aceptar(self):
        tel = "573001119901"
        self._enviar(tel, "Hola")
        self._enviar(tel, "no")
        user = self.session.execute(select(User).where(User.telefono == tel)).scalar_one()
        self.assertEqual(user.estado, "rechazado")

        resp_hola = self._enviar(tel, "Hola")
        self.session.refresh(user)
        self.assertEqual(user.estado, "nuevo")
        self.assertTrue(any("Aviso de Tratamiento" in r for r in resp_hola["respuestas"]))

        self._enviar(tel, "Sí, acepto")
        self.session.refresh(user)
        self.assertTrue(user.consentimiento)
        self.assertEqual(user.estado, "activo")
        tutela = self.session.execute(
            select(Tutela).where(Tutela.user_id == user.id)
        ).scalar_one()
        self.assertEqual(tutela.estado, "recogiendo_datos")

    def test_continuar_mi_tutela_retoma_todos_los_estados_clave(self):
        casos = [
            (
                "confirmar_datos_personales",
                {"accionante_nombres": "Ana", "accionante_apellidos": "Gómez"},
                "Tus datos personales",
            ),
            (
                "preguntas_clinicas",
                {"_step_clinico": 2},
                webhook_whatsapp.DATOS_CLINICOS_STEPS[1][1],
            ),
            (
                "esperando_decision_radicacion",
                {},
                "PDF generado y enviado",
            ),
            (
                "esperando_pago",
                {},
                "/pago/",
            ),
        ]
        for idx, (estado, datos, texto_esperado) in enumerate(casos):
            with self.subTest(estado=estado):
                tel = f"5730022200{idx:02d}"
                user = User(telefono=tel, estado="activo", consentimiento=True)
                self.session.add(user)
                self.session.flush()
                tutela = Tutela(
                    user_id=user.id,
                    tipo="salud",
                    estado=estado,
                    datos_json=json.dumps(datos),
                )
                self.session.add(tutela)
                self.session.commit()

                resp = self._enviar(tel, "Continuar mi tutela")
                cuerpo = "\n".join(resp["respuestas"])
                self.assertIn(texto_esperado, cuerpo)

    def test_narracion_sin_texto_o_con_imagen_sola_no_llama_ia(self):
        tel = "573003330001"
        user = User(telefono=tel, estado="activo", consentimiento=True)
        self.session.add(user)
        self.session.flush()
        tutela = Tutela(
            user_id=user.id,
            tipo="salud",
            estado="narracion",
            datos_json=json.dumps({"tipo": "salud"}),
        )
        self.session.add(tutela)
        self.session.commit()

        with mock.patch.object(
            webhook_whatsapp, "extraer_datos_caso", new=mock.AsyncMock()
        ) as mock_ia, mock.patch.object(
            webhook_whatsapp, "_descargar_prueba", new=mock.AsyncMock(return_value="/tmp/soporte.jpg")
        ):
            resp_img = self._enviar(tel, "", num_media=1, media_url="MEDIA123", es_audio=False)
            resp_vacio = self._enviar(tel, "   ")

        mock_ia.assert_not_called()
        self.assertIn("Recibí tu archivo", "\n".join(resp_img["respuestas"]))
        self.assertIn("Cuéntame tu caso", "\n".join(resp_vacio["respuestas"]))
        self.session.refresh(tutela)
        datos = json.loads(tutela.datos_json)
        self.assertEqual(datos.get("pruebas_paths"), ["/tmp/soporte.jpg"])

    def test_datos_listos_si_juro_con_coma_genera_pdf(self):
        tel = "573004440001"
        user = User(telefono=tel, estado="activo", consentimiento=True)
        self.session.add(user)
        self.session.flush()
        tutela = Tutela(
            user_id=user.id,
            tipo="salud",
            estado="datos_listos",
            datos_json=json.dumps({
                "tipo": "salud",
                "accionante_nombre": "Carlos Ruiz",
                "accionante_cedula": "10203040",
                "accionado": "EPS Sanitas",
                "hechos": "Negaron medicamento",
            }),
        )
        self.session.add(tutela)
        self.session.commit()

        with mock.patch.object(
            webhook_whatsapp, "generar_tutela", new=mock.AsyncMock(return_value="Texto tutela")
        ), mock.patch.object(
            webhook_whatsapp, "extraer_citas", new=mock.AsyncMock(return_value=[])
        ), mock.patch.object(
            webhook_whatsapp, "generar_pdf", return_value="storage/10203040.pdf"
        ) as mock_pdf, mock.patch.object(
            webhook_whatsapp, "enviar_documento", return_value=True
        ):
            resp = self._enviar(tel, "Sí, juro.")

        mock_pdf.assert_called_once()
        self.session.refresh(tutela)
        self.assertEqual(tutela.estado, "esperando_decision_radicacion")
        self.assertTrue(any("Tutela generada" in r for r in resp["respuestas"]))

    def test_esperando_pago_reenvia_link_si_escribe_otra_cosa(self):
        tel = "573005550001"
        user = User(telefono=tel, estado="activo", consentimiento=True)
        self.session.add(user)
        self.session.flush()
        tutela = Tutela(
            user_id=user.id,
            tipo="salud",
            estado="esperando_pago",
            datos_json="{}",
        )
        self.session.add(tutela)
        self.session.commit()

        resp = self._enviar(tel, "No encuentro el link para pagar")
        cuerpo = "\n".join(resp["respuestas"])
        self.assertIn(f"/pago/{tutela.id}?t=", cuerpo)
        self.assertIn("Pagado", cuerpo)


if __name__ == "__main__":
    unittest.main()
