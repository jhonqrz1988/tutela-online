"""Reporte de entrega: quién escribió y nunca recibió respuesta.

Cubre dos Things que importan en producción:
1. Clasifica bien a quien sí recibió, a quien Meta le rechazó y a quien no hay
   rastro de ninguna salida.
2. NO marca como "sin respuesta" a quien escribió antes de que existiera el
   seguimiento de entregas (daría un informe falso y alarmista).
"""
import datetime
import unittest

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.tutela import Tutela
from app.models.user import User
from app.models.whatsapp import EnvioWhatsApp
from app.services import entrega_service

AHORA = datetime.datetime.utcnow()


def _nueva_sesion():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _msg(session, **cambios):
    """Inserta un mensaje entrante con `created_at` explícito y reciente por defecto."""
    valores = {
        "from_number": "573000000000",
        "body": "hola",
        "tipo_mensaje": "texto",
        "es_recibido": True,
        "created_at": AHORA,
    }
    valores.update(cambios)
    columnas = ", ".join(valores)
    marcadores = ", ".join(f":{k}" for k in valores)
    session.execute(
        text(f"INSERT INTO mensajes_whatsapp ({columnas}) VALUES ({marcadores})"), valores
    )
    session.commit()


class TestReporteEntrega(unittest.TestCase):
    def setUp(self):
        self.session = _nueva_sesion()

    def test_numero_recibiendo_respuesta_confirmada(self):
        _msg(self.session, from_number="573111111111")
        self.session.add(EnvioWhatsApp(
            wamid="wamid.a", from_number="573111111111", estado="entregado",
        ))
        self.session.commit()

        r = entrega_service.reporte_entrega(self.session)
        self.assertEqual(r["resumen"]["numeros_que_escribieron"], 1)
        self.assertEqual(r["resumen"]["con_respuesta_confirmada"], 1)
        self.assertEqual(r["resumen"]["sin_respuesta"], 0)
        self.assertEqual(r["sin_respuesta"], [])

    def test_numero_sin_ninguna_salida(self):
        _msg(self.session, from_number="573222222222")

        r = entrega_service.reporte_entrega(self.session)
        self.assertEqual(r["resumen"]["sin_respuesta"], 1)
        self.assertEqual(r["sin_respuesta"][0]["telefono"], "573222222222")

    def test_numero_con_envio_fallido_muestra_motivo(self):
        _msg(self.session, from_number="573333333333")
        self.session.add(EnvioWhatsApp(
            wamid="wamid.b", from_number="573333333333", estado="fallido",
            error_code=131026, error_detalle="not deliverable",
        ))
        self.session.commit()

        r = entrega_service.reporte_entrega(self.session)
        self.assertEqual(r["resumen"]["con_envio_fallido"], 1)
        fallido = r["fallidos"][0]
        self.assertEqual(fallido["telefono"], "573333333333")
        self.assertEqual(fallido["codigo"], 131026)
        self.assertIn("WhatsApp", fallido["motivo"])
        # Un fallo NO debe contar como "sin respuesta": sí hubo respuesta nuestra.
        self.assertEqual(r["resumen"]["sin_respuesta"], 0)

    def test_mensajes_antiguos_no_se_reportan_sin_respuesta(self):
        """Sin seguimiento previo no hay dato de entrega: no es 'sin respuesta'."""
        _msg(self.session, from_number="573444444444",
             created_at=AHORA - datetime.timedelta(days=200))

        r = entrega_service.reporte_entrega(self.session, dias=7)
        self.assertEqual(r["resumen"]["numeros_que_escribieron"], 0)
        self.assertEqual(r["resumen"]["sin_respuesta"], 0)
        self.assertEqual(r["sin_respuesta"], [])
        self.assertIn("despliegue", r["nota"])

    def test_basura_del_bug_se_detecta_y_se_excluye_del_reporte(self):
        _msg(self.session, from_number="")
        self.session.add(User(telefono=""))
        self.session.commit()
        self.session.add(Tutela(user_id=1, tipo="salud", estado="borrador", datos_json="{}"))
        self.session.commit()

        r = entrega_service.reporte_entrega(self.session)
        # No debe contaminar la lista de números sin respuesta.
        self.assertEqual(r["resumen"]["sin_respuesta"], 0)
        self.assertEqual(r["resumen"]["numeros_que_escribieron"], 0)
        # Pero sí debe reportarse como basura para poder limpiarla.
        self.assertEqual(r["basura_del_bug"]["usuarios_sin_numero"], 1)
        self.assertEqual(r["basura_del_bug"]["tutelas_sin_numero"], 1)

    def test_espacios_en_telefono_tambien_cuentan_como_basura(self):
        self.session.add(User(telefono="   "))
        self.session.commit()
        r = entrega_service.reporte_entrega(self.session)
        self.assertEqual(r["basura_del_bug"]["usuarios_sin_numero"], 1)

    def test_resumen_de_errores_agrupado(self):
        for i in range(3):
            self.session.add(EnvioWhatsApp(
                wamid=f"wamid.e{i}", from_number=f"57355555555{i}",
                estado="fallido", error_code=131047,
            ))
        self.session.commit()

        r = entrega_service.reporte_entrega(self.session)
        self.assertEqual(r["errores_meta"][0]["codigo"], 131047)
        self.assertEqual(r["errores_meta"][0]["veces"], 3)
        self.assertIn("24 h", r["errores_meta"][0]["motivo"])

    def test_estados_globales(self):
        for i, estado in enumerate(["entregado", "leido", "fallido", "entregado"]):
            self.session.add(EnvioWhatsApp(
                wamid=f"wamid.g{i}", from_number=f"57366666666{i}", estado=estado,
            ))
        self.session.commit()

        r = entrega_service.reporte_entrega(self.session)
        self.assertEqual(r["estados_envio"]["entregado"], 2)
        self.assertEqual(r["estados_envio"]["leido"], 1)
        self.assertEqual(r["estados_envio"]["fallido"], 1)

    def test_varios_mensajes_del_mismo_numero_se_agrupan(self):
        _msg(self.session, from_number="573777777777", created_at=AHORA)
        _msg(self.session, from_number="573777777777", created_at=AHORA)

        r = entrega_service.reporte_entrega(self.session)
        self.assertEqual(r["resumen"]["numeros_que_escribieron"], 1)
        self.assertEqual(r["sin_respuesta"][0]["mensajes"], 2)

    def test_dias_se_acotan(self):
        for dias in (0, -5, 5000):
            with self.subTest(dias=dias):
                r = entrega_service.reporte_entrega(self.session, dias=dias)
                self.assertGreaterEqual(r["ventana_dias"], 1)
                self.assertLessEqual(r["ventana_dias"], 90)

    def test_base_vacia_no_revienta(self):
        r = entrega_service.reporte_entrega(self.session)
        self.assertEqual(r["resumen"]["numeros_que_escribieron"], 0)
        self.assertEqual(r["sin_respuesta"], [])
        self.assertEqual(r["fallidos"], [])
        self.assertEqual(r["estados_envio"], {})


