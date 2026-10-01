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


if __name__ == "__main__":
    unittest.main()