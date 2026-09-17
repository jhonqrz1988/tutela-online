"""Crea una tutela de PRUEBA lista para radicar (estado pago_confirmado).

Uso:
  1. Edita el dict DATOS con los datos reales de la persona de prueba
     (cédula y correo NUEVOS: el portal guarda un borrador por cédula y un
     correo ya registrado cambia el flujo de verificación).
  2. Ejecuta:  .\\.venv\\Scripts\\python.exe seed_tutela_prueba.py
  3. Anota el ID que imprime y dispara la radicación desde el panel admin
     ("Ejecutar bot") o espera al scheduler en horario hábil.

El PDF se genera con los mismos datos (documento_service.generar_pdf), así el
portal recibe un archivo coherente con lo que el bot escribe en el formulario.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal, init_db
from app.models.tutela import Tutela
from app.models.user import User
from app.services.documento_service import generar_pdf

# ---------------------------------------------------------------------------
# DATOS DE LA PERSONA DE PRUEBA — REEMPLAZAR por los datos reales.
# Campos que el pre-vuelo exige: accionante_nombre, accionante_tipo_doc,
# accionante_cedula, accionante_telefono, accionante_email, ciudad,
# departamento, accionado, hechos.
# ---------------------------------------------------------------------------
DATOS = {
    "tipo": "salud",
    # Accionante (la persona que interpone la tutela)
    "accionante_nombre": "REEMPLAZAR Nombre Apellido Apellido",
    "accionante_nombres": "REEMPLAZAR Nombre",
    "accionante_apellidos": "REEMPLAZAR Apellido Apellido",
    "accionante_tipo_doc": "CC",
    "accionante_cedula": "REEMPLAZAR_CEDULA_NUEVA",
    "accionante_telefono": "3000000000",
    "accionante_email": "REEMPLAZAR@correo.com",
    "accionante_discapacidad": "",
    # Lugar
    "departamento": "ANTIOQUIA",
    "ciudad": "MEDELLIN",
    # Accionado (la EPS demandada)
    "accionado": "REEMPLAZAR EPS S.A.",
    "accionado_tipo": "juridica",
    "accionado_nit": "8000000000",
    "accionado_direccion": "-",
    "accionado_telefono": "-",
    "accionado_email": "notificaciones@eps.com.co",
    # Caso
    "hechos": "REEMPLAZAR: relato de los hechos (servicio/medicamento negado).",
    "derechos_vulnerados": ["Salud", "Vida"],
    "medida_provisional": "no",
}

# Teléfono WhatsApp del usuario de prueba (dueño de la tutela). Se usa para
# los avisos del bot (código de email, radicado). Formato: 57XXXXXXXXXX.
TELEFONO_PRUEBA = "570000000000"


def seed_tutela_prueba():
    if any(str(v).startswith("REEMPLAZAR") for v in DATOS.values()):
        print("Edita primero el dict DATOS con los datos reales de la prueba.")
        return

    init_db()
    pdf_path = generar_pdf(DATOS, None)

    session = SessionLocal()
    try:
        user = session.query(User).filter(User.telefono == TELEFONO_PRUEBA).first()
        if user is None:
            user = User(
                telefono=TELEFONO_PRUEBA,
                nombre=DATOS["accionante_nombre"],
                email=DATOS["accionante_email"],
                estado="activo",
                consentimiento=True,
            )
            session.add(user)
            session.flush()

        tutela = Tutela(
            user_id=user.id,
            tipo=DATOS.get("tipo", "salud"),
            estado="pago_confirmado",
            datos_json=json.dumps(DATOS, ensure_ascii=False),
            pdf_path=pdf_path,
        )
        session.add(tutela)
        session.commit()
        print(f"Tutela de prueba creada: id={tutela.id}  estado={tutela.estado}")
        print(f"  accionante: {DATOS['accionante_nombre']}  CC {DATOS['accionante_cedula']}")
        print(f"  PDF: {pdf_path}")
        print("Dispara la radicación desde el panel admin ('Ejecutar bot') o el scheduler.")
    finally:
        session.close()


if __name__ == "__main__":
    seed_tutela_prueba()
