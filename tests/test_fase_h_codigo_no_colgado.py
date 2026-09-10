"""Tests for Fase H: el flujo de código de email NO puede quedarse colgado.

Estado NUEVO (escenario A: loop persistente + parqueo):
  - `iniciar_radicacion` corre SIEMPRE sobre el loop persistente del bot
    (dueño del navegador). Cuando el portal pide el código, la coroutine se
    PARQUEA: espera un `asyncio.Event` (asyncio puro) hasta
    `TIMEOUT_ESPERA_CODIGO` segundos y luego se abandona sola.
  - `continuar_radicacion_con_codigo` es un SEÑALADOR puro: escribe
    `_codigos_pendientes[tutela_id]` y ordena `loop.call_soon_threadsafe(set)`.
    Nunca toca el navegador, nunca crea hilos, el webhook responde al instante.
  - El coroutine parkeado despierta, ingresa el código y completa pasos 5-10.
  - Si el proceso se reinicia mientras está parkeado, el watchdog cubre el
    caso: un 'esperando_codigo_email' viejo pasa a 'fallida'.

Antes (bug de producción) cada llamada corría `asyncio.run(iniciar_radicacion)`,
el loop moría al pausar, el navegador quedaba 'vivo aparente' pero muerto, y la
radicación se quedaba en 'continuando' para siempre.
"""
import asyncio
import datetime
import json
import threading
import time
import unittest
from unittest import mock

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela
from app.models.user import User

from app.services import radicacion_service


def _nueva_fabrica():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _nueva_sesion():
    return _nueva_fabrica()()


class _FakePage:
    def is_closed(self):
        return False


def _crear_tutela_a_radicar(session, telefono="573001112233", estado="pendiente_radicacion"):
    user = User(telefono=telefono, estado="activo", consentimiento=True)
    session.add(user)
    session.flush()
    tutela = Tutela(
        user_id=user.id,
        tipo="salud",
        estado=estado,
        datos_json=json.dumps({"tipo": "salud"}),
    )
    session.add(tutela)
    session.commit()
    return user, tutela


class _FakeBotConCodigo:
    """Bot cuyo formulario pide código de email y completa los pasos 5-10."""

    def __init__(self, ingreso_delay=0.05):
        self.page = _FakePage()
        self.entradas = []
        self.cerrado = False
        self.ingreso_delay = ingreso_delay
        self.vivo_al_ping = True
        self.ping_fallidos_antes = 0
        self.llamadas_ping = 0

    async def iniciar(self):
        pass

    async def navegar_portal(self):
        pass

    async def llenar_formulario(self, datos):
        return {"ok": True, "requiere_codigo_email": True}

    async def verificar_conexion(self):
        self.llamadas_ping += 1
        if self.llamadas_ping <= self.ping_fallidos_antes:
            return False
        return self.vivo_al_ping

    async def ingresar_codigo_email(self, codigo):
        self.entradas.append(codigo)
        await asyncio.sleep(self.ingreso_delay)

    async def completar_post_codigo(self, datos, ruta):
        return {"ok": True}

    async def resolver_recaptcha(self):
        return True

    async def enviar_y_descargar(self):
        return {"path": "storage/constancia_h.png", "num_radicado": "11001-2026-00010"}

    async def tomar_screenshot(self, nombre):
        return None

    async def cerrar(self):
        self.cerrado = True


