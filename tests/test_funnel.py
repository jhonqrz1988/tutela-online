"""El embudo tiene que decir la verdad, aunque la verdad sea incómoda.

Se prueban tres cosas:

1. Que cada etapa cuente "alcanzó esta etapa o una posterior", no "está
   exactamente aquí". Si no, una tutela que ya pagó aparecería como si nunca
   hubiera dado sus datos.
2. Que el hueco grande entre etapas se detecte y se nombre.
3. Que no se cuele en el embudo lo que no es una persona (visitas de bot) ni
   la deuteron que no llegó a escribir (los envíos propios del bot).
"""
import contextlib
import datetime
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as database
from app.database import Base
from app.models.clic import ClicWhatsApp
from app.models.tutela import Tutela
from app.models.user import User
from app.models.visita import VisitaLanding
from app.models.whatsapp import MensajeWhatsApp
from app.services.funnel_service import etapa_de_estado, funnel

AHORA = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


@contextlib.contextmanager
def _db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    original = database.SessionLocal
    database.SessionLocal = maker
    try:
        yield maker()
    finally:
        database.SessionLocal = original
        engine.dispose()


def _tutela(session, telefono, estado):
    user = select_one_user(session, telefono)
    t = Tutela(user_id=user.id, tipo="salud", estado=estado, datos_json="{}",
               created_at=AHORA)
    session.add(t)
    session.commit()
    return user, t


def select_one_user(session, telefono):
    from sqlalchemy import select
    u = session.execute(select(User).where(User.telefono == telefono)).scalar_one_or_none()
    if u is None:
        u = User(telefono=telefono, estado="activo", consentimiento=True)
        session.add(u)
        session.commit()
    return u


class TestEscaleraDeEstados(unittest.TestCase):
    def test_un_estado_avanzado_tambien_cuenta_los_pasos_previos(self):
        """'alcanzó datos' debe ser cierto para quien ya está pagando."""
        i_datos = etapa_de_estado("recogiendo_datos")
        i_pago = etapa_de_estado("esperando_pago")
        self.assertIsNotNone(i_pago)
        self.assertGreater(i_pago, i_datos)

    def test_los_estados_fuera_del_embudo_no_cuentan(self):
        self.assertIsNone(etapa_de_estado(None))
        self.assertIsNone(etapa_de_estado("estado_inventado"))


