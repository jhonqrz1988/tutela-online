"""
Prueba de integración del flujo completo.
Ejecutar: python test_integracion.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

os.environ["SECRET_KEY"] = "test-key"
os.environ["OPENAI_API_KEY"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""


def test_flujo_completo():
    from app.database import SessionLocal, init_db
    from app.models.tutela import Tutela
    from app.models.user import User
    from app.services.documento_service import generar_pdf

    # 1. Inicializar DB
    init_db()
    print("[OK] DB inicializada")

    # 2. Crear usuario (eliminar uno anterior con el mismo teléfono si existe)
    with SessionLocal() as s:
        s.query(Tutela).filter(Tutela.user_id == s.query(User.id).filter(User.telefono == "whatsapp:+573001234567")).delete(synchronize_session="fetch")
        s.query(User).filter(User.telefono == "whatsapp:+573001234567").delete(synchronize_session="fetch")
        s.commit()
        user = User(telefono="whatsapp:+573001234567", nombre="Test User", consentimiento=True)
        s.add(user)
        s.commit()
        s.refresh(user)
        print(f"[OK] Usuario creado: id={user.id}")

        # 3. Crear tutela con datos
        datos = {
            "tipo": "salud",
            "accionante_nombre": "Carlos Restrepo",
            "accionante_cedula": "12345678",
            "accionante_tipo_doc": "CC",
            "accionante_telefono": "3001234567",
            "accionante_email": "carlos@email.com",
            "accionado": "EPS Sanitas",
            "hechos": "El dia 15 de enero de 2026 acudi a mi EPS Sanitas solicitando una cita con "
                       "medicina general por fuertes dolores de cabeza. Me negaron la cita argumentando "
                       "que no hay disponibilidad y me dieron cita para 3 meses despues. Mis dolores "
                       "son insoportables y no puedo esperar ese tiempo.",
            "derechos_vulnerados": ["Derecho a la Salud (Art. 49 CP)", "Derecho a la Vida Digna (Art. 11 CP)"],
            "ciudad": "Bogota",
            "peticion": "Solicito al senor Juez ordenar a EPS Sanitas que me asigne una cita con "
                        "medicina general en un plazo maximo de 48 horas."
        }
        tutela = Tutela(
            user_id=user.id,
            tipo="salud",
            estado="datos_listos",
            datos_json=json.dumps(datos),
        )
        s.add(tutela)
        s.commit()
        s.refresh(tutela)
        print(f"[OK] Tutela creada: id={tutela.id}, estado={tutela.estado}")

        # 4. Generar PDF
        ruta_pdf = generar_pdf(datos, None)
        tutela.pdf_path = ruta_pdf
        tutela.estado = "pdf_generado"
        s.commit()
        print(f"[OK] PDF generado: {ruta_pdf}")
        assert os.path.exists(ruta_pdf), "El PDF no se creo"
        assert os.path.getsize(ruta_pdf) > 1000, "El PDF esta vacio"

        # 5. Marcar como pendiente de radicacion
        tutela.estado = "pendiente_radicacion"
        s.commit()
        print("[OK] Tutela marcada para radicacion")

        # 6. Verificar scheduler
        from app.tasks.scheduler import scheduler
        jobs = scheduler.get_jobs()
        print(f"[OK] Scheduler activo: {len(list(jobs))} trabajo(s) registrado(s)")

        print("\n[OK] TODAS LAS PRUEBAS PASARON")


if __name__ == "__main__":
    test_flujo_completo()