class TestHorasBogota(unittest.TestCase):
    """El panel muestra hora de Bogotá; el reporte debe hablar el mismo idioma."""

    def setUp(self):
        self.session = _nueva_sesion()

    def test_fecha_se_muestra_en_bogota_no_utc(self):
        # 15:00 UTC son las 10:00 en Colombia (UTC-5, sin horario de verano).
        _msg(self.session, from_number="573999999999",
             created_at=datetime.datetime(2026, 10, 1, 15, 0, 0))

        r = entrega_service.reporte_entrega(self.session)
        self.assertEqual(r["conversaciones"][0]["ultimo"], "2026-10-01 10:00")

    def test_generado_usa_hora_local(self):
        # El naive de la BD es UTC; hay que marcarlo como UTC antes de convertir.
        naive_utc = entrega_service._utc_naive()
        esperado = naive_utc.replace(tzinfo=datetime.UTC).astimezone(
            entrega_service.BOGOTA_TZ
        ).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(entrega_service._iso(naive_utc), esperado)

    def test_orden_por_ultimo_sigue_funcionando(self):
        # El orden usa strings "YYYY-MM-DD HH:MM": debe seguir siendo cronológico.
        _msg(self.session, from_number="573111111111",
             created_at=datetime.datetime(2026, 10, 1, 20, 0, 0))
        _msg(self.session, from_number="573222222222",
             created_at=datetime.datetime(2026, 10, 1, 18, 0, 0))

        r = entrega_service.reporte_entrega(self.session)
        orden = [c["telefono"] for c in r["conversaciones"]]
        self.assertEqual(orden, ["573111111111", "573222222222"])


