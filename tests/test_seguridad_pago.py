"""El enlace de pago no debe ser adivinable ni filtrar datos personales.

HALLAZGO (verificado en produccion): ``/pago/{tutela_id}`` es publico y el id
es un entero correlativo. Con solo probar 1, 2, 3... un tercero obtiene:

  1. INFORMACION: el correo del accionante de cada tutela (dato personal, y el
     contexto es salud -> Ley 1581).
  2. ESCRITURA: cada GET guardaba ``mercadopago_reference`` en ``datos_json``
     de una tutela que no le pertenece.
  3. ABUSO: cada GET creaba una preferencia real de Mercado Pago (cuota de la
     API y basura en las preferencias del comercio).

``/pago/{id}/verificar`` tenia el mismo problema y peor: sin token, quien
encontrara el id podia forzar la confirmacion del pago y disparar el envio de
WhatsApp.

Solucion: token por tutela = HMAC-SHA256 del id con SECRET_KEY. No es adivinable
y no requiere columna nueva ni migracion. Ademas el correo se muestra
enmascarado aunque el enlace sea legitimo.
"""
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.api import pagos as pagos_mod
from app.config import settings
from app.database import Base, get_session
from app.main import app
from app.models.tutela import Tutela
from app.models.user import User


def _sesion_con_tutela(email="persona@correo.com"):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    user = User(telefono="573000000123", estado="activo", consentimiento=True)
    session.add(user)
    session.commit()
    tutela = Tutela(
        user_id=user.id, tipo="salud", estado="esperando_pago",
        datos_json='{"tipo":"salud","accionante_email":"%s"}' % email,
    )
    session.add(tutela)
    session.commit()
    return session, tutela


def _client(session):
    def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    cliente = TestClient(app)
    cliente.__enter__()

    def _teardown():
        app.dependency_overrides.pop(get_session, None)
        cliente.__exit__(None, None, None)
        cliente.close()

    return cliente, _teardown


class TestTokenPago(unittest.TestCase):
    def setUp(self):
        settings.secret_key = "clave-de-prueba-para-el-token"

    def test_el_token_depende_del_id(self):
        self.assertNotEqual(
            pagos_mod.token_pago(1), pagos_mod.token_pago(2),
            "dos tutelas distintas no pueden compartir token",
        )

    def test_el_token_es_estable(self):
        self.assertEqual(pagos_mod.token_pago(7), pagos_mod.token_pago(7))

    def test_el_token_cambia_con_la_clave(self):
        primero = pagos_mod.token_pago(7)
        settings.secret_key = "otra-clave-distinta"
        self.assertNotEqual(primero, pagos_mod.token_pago(7))
        settings.secret_key = "clave-de-prueba-para-el-token"

    def test_no_adivina_un_token_cualquiera(self):
        self.assertFalse(pagos_mod.token_pago_valido(7, "0" * 32))
        self.assertFalse(pagos_mod.token_pago_valido(7, ""))
        self.assertFalse(pagos_mod.token_pago_valido(7, None))