class _LoopHogarTest:
    """Event loop en un hilo de fondo: el coroutine de radicación vive aquí."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.hilo = threading.Thread(target=self._correr, name="loop-parqueo-test", daemon=True)
        self.hilo.start()
        limite = time.monotonic() + 5
        while not self.loop.is_running():
            if time.monotonic() > limite:
                raise RuntimeError("El loop de fondo no arrancó")
            time.sleep(0.001)

    def _correr(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def despachar(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def detener(self):
        if self.loop.is_closed():
            return
        for tarea in asyncio.all_tasks(self.loop):
            self.loop.call_soon_threadsafe(tarea.cancel)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.hilo.join(timeout=2)
        self.loop.close()


class TestParqueoCodigo(unittest.TestCase):
    """La radicación se parquea esperando el código y el webhook la despierta."""

    def setUp(self):
        with radicacion_service._parqueos_lock:
            radicacion_service._parqueos.clear()
            radicacion_service._codigos_pendientes.clear()
        self.fabrica = _nueva_fabrica()
        session = self.fabrica()
        self.user, self.tutela = _crear_tutela_a_radicar(session)
        self.tutela_id = self.tutela.id
        session.close()
        self.bot = _FakeBotConCodigo()
        self.avisos = []
        self._patches = (
            mock.patch.object(radicacion_service, "SessionLocal", self.fabrica),
            mock.patch.object(radicacion_service, "_get_bot", lambda: self.bot),
            mock.patch.object(
                radicacion_service,
                "enviar_texto",
                side_effect=lambda tel, msg: self.avisos.append(msg),
            ),
            mock.patch.object(radicacion_service, "enviar_imagen", return_value=True),
        )
        for p in self._patches:
            p.start()
        self.loop_hogar = _LoopHogarTest()

    def tearDown(self):
        self.loop_hogar.detener()
        for p in self._patches:
            p.stop()
        with radicacion_service._parqueos_lock:
            radicacion_service._parqueos.clear()
            radicacion_service._codigos_pendientes.clear()

    def _despachar(self):
        return self.loop_hogar.despachar(
            radicacion_service.iniciar_radicacion(self.tutela_id, forzar=True)
        )

    def _esperar_parqueo(self, tutela_id=None, segundos=5):
        tutela_id = tutela_id or self.tutela_id
        limite = time.monotonic() + segundos
        while time.monotonic() < limite:
            with radicacion_service._parqueos_lock:
                if tutela_id in radicacion_service._parqueos:
                    return True
            time.sleep(0.005)
        return False

    def _esperar_entrada(self, segundos=5):
        limite = time.monotonic() + segundos
        while time.monotonic() < limite and not self.bot.entradas:
            time.sleep(0.005)
        return bool(self.bot.entradas)

    def _esperar_despachos(self, n, segundos=5):
        limite = time.monotonic() + segundos
        while time.monotonic() < limite and len(self.redespachos) < n:
            time.sleep(0.005)
        return len(self.redespachos)

    def _señalar(self, codigo):
        return asyncio.run(radicacion_service.continuar_radicacion_con_codigo(self.tutela_id, codigo))

    def test_codigo_señalado_despierta_el_parqueo_y_completa_la_radicacion(self):
        """Reproduce el bug raíz: el loop del bot muere al pausar. Ahora la
        coroutine se parquea y el código del webhook la despierta (vía
        call_soon_threadsafe), ingresa el código y completa la radicación."""
        fut = self._despachar()
        self.assertTrue(self._esperar_parqueo(), "La radicación debe quedar parkeada esperando el código")

        res = self._señalar("582913")
        self.assertTrue(res.get("ok"), f"El señalamiento debe proceder: {res}")

        resultado = fut.result(timeout=5)
        self.assertTrue(resultado.get("ok"), f"La radicación debe completarse: {resultado}")
        self.assertEqual(self.bot.entradas, ["582913"], "El código debe ingresarse una sola vez")

        session = self.fabrica()
        rad = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == self.tutela_id)
        ).scalar_one()
        self.assertEqual(rad.estado, "radicada")
        tutela = session.get(Tutela, self.tutela_id)
        self.assertEqual(tutela.estado, "radicada")
        session.close()

        with radicacion_service._parqueos_lock:
            self.assertNotIn(self.tutela_id, radicacion_service._parqueos, "No debe quedar parqueo")
            self.assertNotIn(self.tutela_id, radicacion_service._codigos_pendientes, "No debe quedar código pendiente")

    def test_doble_envio_manda_un_solo_codigo(self):
        """Los envíos duplicados nunca duplican el código en el navegador: pop
        único del coroutine parkeado. El segundo envío (tras consumirse) ya no
        encuentra parqueo y responde sin tocar el navegador."""
        fut = self._despachar()
        self.assertTrue(self._esperar_parqueo())

        r1 = self._señalar("582913")
        self.assertTrue(r1.get("ok"), f"Primer envío debe señalarse: {r1}")
        self.assertTrue(self._esperar_entrada(), "El coroutine debe despertar e ingresar el código")

        r2 = self._señalar("582914")
        self.assertFalse(r2.get("ok"), "El parqueo ya fue consumido: no debe señalarse de nuevo")

        resultado = fut.result(timeout=5)
        self.assertTrue(resultado.get("ok"), f"La radicación debe completarse: {resultado}")
        self.assertEqual(self.bot.entradas, ["582913"], "Solo UN código debe ingresarse (nunca duplicar)")

        with radicacion_service._parqueos_lock:
            self.assertNotIn(self.tutela_id, radicacion_service._codigos_pendientes, "No debe quedar código huérfano")

    def test_parqueo_expira_sin_codigo_programa_reintento_automatico(self):
        """Sin código a tiempo, el parqueo se abandona solo y la radicación se
        re-lanza automáticamente (sin esperar al admin): cierra el navegador,
        encola la tutela y agenda un re-despacho que vuelve a pedir un código
        nuevo (el portal lo genera de nuevo en cada corrida)."""
        self.redespachos = []
        with mock.patch.object(radicacion_service, "TIMEOUT_ESPERA_CODIGO", 0.15), \
             mock.patch.object(radicacion_service, "RETRY_ESPERA_SEG", 0.05), \
             mock.patch.object(
                 radicacion_service,
                 "despachar_radicacion",
                 side_effect=lambda tid, **kw: self.redespachos.append(tid) or {"ok": True, "despachada": True},
             ):
            fut = self._despachar()
            self.assertTrue(self._esperar_parqueo())
            resultado = fut.result(timeout=5)
            self.assertTrue(self._esperar_despachos(1), "Debe re-despacharse antes de desparchear: evita un navegador real")

        self.assertFalse(resultado.get("ok"), f"Sin código debe abandonarse: {resultado}")
        self.assertTrue(resultado.get("reintento_automatico"), f"Debe ser un reintento automático: {resultado}")
        self.assertIn("tiempo", resultado.get("error", "").lower())
        self.assertIn("automáticamente", resultado.get("error", "").lower())
        self.assertTrue(self.bot.cerrado, "El navegador debe cerrarse al abandonar el parqueo")
        self.assertEqual(self.bot.entradas, [], "Nunca debe ingresarse un código inexistente")

        session = self.fabrica()
        rad = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == self.tutela_id)
        ).scalar_one()
        self.assertEqual(rad.estado, "fallida", "El intento actual se registra como fallido")
        self.assertEqual(rad.intentos, 1, "El reintento automático cuenta como un intento")
        self.assertTrue(rad.ultimo_error)
        tutela = session.get(Tutela, self.tutela_id)
        self.assertEqual(tutela.estado, "pendiente_radicacion", "Se encola para re-radicar sola")
        session.close()

        self.assertEqual(len(self.redespachos), 1, "Un solo re-despacho automático")
        self.assertEqual(self.redespachos[0], self.tutela_id, "El re-despacho debe apuntar a ESTA tutela")
        self.assertTrue(any("automáticamente" in a.lower() for a in self.avisos), f"Aviso de reintento: {self.avisos}")

        with radicacion_service._parqueos_lock:
            self.assertNotIn(self.tutela_id, radicacion_service._parqueos)
            self.assertNotIn(self.tutela_id, radicacion_service._codigos_pendientes)

    def test_browser_muerto_al_despertar_reintenta_automaticamente(self):
        """El navegador pudo quedar 'vivo aparente' al parquear. Si al llegar el
        código el ping falla, NO se escribe sobre una página muerta: la
        radicación se re-lanza sola (la falla que veía el usuario ahora se
        recupera automáticamente)."""
        self.redespachos = []
        self.bot.vivo_al_ping = False
        with mock.patch.object(radicacion_service, "CONEXION_PAUSA_SEG", 0.02), \
             mock.patch.object(radicacion_service, "RETRY_ESPERA_SEG", 0.05), \
             mock.patch.object(
                 radicacion_service,
                 "despachar_radicacion",
                 side_effect=lambda tid, **kw: self.redespachos.append(tid) or {"ok": True, "despachada": True},
             ):
            fut = self._despachar()
            self.assertTrue(self._esperar_parqueo())
            res = self._señalar("582913")
            self.assertTrue(res.get("ok"), f"El código debe señalarse: {res}")
            resultado = fut.result(timeout=5)
            self.assertTrue(self._esperar_despachos(1), "Debe re-despacharse antes de desparchear")

        self.assertFalse(resultado.get("ok"))
        self.assertTrue(resultado.get("reintento_automatico"), f"Debe reintentar sola: {resultado}")
        self.assertEqual(self.bot.entradas, [], "Nunca se escribe sobre un navegador muerto")
        self.assertGreaterEqual(self.bot.llamadas_ping, 3, "El sondeo tolerante debe intentar varias veces antes de declararlo muerto")
        session = self.fabrica()
        tutela = session.get(Tutela, self.tutela_id)
        self.assertEqual(tutela.estado, "pendiente_radicacion")
        rad = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == self.tutela_id)
        ).scalar_one()
        self.assertEqual(rad.intentos, 1)
        session.close()
        self.assertEqual(self.redespachos, [self.tutela_id])
        self.assertTrue(any("automáticamente" in a.lower() for a in self.avisos), f"Avisos: {self.avisos}")

    def test_ping_lento_transitorio_no_mata_el_parqueo(self):
        """El portal pesado puede tardar en responder el ping (cajón de
        verificación abierto) estando VIVO. El sondeo tolerante (varios
        intentos espaciados) no debe declararlo muerto por un par de fallos:
        el código se ingresa y la radicación completa."""
        self.bot.ping_fallidos_antes = 2
        fut = self._despachar()
        self.assertTrue(self._esperar_parqueo())

        res = self._señalar("582913")
        self.assertTrue(res.get("ok"), f"El código debe señalarse: {res}")

        with mock.patch.object(radicacion_service, "CONEXION_PAUSA_SEG", 0.02):
            resultado = fut.result(timeout=5)

        self.assertTrue(resultado.get("ok"), f"Debe completarse pese al ping lento: {resultado}")
        self.assertEqual(self.bot.entradas, ["582913"], "El código debe ingresarse una sola vez")
        self.assertGreaterEqual(self.bot.llamadas_ping, 3, "El sondeo debe haber intentado varias veces")

    def test_reintentos_agotados_no_reintenta_y_pasa_a_fallida(self):
        """Con los intentos agotados (3), el timeout ya no agenda más
        re-despachos: falla definitivo para revisión del admin (y el scheduler
        tampoco lo reprocesa: respeta `intentos >= 3`)."""
        session = self.fabrica()
        session.add(Radicacion(tutela_id=self.tutela_id, intentos=3))
        session.commit()
        session.close()

        self.redespachos = []
        with mock.patch.object(radicacion_service, "TIMEOUT_ESPERA_CODIGO", 0.15), \
             mock.patch.object(radicacion_service, "RETRY_ESPERA_SEG", 0.05), \
             mock.patch.object(
                 radicacion_service,
                 "despachar_radicacion",
                 side_effect=lambda tid, **kw: self.redespachos.append(tid) or {"ok": True, "despachada": True},
             ):
            fut = self._despachar()
            self.assertTrue(self._esperar_parqueo())
            resultado = fut.result(timeout=5)

        time.sleep(0.2)
        self.assertFalse(resultado.get("ok"))
        self.assertNotIn("reintento_automatico", resultado, f"No debe reintentar con intentos agotados: {resultado}")
        self.assertEqual(self.redespachos, [], "No debe re-despacharse con intentos agotados")
        self.assertEqual(self.bot.entradas, [])
        session = self.fabrica()
        tutela = session.get(Tutela, self.tutela_id)
        rad = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == self.tutela_id)
        ).scalar_one()
        self.assertEqual(rad.intentos, 4, "El intento agotado cuenta pero no reintenta más (3 previos + 1 actual)")
        self.assertEqual(tutela.estado, "fallida")
        session.close()
        with radicacion_service._parqueos_lock:
            self.assertNotIn(self.tutela_id, radicacion_service._parqueos)
            self.assertNotIn(self.tutela_id, radicacion_service._codigos_pendientes)

    def test_reintento_no_dobla_si_la_tutela_ya_se_radico(self):
        """El hilo de reintento revisa la BD antes de despachar: si mientras
        esperaba otra corrida (o el admin) ya radicó, NO abre un segundo
        navegador sobre la misma tutela."""
        session = self.fabrica()
        _, tutela_hecha = _crear_tutela_a_radicar(session, telefono="573004445566", estado="radicada")
        session.close()

        self.redespachos = []
        with mock.patch.object(radicacion_service, "RETRY_ESPERA_SEG", 0.05), \
             mock.patch.object(
                 radicacion_service,
                 "despachar_radicacion",
                 side_effect=lambda tid, **kw: self.redespachos.append(tid) or {"ok": True, "despachada": True},
             ):
            radicacion_service._programar_reintento_codigo(tutela_hecha.id)
            time.sleep(0.3)

        self.assertEqual(self.redespachos, [], "Ya radicada: el reintento no debe volver a radicar")

    def test_webhook_no_espera_al_navegador_con_ingesta_lenta(self):
        """El señalador responde al instante aunque el navegador tarde en
        ingresar el código: el webhook nunca se bloquea con Playwright."""
        self.bot.ingreso_delay = 0.5
        fut = self._despachar()
        self.assertTrue(self._esperar_parqueo())

        inicio = time.monotonic()
        res = self._señalar("582913")
        duracion = time.monotonic() - inicio

        self.assertTrue(res.get("ok"), f"El señalamiento debe proceder: {res}")
        self.assertLess(duracion, 0.25, "El webhook NO debe bloquearse esperando al navegador")
        self.assertTrue(fut.result(timeout=5).get("ok"))
        self.assertEqual(self.bot.entradas, ["582913"])

    def test_sin_parqueo_registrado_responde_sin_tocar_el_bot(self):
        """Servidor reiniciado (el parqueo en memoria se perdió): el código
        llega al webhook pero NO hay coroutine a quién señalarle. Responde un
        error claro, no toca el navegador y no deja códigos huérfanos."""
        # Radicación pidiendo el código en BD, pero sin parqueo en memoria.
        session = self.fabrica()
        session.add(Radicacion(tutela_id=self.tutela_id, estado="esperando_codigo_email"))
        session.commit()
        session.close()

        res = self._señalar("582913")

        self.assertFalse(res.get("ok"), "Sin parqueo no debe señalarse nada")
        self.assertIn("reinició", res.get("error", "").lower())
        self.assertEqual(self.bot.entradas, [])
        self.assertFalse(self.bot.cerrado, "No debe tocarse el navegador sin parqueo")
        with radicacion_service._parqueos_lock:
            self.assertNotIn(self.tutela_id, radicacion_service._codigos_pendientes)


class TestWatchdogEsperandoCodigo(unittest.TestCase):
    def test_esperando_codigo_email_viejo_pasa_a_fallida(self):
        """El watchdog cubre el caso de proceso reiniciado mientras el parqueo
        esperaba el código: un 'esperando_codigo_email' más viejo que
        UMBRAL_ESPERANDO_CODIGO pasa a 'fallida' (reintentable)."""
        session = _nueva_sesion()
        _, vieja = _crear_tutela_a_radicar(session, telefono="573001112244", estado="esperando_codigo_email")
        _, reciente = _crear_tutela_a_radicar(session, telefono="573001112255", estado="esperando_codigo_email")
        session.add(Radicacion(tutela_id=vieja.id, estado="esperando_codigo_email"))
        session.add(Radicacion(tutela_id=reciente.id, estado="esperando_codigo_email"))
        session.execute(
            update(Radicacion).where(Radicacion.tutela_id == vieja.id).values(
                updated_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=13)
            )
        )
        session.commit()

        with mock.patch.object(radicacion_service, "SessionLocal", return_value=session), \
             mock.patch.object(radicacion_service, "_bot", None):
            descolgadas = radicacion_service.descolgar_radicaciones_estancadas()

        rad_vieja = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == vieja.id)
        ).scalar_one()
        rad_reciente = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == reciente.id)
        ).scalar_one()
        self.assertEqual(rad_vieja.estado, "fallida")
        self.assertIn("vencido", rad_vieja.ultimo_error.lower())
        self.assertEqual(rad_reciente.estado, "esperando_codigo_email",
                         "La esperando código reciente no debe tocarse")
        ids = [d["tutela_id"] for d in descolgadas]
        self.assertIn(vieja.id, ids)
        self.assertNotIn(reciente.id, ids)


def _crear_tutela_esperando_codigo(session, telefono="573001112233"):
    user = User(telefono=telefono, estado="activo", consentimiento=True)
    session.add(user)
    session.flush()
    tutela = Tutela(
        user_id=user.id,
        tipo="salud",
        estado="esperando_codigo_email",
        datos_json=json.dumps({"tipo": "salud"}),
    )
    session.add(tutela)
    session.flush()
    session.add(Radicacion(tutela_id=tutela.id, estado="continuando"))
    session.commit()
    return user, tutela


class TestDescolgarEstancadas(unittest.TestCase):
    def test_continuando_viejo_pasa_a_fallida(self):
        """El watchdog marca como 'fallida' una radicación colgada hace más
        del umbral, y deja intactas las recientes (o en espera de código)."""
        session = _nueva_sesion()
        _, tutela_vieja = _crear_tutela_esperando_codigo(session, telefono="573001112244")
        _, tutela_reciente = _crear_tutela_esperando_codigo(session, telefono="573001112255")
        session.execute(
            update(Radicacion).where(Radicacion.tutela_id == tutela_vieja.id).values(
                updated_at=datetime.datetime.utcnow() - datetime.timedelta(minutes=20)
            )
        )
        session.commit()

        with mock.patch.object(radicacion_service, "SessionLocal", return_value=session), \
             mock.patch.object(radicacion_service, "_bot", None):
            radicacion_service.descolgar_radicaciones_estancadas()

        rad_vieja = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela_vieja.id)
        ).scalar_one()
        rad_reciente = session.execute(
            select(Radicacion).where(Radicacion.tutela_id == tutela_reciente.id)
        ).scalar_one()
        self.assertEqual(rad_vieja.estado, "fallida")
        self.assertTrue(rad_vieja.ultimo_error)
        self.assertEqual(rad_reciente.estado, "continuando", "La reciente no debe tocarse")


class TestIngresarCodigoEmailAcotado(unittest.TestCase):
    def test_selector_ausente_devuelve_error_sin_colgarse(self):
        """Si el portal no muestra #IdEmail1, `ingresar_codigo_email` debe
        acotar la espera y devolver {ok: False} en vez de colgar el loop."""
        import app.bot.navegador as navegador_mod
        from app.bot.navegador import RadicadorBot

        real_timeout = navegador_mod.ESPERA_CODIGO_SELECTOR_MS

        class PageSinSelector:
            def __init__(self):
                self.llamadas_selector = 0

            async def wait_for_selector(self, selector, **kwargs):
                self.llamadas_selector += 1
                raise TimeoutError(f"No aparece {selector}")

            async def fill(self, *args, **kwargs):
                raise AssertionError("No se debe intentar escribir sin selector")

            async def type(self, *args, **kwargs):
                raise AssertionError("No se debe intentar escribir sin selector")

            async def wait_for_timeout(self, ms):
                return

        navegador_mod.ESPERA_CODIGO_SELECTOR_MS = 100
        bot = RadicadorBot()
        bot.page = PageSinSelector()
        try:
            resultado = asyncio.run(bot.ingresar_codigo_email("582913"))
        finally:
            navegador_mod.ESPERA_CODIGO_SELECTOR_MS = real_timeout

        self.assertIsInstance(resultado, dict)
        self.assertFalse(resultado.get("ok"))
        self.assertTrue(resultado.get("error"))

    def test_codigo_valido_escribe_confirma_reingresa_correo_y_devuelve_ok(self):
        """FLUJO REAL del portal: el código se escribe en un cajón que se abre
        al validar el correo, se pulsa 'Continuar', el cajón valida y el portal
        vuelve a pedir el correo. El bot debe: escribir el código, pulsar
        'Continuar', re-ingresar el correo y validarlo de nuevo (ya sin código)."""
        from app.bot.navegador import RadicadorBot

        esperado = {"limpiados": [], "escritos": [], "clicks": []}

        class ElementoBoton:
            async def is_visible(self):
                return True

            async def click(self):
                return None

        class PageCajon:
            def __init__(self):
                self.pide_codigo_de_nuevo = False

            async def wait_for_selector(self, selector, **kwargs):
                return ElementoBoton()

            async def query_selector(self, selector):
                if selector == "#IdEmail1":
                    return ElementoBoton() if self.pide_codigo_de_nuevo else None
                return ElementoBoton()

            async def fill(self, selector, valor, **kwargs):
                esperado["limpiados"].append((selector, valor))

            async def type(self, selector, valor, **kwargs):
                esperado["escritos"].append((selector, valor))

            async def wait_for_timeout(self, ms):
                return None

        bot = RadicadorBot()
        bot.page = PageCajon()
        bot._email_accionante = "a@b.com"
        bot._js_click = mock.AsyncMock()
        bot.tomar_screenshot = mock.AsyncMock(return_value=None)

        resultado = asyncio.run(bot.ingresar_codigo_email("582913"))

        self.assertTrue(resultado.get("ok"), f"Flujo completo debe devolver ok: {resultado}")
        self.assertIn(("#IdEmail1", ""), esperado["limpiados"])
        self.assertIn(("#IdEmail1", "582913"), esperado["escritos"])
        self.assertIn(("#Email", ""), esperado["limpiados"], "Debe limpiarse el correo para re-ingresarlo")
        self.assertIn(("#Email", "a@b.com"), esperado["escritos"], "Debe re-ingresarse el correo")
        bot._js_click.assert_has_calls([mock.call("#btnValidar")], any_order=True)

    def test_cajon_sin_boton_continuar_devuelve_error(self):
        """Si el cajón de verificación no expone un botón 'Continuar', se
        devuelve {ok: False} con un error claro en vez de avanzar a ciegas."""
        from app.bot.navegador import RadicadorBot

        class PageSinBoton:
            async def wait_for_selector(self, selector, **kwargs):
                return object()

            async def query_selector(self, selector):
                return None

            async def fill(self, *args, **kwargs):
                return None

            async def type(self, *args, **kwargs):
                return None

            async def wait_for_timeout(self, ms):
                return None

        bot = RadicadorBot()
        bot.page = PageSinBoton()
        bot.tomar_screenshot = mock.AsyncMock(return_value=None)

        resultado = asyncio.run(bot.ingresar_codigo_email("582913"))

        self.assertIsInstance(resultado, dict)
        self.assertFalse(resultado.get("ok"))
        self.assertIn("Continuar", resultado.get("error", ""))

    def test_cajon_pide_codigo_de_nuevo_devuelve_error(self):
        """Tras re-ingresar el correo el cajón siguió pidiendo código (correo
        no quedó verificado): se devuelve error claro, no se avanza a ciegas."""
        from app.bot.navegador import RadicadorBot

        class ElementoVisible:
            async def is_visible(self):
                return True

            async def click(self):
                return None

        class PagePideDeNuevo:
            async def wait_for_selector(self, selector, **kwargs):
                return ElementoVisible()

            async def query_selector(self, selector):
                return ElementoVisible()

            async def fill(self, *args, **kwargs):
                return None

            async def type(self, *args, **kwargs):
                return None

            async def wait_for_timeout(self, ms):
                return None

        bot = RadicadorBot()
        bot.page = PagePideDeNuevo()
        bot._email_accionante = "a@b.com"
        bot._js_click = mock.AsyncMock()
        bot.tomar_screenshot = mock.AsyncMock(return_value=None)

        resultado = asyncio.run(bot.ingresar_codigo_email("582913"))

        self.assertFalse(resultado.get("ok"))
        self.assertIn("volvió a pedir", resultado.get("error", "").lower())


if __name__ == "__main__":
    unittest.main()