class TestEstadoDelBot(unittest.TestCase):
    """La pregunta de soporte: ¿este número arrancó el bot o no?"""

    def setUp(self):
        self.session = _nueva_sesion()

    def _con_tutela(self, telefono, estado):
        user = User(telefono=telefono)
        self.session.add(user)
        self.session.commit()
        self.session.add(Tutela(
            user_id=user.id, tipo="salud", estado=estado, datos_json="{}",
        ))
        self.session.commit()

    def test_escribio_sin_tutela_es_no_arranco(self):
        _msg(self.session, from_number="573000000001")

        r = entrega_service.reporte_entrega(self.session)
        self.assertEqual(r["resumen"]["sin_arrancar"], 1)
        self.assertEqual(r["resumen"]["arrancaron"], 0)
        fila = r["conversaciones"][0]
        self.assertEqual(fila["bot_etiqueta"], "No arrancó")
        self.assertEqual(fila["bot_tono"], "mal")
        self.assertIsNone(fila["tutela_id"])

    def test_tutela_borrador_tambien_es_no_arranco(self):
        _msg(self.session, from_number="573000000002")
        self._con_tutela("573000000002", "borrador")

        r = entrega_service.reporte_entrega(self.session)
        self.assertEqual(r["conversaciones"][0]["bot_etiqueta"], "No arrancó")

    def test_tutela_en_recogiendo_datos_arranco(self):
        _msg(self.session, from_number="573000000003")
        self._con_tutela("573000000003", "recogiendo_datos")

        r = entrega_service.reporte_entrega(self.session)
        self.assertEqual(r["resumen"]["sin_arrancar"], 0)
        self.assertEqual(r["resumen"]["arrancaron"], 1)
        fila = r["conversaciones"][0]
        self.assertEqual(fila["bot_etiqueta"], "Recogiendo datos")
        self.assertIsNotNone(fila["tutela_id"])

    def test_etiquetas_cubren_las_etapas(self):
        casos = {
            "recogiendo_datos": "Recogiendo datos",
            "confirmar_datos_personales": "Recogiendo datos",
            "narracion": "Narración",
            "revision_datos": "Narración",
            "preguntas_clinicas": "Datos clínicos",
            "pruebas_pendiente": "Pruebas",
            "datos_listos": "Datos completos",
            "pdf_generado": "Datos completos",
            "esperando_pago": "Pendiente de radicación",
            "radicada": "Radicada",
            "completado": "Radicada",
            "fallida": "Con incidencias",
        }
        for estado, esperado in casos.items():
            with self.subTest(estado=estado):
                etiqueta, tono = entrega_service._etapa(estado)
                self.assertEqual(etiqueta, esperado)
                self.assertIn(tono, ("ok", "aviso", "mal", "info"))

    def test_estado_desconocido_no_rompe(self):
        etiqueta, tono = entrega_service._etapa("estado_del_futuro")
        self.assertEqual(etiqueta, "estado_del_futuro")
        self.assertEqual(tono, "info")

    def test_ultimo_texto_del_usuario(self):
        _msg(self.session, from_number="573000000004", body="me negaron la medicina")
        _msg(self.session, from_number="573000000004", body="  ")

        r = entrega_service.reporte_entrega(self.session)
        self.assertEqual(r["conversaciones"][0]["ultimo_texto"], "me negaron la medicina")

    def test_las_que_no_arrancan_van_arriba(self):
        _msg(self.session, from_number="573000000005",
             created_at=datetime.datetime(2026, 10, 1, 19, 0, 0))
        self._con_tutela("573000000005", "recogiendo_datos")
        _msg(self.session, from_number="573000000006",
             created_at=datetime.datetime(2026, 10, 1, 20, 0, 0))

        r = entrega_service.reporte_entrega(self.session)
        # Aunque el que arrancó es más reciente, el que NO arrancó va primero:
        # es lo que hay que mirar en soporte.
        self.assertEqual(r["conversaciones"][0]["telefono"], "573000000006")


if __name__ == "__main__":
    unittest.main()