class TestEnlaceDePago(unittest.TestCase):
    def setUp(self):
        settings.secret_key = "clave-de-prueba-para-el-token"

    def test_sin_token_no_entrega_la_pagina(self):
        session, tutela = _sesion_con_tutela()
        cliente, tear = _client(session)
        try:
            resp = cliente.get(f"/pago/{tutela.id}", follow_redirects=False)
            self.assertEqual(resp.status_code, 403)
            self.assertNotIn("persona@correo.com", resp.text)
        finally:
            tear()

    def test_con_token_incorrecto_no_entrega_la_pagina(self):
        session, tutela = _sesion_con_tutela()
        cliente, tear = _client(session)
        try:
            resp = cliente.get(f"/pago/{tutela.id}?t={'f' * 32}", follow_redirects=False)
            self.assertEqual(resp.status_code, 403)
            self.assertNotIn("persona@correo.com", resp.text)
        finally:
            tear()

    def test_el_token_de_otra_tutela_no_sirve(self):
        session, tutela = _sesion_con_tutela()
        cliente, tear = _client(session)
        try:
            token_ajeno = pagos_mod.token_pago(tutela.id + 999)
            resp = cliente.get(f"/pago/{tutela.id}?t={token_ajeno}", follow_redirects=False)
            self.assertEqual(resp.status_code, 403)
        finally:
            tear()

    def test_con_token_valido_entrega_la_pagina(self):
        session, tutela = _sesion_con_tutela()
        cliente, tear = _client(session)
        try:
            token = pagos_mod.token_pago(tutela.id)
            resp = cliente.get(f"/pago/{tutela.id}?t={token}", follow_redirects=False)
            self.assertEqual(resp.status_code, 200)
            self.assertIn("Radicaci", resp.text)
        finally:
            tear()

    def test_el_correo_nunca_se_muestra_completo(self):
        """Aun con el enlace legitimo, el correo va enmascarado."""
        session, tutela = _sesion_con_tutela(email="juan.perez@correo.com")
        cliente, tear = _client(session)
        try:
            token = pagos_mod.token_pago(tutela.id)
            resp = cliente.get(f"/pago/{tutela.id}?t={token}", follow_redirects=False)
            self.assertNotIn("juan.perez@correo.com", resp.text)
            self.assertIn("***@correo.com", resp.text)
        finally:
            tear()

    def test_el_403_no_revela_si_la_tutela_existe(self):
        session, _ = _sesion_con_tutela()
        cliente, tear = _client(session)
        try:
            con_id = cliente.get("/pago/1", follow_redirects=False)
            sin_id = cliente.get("/pago/999999", follow_redirects=False)
            self.assertEqual(con_id.status_code, sin_id.status_code)
            self.assertNotIn("Tutela no encontrada", con_id.text)
        finally:
            tear()

    def test_sin_token_no_crea_preferencia_de_mercado_pago(self):
        session, tutela = _sesion_con_tutela()
        cliente, tear = _client(session)
        try:
            with mock.patch.object(
                pagos_mod, "crear_preferencia_checkout"
            ) as mock_pref:
                cliente.get(f"/pago/{tutela.id}", follow_redirects=False)
            mock_pref.assert_not_called()
        finally:
            tear()

    def test_sin_token_no_escribe_en_la_tutela(self):
        """El GET sin token tampoco puede meter mercadopago_reference."""
        session, tutela = _sesion_con_tutela()
        cliente, tear = _client(session)
        try:
            with mock.patch.object(pagos_mod, "crear_preferencia_checkout"):
                cliente.get(f"/pago/{tutela.id}", follow_redirects=False)
            session.expire_all()
            datos = __import__("json").loads(tutela.datos_json)
            self.assertNotIn("mercadopago_reference", datos)
        finally:
            tear()


class TestVerificarPagoProtegido(unittest.TestCase):
    def setUp(self):
        settings.secret_key = "clave-de-prueba-para-el-token"

    def test_verificar_sin_token_esta_bloqueado(self):
        session, tutela = _sesion_con_tutela()
        cliente, tear = _client(session)
        try:
            resp = cliente.post(f"/pago/{tutela.id}/verificar")
            self.assertEqual(resp.status_code, 403)
        finally:
            tear()

    def test_verificar_con_token_ajeno_esta_bloqueado(self):
        session, tutela = _sesion_con_tutela()
        cliente, tear = _client(session)
        try:
            resp = cliente.post(f"/pago/{tutela.id}/verificar?t={'a' * 32}")
            self.assertEqual(resp.status_code, 403)
        finally:
            tear()


class TestMascarEmail(unittest.TestCase):
    def test_mascara_el_local_y_deja_el_dominio(self):
        self.assertEqual(pagos_mod.enmascarar_email("juan.perez@correo.com"), "j***@correo.com")

    def test_local_corto(self):
        self.assertEqual(pagos_mod.enmascarar_email("j@correo.com"), "j***@correo.com")

    def test_texto_largo_se_recorta(self):
        self.assertEqual(
            pagos_mod.enmascarar_email("unnombrelarguisimodeverdad@gmail.com"),
            "u***@gmail.com",
        )

    def test_vacio_o_invalido_no_rompe(self):
        self.assertEqual(pagos_mod.enmascarar_email(""), "")
        self.assertEqual(pagos_mod.enmascarar_email("sindoominio"), "")


if __name__ == "__main__":
    unittest.main()