class TestFunnel(unittest.TestCase):
    def setUp(self):
        self._ctx = _db()
        self.session = self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def test_embudo_vacio_no_revienta(self):
        d = funnel(self.session)
        self.assertEqual(d["alcance"]["tutelas_creadas"], 0)
        self.assertEqual(d["alcance"]["visitas_landing_humanas"], 0)
        self.assertIsNone(d["mayor_caida"])

    def test_no_cuenta_visitas_de_bot(self):
        self.session.add(VisitaLanding(fuente="ig", es_bot=False))
        self.session.add(VisitaLanding(fuente="facebookexternalhit", es_bot=True))
        self.session.commit()
        d = funnel(self.session)
        self.assertEqual(d["alcance"]["visitas_landing_humanas"], 1)

    def test_no_cuenta_nuestras_propios_envios_como_escritores(self):
        """Si solo salió un mensaje del bot, no es una persona que escribió."""
        select_one_user(self.session, "573000000001")
        self.session.add(MensajeWhatsApp(
            from_number="573000000001", body="Hola, tu tutela quedó a la espera",
            es_recibido=False, created_at=AHORA,
        ))
        self.session.commit()
        d = funnel(self.session)
        self.assertEqual(d["alcance"]["numeros_que_escribieron"], 0)
        self.assertEqual(d["alcance"]["usuarios_con_consentimiento"], 1)

    def test_cuenta_solo_una_vez_a_quien_escribio_varias_veces(self):
        for _ in range(4):
            self.session.add(MensajeWhatsApp(
                from_number="573000000002", body="hola", es_recibido=True,
                created_at=AHORA, tutela_id=None,
            ))
        self.session.commit()
        d = funnel(self.session)
        self.assertEqual(d["alcance"]["numeros_que_escribieron"], 1)

    def test_una_tutela_avanzada_cuenta_en_todas_las_etapas_previas(self):
        _tutela(self.session, "573000000003", "esperando_pago")
        d = funnel(self.session)
        por_clave = {e["clave"]: e["n"] for e in d["etapas"]}
        self.assertEqual(por_clave["inicio"], 1)
        self.assertEqual(por_clave["datos"], 1)
        self.assertEqual(por_clave["pago"], 1)
        self.assertEqual(por_clave["radicada"], 0)

    def test_detecta_el_hueco_mas_grande_entre_etapas(self):
        # 10 se quedan pidiendo datos, 2 llegan al final: el salto grande es
        # entre "mandó sus datos" y "confirmó sus datos".
        for _ in range(10):
            _tutela(self.session, "573000000004", "recogiendo_datos")
        for _ in range(2):
            _tutela(self.session, "573000000005", "radicada")
        d = funnel(self.session)
        self.assertEqual(d["mayor_caida"]["de"], "Mandó sus datos personales")
        self.assertEqual(d["mayor_caida"]["a"], "Confirmó sus datos")
        self.assertEqual(d["mayor_caida"]["perdidos"], 10)

    def test_convierte_a_porcentajes_del_inicio(self):
        _tutela(self.session, "573000000006", "radicada")
        _tutela(self.session, "573000000007", "recogiendo_datos")
        d = funnel(self.session)
        inicio = d["etapas"][0]
        # Ambas pasaron por 'datos' (una terminó, otra sigue ahí): 100%.
        datos = next(e for e in d["etapas"] if e["clave"] == "datos")
        # Solo una llegó a confirmar: 50%.
        confirmacion = next(e for e in d["etapas"] if e["clave"] == "confirmacion_datos")
        self.assertEqual(inicio["n"], 2)
        self.assertEqual(inicio["pct_del_inicio"], 100.0)
        self.assertEqual(datos["pct_del_inicio"], 100.0)
        self.assertEqual(confirmacion["n"], 1)
        self.assertEqual(confirmacion["pct_del_inicio"], 50.0)

    def test_ventana_de_tiempo_filtra_lo_antiguo(self):
        viejo = _tutela(self.session, "573000000008", "radicada")
        viejo[1].created_at = AHORA - datetime.timedelta(days=90)
        self.session.commit()
        nuevo = _tutela(self.session, "573000000009", "recogiendo_datos")
        nuevo[1].created_at = AHORA
        self.session.commit()

        self.assertEqual(funnel(self.session)["alcance"]["tutelas_creadas"], 2)
        self.assertEqual(
            funnel(self.session, dias=30)["alcance"]["tutelas_creadas"], 1
        )

    def test_detecta_quien_escribio_y_nunca_creo_tutela(self):
        """Escribir y quedarse sin tutela = entró y se fue."""
        select_one_user(self.session, "573000000010")
        self.session.add(MensajeWhatsApp(
            from_number="573000000010", body="hola", es_recibido=True,
            created_at=AHORA, tutela_id=None,
        ))
        self.session.commit()
        d = funnel(self.session)
        self.assertEqual(d["alcance"]["escribieron_sin_arrancar"], 1)

    def test_el_primer_mensaje_no_cuenta_como_abandono(self):
        """El mensaje inicial llega antes de que exista la tutela.

        Si eso se contara como abandono, TODO el que empieza una tutela quedaría
        marcado como que se fue, y el embudo se iría a cero por un falso positivo.
        """
        user, _ = _tutela(self.session, "573000000011", "recogiendo_datos")
        self.session.add(MensajeWhatsApp(
            from_number="573000000011", body="hola", es_recibido=True,
            created_at=AHORA, tutela_id=None,   # llegó antes de crear la tutela
        ))
        self.session.commit()
        d = funnel(self.session)
        self.assertEqual(d["alcance"]["escribieron_sin_arrancar"], 0)
        self.assertEqual(d["alcance"]["tutelas_creadas"], 1)

    def test_perfila_a_quien_escribio_y_no_arranco(self):
        """Distinguir 'escribió una vez y se fue' de 'conversó y no lo jalamos'."""
        for tel, veces in (("573000000020", 1), ("573000000021", 1),
                           ("573000000022", 3), ("573000000023", 7)):
            select_one_user(self.session, tel)
            for _ in range(veces):
                self.session.add(MensajeWhatsApp(
                    from_number=tel, body="hola", es_recibido=True, created_at=AHORA,
                ))
        # Uno que sí tiene tutela: no debe entrar en este perfil.
        _tutela(self.session, "573000000024", "recogiendo_datos")
        for _ in range(9):
            self.session.add(MensajeWhatsApp(
                from_number="573000000024", body="hola", es_recibido=True, created_at=AHORA,
            ))
        self.session.commit()

        p = funnel(self.session)["perfil_sin_arrancar"]
        self.assertEqual(p["con_1_mensaje"], 2)
        self.assertEqual(p["con_2_o_3"], 1)
        self.assertEqual(p["con_4_mas"], 1)
        self.assertEqual(p["mensajes_totales"], 1 + 1 + 3 + 7)

    def test_declara_las_cejas_del_embudo(self):
        d = funnel(self.session)
        self.assertTrue(d["notas"], "un embudo sin límites declarados se lee mal")
        self.assertTrue(any("historial" in n for n in d["notas"]))

    def test_expone_las_fugas_entre_etapas_del_alcance(self):
        self.session.add(VisitaLanding(fuente="ig", es_bot=False))
        self.session.add(ClicWhatsApp(fuente="ig"))
        select_one_user(self.session, "573000000012")
        self.session.add(MensajeWhatsApp(
            from_number="573000000012", body="hola", es_recibido=True, created_at=AHORA,
        ))
        self.session.commit()
        d = funnel(self.session)
        origins = [f["de"] for f in d["fugas"]]
        destinos = [f["a"] for f in d["fugas"]]
        self.assertIn("Visitó la landing", origins)
        # El último paso aparece como destino de la fuga anterior, no como origen.
        self.assertIn("Inició una tutela", destinos)
        self.assertNotIn("Inició una tutela", origins)
        fuga_inicio = d["fugas"][0]
        self.assertEqual(fuga_inicio["de_n"], 1)
        self.assertEqual(fuga_inicio["a_n"], 1)


if __name__ == "__main__":
    unittest.main()
