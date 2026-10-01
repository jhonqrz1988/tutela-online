"""Regresión del consentimiento vía BOTONES: el bug de "el flujo no inicia".

Síntoma reportado por usuarios (probado en iPhone): escriben al número del anuncio,
reciben la bienvenida y el aviso de privacidad, pulsan "Sí, acepto" y el bot nunca
responde → quedan bloqueados sin poder avanzar.

Causa raíz: el clic en un botón que envía el bot llega como
``interactive.button_reply``, NO como texto. Si ese payload no se normalizaba a un
``body`` con el id del botón ("acepto"), el ``body`` quedaba vacío, no coincidía con
ninguna opción de la máquina de estados y el bot caía en el fallback que REENVÍA el
aviso de privacidad (bucle infinito) sin registrar el consentimiento.

Estos tests ejercitan la máquina de estados REAL (``procesar_mensaje``) alimentada
con el payload crudo que Meta postea a ``/webhook/meta``, no solo el parser aislado.
"""
import asyncio
import unittest
from unittest import mock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import MensajeWhatsApp

from app.api import webhook_whatsapp

TELEFONO = "573009999999"


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _payload_meta(mensaje: dict) -> dict:
    """Arma el payload completo que Meta envía a /webhook/meta."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA_ID",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "573106386975",
                        "phone_number_id": "1185525421321347",
                    },
                    "contacts": [{"profile": {"name": "Prueba"}, "wa_id": TELEFONO}],
                    "messages": [mensaje],
                },
            }],
        }],
    }


def _mensaje_del_payload(payload: dict) -> dict:
    return payload["entry"][0]["changes"][0]["value"]["messages"][0]


class _BaseConsentimiento(unittest.TestCase):
    def setUp(self):
        self.session = _nueva_sesion()
        self.enviados: list[str] = []
        self.ok_texto = True
        self.ok_botones = True
        p_txt = mock.patch.object(
            webhook_whatsapp, "enviar_texto", side_effect=self._fake_texto
        )
        p_btn = mock.patch.object(
            webhook_whatsapp, "enviar_botones", side_effect=self._fake_botones
        )
        p_txt.start()
        p_btn.start()
        self.addCleanup(p_txt.stop)
        self.addCleanup(p_btn.stop)

    def _fake_texto(self, telefono, mensaje, *args, **kwargs):
        self.enviados.append(mensaje)
        return self.ok_texto

    def _fake_botones(self, telefono, texto, botones, *args, **kwargs):
        self.enviados.append(f"[BOTONES] {texto} {botones}")
        return self.ok_botones

    def _procesar(self, mensaje_meta: dict) -> dict:
        """Simula el webhook completo: payload crudo -> parser -> máquina de estados."""
        msg = _mensaje_del_payload(_payload_meta(mensaje_meta))
        parsed = webhook_whatsapp._parsear_mensaje(msg)
        return asyncio.run(
            webhook_whatsapp.procesar_mensaje(
                self.session,
                TELEFONO,
                parsed["body_text"],
                parsed["num_media"],
                parsed["media_url"],
                parsed["es_audio"],
            )
        )

    def _usuario(self) -> User:
        return self.session.execute(
            select(User).where(User.telefono == TELEFONO)
        ).scalar_one()

    def _tutela(self) -> Tutela:
        user = self._usuario()
        return self.session.execute(
            select(Tutela).where(Tutela.user_id == user.id)
        ).scalar_one()

    def _ultimo_mensaje(self) -> MensajeWhatsApp:
        self.session.expire_all()
        return self.session.execute(
            select(MensajeWhatsApp)
            .where(MensajeWhatsApp.from_number == TELEFONO)
            .order_by(MensajeWhatsApp.id.desc())
        ).scalars().first()


class TestConsentimientoConBotones(_BaseConsentimiento):
    def test_primer_mensaje_manda_bienvenida_y_aviso(self):
        self._procesar({"type": "text", "text": {"body": "Hola"}})

        user = self._usuario()
        self.assertEqual(user.estado, "nuevo")
        self.assertFalse(user.consentimiento)
        self.assertEqual(len(self.enviados), 1)
        self.assertTrue(self.enviados[0].startswith("[BOTONES]"))

    def test_boton_si_acepto_registra_consentimiento_y_crea_tutela(self):
        """EL BUG: pulsar el botón debe avanzar el flujo, no reenviar el aviso."""
        self._procesar({"type": "text", "text": {"body": "Hola"}})

        resultado = self._procesar({
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "acepto", "title": "✅ Sí, acepto"},
            },
        })

        user = self._usuario()
        self.assertTrue(user.consentimiento, "el clic en el botón no registró consentimiento")
        self.assertEqual(user.estado, "activo")
        self.assertIsNotNone(user.consentimiento_timestamp)

        tutela = self._tutela()
        self.assertEqual(tutela.estado, "recogiendo_datos")

        respuestas = resultado["respuestas"]
        self.assertTrue(
            any("Consentimiento registrado" in r for r in respuestas),
            f"no confirmó el consentimiento: {respuestas}",
        )
        # Debe preguntar el primer dato personal (no reenviar el aviso).
        self.assertFalse(
            any(r.startswith("[BOTONES]") for r in respuestas),
            "el bot se quedó reenviando el aviso de privacidad en bucle",
        )
        _, primera_pregunta = webhook_whatsapp.DATOS_PERSONALES_STEPS[0]
        cuerpo = "\n".join(respuestas)
        self.assertIn(primera_pregunta, cuerpo)

    def test_boton_no_deja_usuario_rechazado(self):
        self._procesar({"type": "text", "text": {"body": "Hola"}})

        self._procesar({
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "no", "title": "❌ No acepto"},
            },
        })

        self.assertEqual(self._usuario().estado, "rechazado")

    def test_texto_plano_acepto_tambien_avanza(self):
        """El texto plano 'acepto' también debe avanzar (no solo el botón)."""
        self._procesar({"type": "text", "text": {"body": "Hola"}})
        self._procesar({"type": "text", "text": {"body": "acepto"}})

        self.assertTrue(self._usuario().consentimiento)
        self.assertEqual(self._tutela().estado, "recogiendo_datos")

    def test_cuerpo_no_reconocido_reenvia_el_aviso(self):
        """Documenta el síntoma del bug: sin 'body' útil el bot repite el aviso.

        Si este test empieza a fallar tras tocar el parser, el parser volvió a
        dejar cuerpos vacíos y el usuario queda trabado.
        """
        self._procesar({"type": "text", "text": {"body": "Hola"}})
        antes = len(self.enviados)

        resultado = self._procesar({"type": "text", "text": {"body": "xyz"}})

        self.assertFalse(self._usuario().consentimiento)
        self.assertEqual(len(self.enviados) - antes, 1)
        self.assertTrue(resultado["respuestas"][-1].startswith("[BOTONES]"))


class TestTiposNoMudanElFlujo(_BaseConsentimiento):
    def test_todos_los_tipos_dejan_al_bot_algo_con_que_responder(self):
        """Ningún mensaje puede dejar al bot sin nada con qué responder.

        Para los tipos multimedia (``body_text`` vacío es correcto) lo que
        importa es que ``num_media`` avise del adjunto; el resto debe traer texto
        para que el flujo pueda compararlo contra sus opciones.
        """
        muestras = {
            "texto": ({"type": "text", "text": {"body": "Hola"}}, False),
            "boton_nuestro": ({
                "type": "interactive",
                "interactive": {"type": "button_reply",
                                "button_reply": {"id": "acepto", "title": "Sí"}},
            }, False),
            "cta_anuncio": ({
                "type": "interactive",
                "interactive": {"type": "button", "button": {"text": "Quiero info"}},
            }, False),
            "formulario": ({
                "type": "interactive",
                "interactive": {"type": "nfm_reply",
                                "nfm_reply": {"name": "form", "response_json": "si"}},
            }, False),
            "reaction": ({"type": "reaction", "reaction": {"emoji": "\U0001f44d"}}, False),
            "sticker": ({"type": "sticker", "sticker": {}}, False),
            "ubicacion": ({"type": "location",
                           "location": {"latitude": 1.0, "longitude": 2.0}}, False),
            "contacto": ({"type": "contacts", "contacts": []}, False),
            "desconocido": ({"type": "tipo_futuro", "x": 1}, False),
            # Multimedia: sin texto, pero con adjunto detectado.
            "imagen": ({"type": "image", "image": {"id": "IMG", "mime_type": "image/jpeg"}}, True),
            "documento": ({"type": "document", "document": {"id": "DOC"}}, True),
            "video": ({"type": "video", "video": {"id": "VID"}}, True),
        }
        for nombre, (mensaje, es_multimedia) in muestras.items():
            with self.subTest(tipo=nombre):
                parsed = webhook_whatsapp._parsear_mensaje(mensaje)
                tiene_texto = parsed["body_text"].strip() != ""
                self.assertTrue(
                    tiene_texto or parsed["num_media"] > 0,
                    f"el tipo {nombre} no deja texto ni adjunto: {parsed}",
                )
                if es_multimedia:
                    self.assertEqual(parsed["num_media"], 1,
                                     f"{nombre} debe contar como adjunto")

    def test_cta_de_anuncio_llega_como_texto_y_el_bot_responde(self):
        resultado = self._procesar({
            "type": "interactive",
            "interactive": {"type": "button", "button": {"text": "Quiero una tutela"}},
        })
        self.assertTrue(resultado["ok"])
        self.assertTrue(self.enviados)


class TestEstadoDeEnvioEnFlujo(_BaseConsentimiento):
    """`envio_estado` visto desde el flujo, no desde la función aislada.

    Semántica de ``_estado_envio``:
      - ``entregado``     el bot respondió y Meta aceptó el envío.
      - ``fallido``        el bot respondió pero Meta rechazó el envío.
      - ``sin_respuesta`` el flujo no emitió NINGÚN envío (bot atascado).
    """

    def test_envio_exitoso_queda_marcado_entregado(self):
        self._procesar({"type": "text", "text": {"body": "Hola"}})
        self.assertEqual(self._ultimo_mensaje().envio_estado, "entregado")

    def test_rechazo_de_meta_queda_fallido(self):
        """Es el caso que cuenta la tarjeta: el bot respondió y Meta lo rechazó."""
        self.ok_texto = False
        self.ok_botones = False
        self._procesar({"type": "text", "text": {"body": "Hola"}})
        self.assertEqual(self._ultimo_mensaje().envio_estado, "fallido")

    def test_un_solo_envio_fallido_marca_el_mensaje_como_fallido(self):
        """Con un envío bueno y otro malo, el mensaje igual queda `fallido`."""
        self.ok_texto = True
        self.ok_botones = False
        self._procesar({"type": "text", "text": {"body": "Hola"}})
        self.assertEqual(self._ultimo_mensaje().envio_estado, "fallido")

    def test_flujo_que_no_emite_nada_queda_sin_respuesta(self):
        """Señal de atasco: el bot no generó ninguna respuesta para el usuario."""
        with mock.patch.object(webhook_whatsapp, "_r", return_value=True), \
                mock.patch.object(webhook_whatsapp, "_b", return_value=True):
            self._procesar({"type": "text", "text": {"body": "Hola"}})
        self.assertEqual(self._ultimo_mensaje().envio_estado, "sin_respuesta")


if __name__ == "__main__":
    unittest.main()