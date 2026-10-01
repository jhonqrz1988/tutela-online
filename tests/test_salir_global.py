"""Comando global SALIR: debe reiniciar el flujo en CUALQUIER estado.

Bug reportado: un usuario se equivocó al escribir su nombre y luego su
apellido, y cuando quiso corregir con "salir" el bot NO se reinició: "salir" se
guardaba como dato del campo en vez de interpretar-se como comando. Con un
nombre/apellido equivocado el flujo quedaba bloqueado y el usuario sin salida.

El bloque original de SALIR estaba DESPUÉS de la lógica de estado, así que solo
se ejecutaba si el flujo hadn't llegado todavía a un punto que lo consumiera.
Este test fija que el comando se evalúa al inicio, antes de cualquier lógica de
estado, en todos los estados del flujo.
"""
import asyncio
import unittest
from unittest import mock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import webhook_whatsapp
from app.database import Base
from app.models.tutela import Tutela
from app.models.user import User


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


class TestSalirGlobal(unittest.TestCase):
    """Reproduce el bug y fija el comportamiento esperado."""

    def setUp(self):
        self.session = _nueva_sesion()
        p1 = mock.patch.object(webhook_whatsapp, "enviar_texto", return_value=True)
        p2 = mock.patch.object(webhook_whatsapp, "enviar_botones", return_value=True)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def _crear_usuario_con_tutela(self, telefono, estado_tutela="recogiendo_datos"):
        user = User(telefono=telefono, estado="activo", consentimiento=True)
        self.session.add(user)
        self.session.commit()
        tutela = Tutela(
            user_id=user.id, tipo="salud", estado=estado_tutela,
            datos_json='{"tipo":"salud","_step":3,"accionante_nombres":"slid"}',
        )
        self.session.add(tutela)
        self.session.commit()
        return user, tutela

    def _enviar(self, telefono, texto):
        return asyncio.run(
            webhook_whatsapp.procesar_mensaje(self.session, telefono, texto, 0, "", False)
        )

    def _tutelas(self):
        return self.session.execute(select(Tutela)).scalars().all()

    def _usuario(self, telefono):
        return self.session.execute(
            select(User).where(User.telefono == telefono)
        ).scalar_one_or_none()

    # ─── El bug reportado ───

    def test_salir_durante_recogiendo_datos_reinicia(self):
        """El caso exacto del bug: nombre equivocado + "salir" no reiniciaba."""
        self._crear_usuario_con_tutela("573000000001", "recogiendo_datos")
        # El usuario está en paso 3 con un nombre basura guardado.
        self._enviar("573000000001", "salir")

        # Ya no debe haber ninguna tutela: se borró todo.
        self.assertEqual(self._tutelas(), [], "salir debe borrar la tutela en curso")
        user = self._usuario("573000000001")
        self.assertEqual(user.estado, "nuevo")
        self.assertFalse(user.consentimiento)

    def test_salir_no_se_guarda_como_dato_de_nombre(self):
        """Con el bug, "salir" se guardaba como nombre y el flujo avanzaba."""
        self._crear_usuario_con_tutela("573000000002", "recogiendo_datos")
        self._enviar("573000000002", "salir")

        # No debe haber ninguna tutela con "salir" guardado en los datos.
        tutelas = self._tutelas()
        for t in tutelas:
            self.assertNotIn('"salir"', t.datos_json or "")

    def test_salir_durante_cualquier_estado_reinicia(self):
        """Debe funcionar en todos los estados, no solo en unos pocos."""
        estados = [
            "recogiendo_datos",
            "confirmar_datos_personales",
            "corrigiendo_datos_personales",
            "narracion",
            "confirmar_audio",
            "revision_datos",
            "preguntas_clinicas",
            "pruebas_pendiente",
            "recibiendo_pruebas",
            "datos_listos",
            "pdf_generado",
            "esperando_decision_radicacion",
            "hazlo_tu_mismo",
            "esperando_pago",
            "pago_por_confirmar",
            "pago_confirmado",
            "pendiente_radicacion",
            "esperando_codigo_email",
        ]
        for estado in estados:
            with self.subTest(estado=estado):
                tel = f"5730000{abs(hash(estado)) % 10000:04d}"
                self.session.query(Tutela).delete()
                self.session.query(User).delete()
                self.session.commit()
                self._crear_usuario_con_tutela(tel, estado)

                self._enviar(tel, "salir")
                self.assertEqual(self._tutelas(), [], f"salir falló en {estado}")
                self.assertEqual(self._usuario(tel).estado, "nuevo")

    def test_salir_con_acentos_y_mayusculas_funciona(self):
        """Usuarios reales escriben con mayúsculas, tildes y espacios extra."""
        self._crear_usuario_con_tutela("573000000003", "recogiendo_datos")
        for variante in ["Salir", "SALIR", " salir ", "Salir ", "salir."]:
            with self.subTest(variante=variante):
                # Recrear estado para cada intento.
                self.session.query(Tutela).delete()
                self.session.query(User).delete()
                self.session.commit()
                self._crear_usuario_con_tutela("573000000003", "recogiendo_datos")
                self._enviar("573000000003", variante)
                self.assertEqual(self._tutelas(), [], f"'{variante}' no reinició")

    def test_sinónimos_de_salir(self):
        sinónimos = ["reiniciar", "empezar de nuevo", "nuevo proceso", "nueva tutela",
                     "cancelar tutela", "dejar la tutela"]
        for sinonimo in sinónimos:
            with self.subTest(sinónimo=sinonimo):
                self.session.query(Tutela).delete()
                self.session.query(User).delete()
                self.session.commit()
                self._crear_usuario_con_tutela("573000000004", "recogiendo_datos")
                self._enviar("573000000004", sinonimo)
                self.assertEqual(self._tutelas(), [], f"'{sinonimo}' no reinició")

    def test_salir_envia_mensaje_de_confirmacion(self):
        """El usuario debe saber que el reinicio funcionó."""
        self._crear_usuario_con_tutela("573000000005", "recogiendo_datos")
        resultado = self._enviar("573000000005", "salir")

        respuestas = " ".join(resultado["respuestas"]).lower()
        self.assertTrue(
            "reinici" in respuestas or "borraron" in respuestas or "empezar de cero" in respuestas,
            "Debe confirmar que el flujo se reinició",
        )

    def test_salir_preserva_otros_usuarios(self):
        """Reiniciar un usuario no debe tocar a otro."""
        self._crear_usuario_con_tutela("573000000006", "recogiendo_datos")
        # Segundo usuario, también con tutela activa.
        user2 = User(telefono="573000000007", estado="activo", consentimiento=True)
        self.session.add(user2)
        self.session.commit()
        tutela2 = Tutela(user_id=user2.id, tipo="salud", estado="narracion", datos_json="{}")
        self.session.add(tutela2)
        self.session.commit()

        self._enviar("573000000006", "salir")

        # Usuario 1 reiniciado.
        self.assertEqual(self._tutelas(), [tutela2], "solo debe borrar la tutela del usuario 1")
        # Usuario 2 intacto.
        user2_reload = self._usuario("573000000007")
        self.assertEqual(user2_reload.estado, "activo")


class TestNormalizarComando(unittest.TestCase):
    """Los usuarios escriben con mayúsculas, tildes y signos de puntuación."""

    def test_normaliza_variantes(self):
        casos = {
            "Salir": "salir",
            "SALIR": "salir",
            " salir ": "salir",
            "salir.": "salir",
            "Salir!": "salir",
            "sálir": "salir",
            "SALIR!!!": "salir",
            "¿Salir?": "salir",
            "salir,": "salir",
        }
        for entrada, esperado in casos.items():
            with self.subTest(entrada=entrada):
                self.assertEqual(
                    webhook_whatsapp._normalizar_texto_entrada(entrada), esperado
                )

    def test_es_comando_salir_acepta_variantes(self):
        for entrada in ["salir", "Salir", " SALIR ", "sálir", "salir."]:
            with self.subTest(entrada=entrada):
                self.assertTrue(webhook_whatsapp._es_comando_salir(entrada))

    def test_no_confunde_frases_con_el_comando(self):
        """'quiero salir de aquí' NO debe borrar los datos de la persona."""
        frases = [
            "quiero salir de aquí",
            "salir del trabajo",
            "no quiero salir",
            "mi apellido es salir",
            "salazar",
            "salirme",
        ]
        for frase in frases:
            with self.subTest(frase=frase):
                self.assertFalse(
                    webhook_whatsapp._es_comando_salir(frase),
                    f"'{frase}' no debe interpretarse como comando de salida",
                )

    def test_texto_vacio_no_es_comando(self):
        for vacio in ["", "   ", None]:
            with self.subTest(vacio=vacio):
                self.assertFalse(webhook_whatsapp._es_comando_salir(vacio))


class TestBotonSalirEnConsentimiento(unittest.TestCase):
    """El botón 'Salir' del aviso de privacidad debe reiniciar el flujo."""

    def setUp(self):
        self.session = _nueva_sesion()
        p1 = mock.patch.object(webhook_whatsapp, "enviar_texto", return_value=True)
        p2 = mock.patch.object(webhook_whatsapp, "enviar_botones", return_value=True)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def _enviar_boton(self, telefono, boton_id):
        msg = {
            "from": telefono,
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": boton_id, "title": "Salir"},
            },
        }
        datos = webhook_whatsapp._parsear_mensaje(msg)
        return asyncio.run(
            webhook_whatsapp.procesar_mensaje(
                self.session, telefono, datos["body_text"], datos["num_media"],
                datos["media_url"], datos["es_audio"],
            )
        )

    def test_el_aviso_de_privacidad_ofrece_el_boton_salir(self):
        """El mensaje de consentimiento debe incluir la salida, no solo aceptar/rechazar."""
        botonones = self._botones_enviados("573000000010")

        assert botonones, "no se envió ningún mensaje con botones"
        ids = [bid for lista in botonones for bid, _ in lista]
        self.assertIn("salir", ids, "el aviso de privacidad debe ofrecer 'Salir'")
        self.assertIn("acepto", ids)

    def test_pulsar_el_boton_salir_reinicia(self):
        user = User(telefono="573000000011", estado="activo", consentimiento=True)
        self.session.add(user)
        self.session.commit()
        tutela = Tutela(
            user_id=user.id, tipo="salud", estado="recogiendo_datos",
            datos_json='{"tipo":"salud","_step":2}',
        )
        self.session.add(tutela)
        self.session.commit()

        self._enviar_boton("573000000011", "salir")

        restantes = self.session.execute(select(Tutela)).scalars().all()
        self.assertEqual(restantes, [], "el botón Salir debe borrar la tutela")
        self.assertEqual(
            self.session.execute(
                select(User).where(User.telefono == "573000000011")
            ).scalar_one().estado,
            "nuevo",
        )

    def _botones_enviados(self, telefono):
        """Captura la lista de botones de cada envío durante el flujo."""
        enviados = []

        def _captura(_telefono, _texto, botones):
            enviados.append(botones)
            return True

        with mock.patch.object(webhook_whatsapp, "enviar_botones", side_effect=_captura), \
                mock.patch.object(webhook_whatsapp, "enviar_texto", return_value=True):
            asyncio.run(
                webhook_whatsapp.procesar_mensaje(
                    self.session, telefono, "Hola", 0, "", False
                )
            )
        return enviados


if __name__ == "__main__":
    unittest.main()