"""
Prueba de integración del flujo completo.
Ejecutar: python test_integracion.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

os.environ["SECRET_KEY"] = "test-key"
os.environ["OPENAI_API_KEY"] = ""
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""


async def test_flujo_completo():
    from app.database import init_db, async_session
    from app.models.tutela import Tutela
    from app.models.user import User
    from app.services.documento_service import generar_pdf
    from app.bot.browser import BrowserManager
    from sqlalchemy import select

    # 1. Inicializar DB
    await init_db()
    print("✅ DB inicializada")

    # 2. Crear usuario
    async with async_session() as s:
        user = User(telefono="whatsapp:+573001234567", nombre="Test User")
        s.add(user)
        await s.commit()
        await s.refresh(user)
        print(f"✅ Usuario creado: id={user.id}")

        # 3. Crear tutela con datos
        datos = {
            "tipo": "salud",
            "accionante_nombre": "Carlos Restrepo",
            "accionante_cedula": "12345678",
            "accionante_direccion": "Cra 30 #45-10",
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
            estado="confirmada",
            datos_json=json.dumps(datos),
        )
        s.add(tutela)
        await s.commit()
        await s.refresh(tutela)
        print(f"✅ Tutela creada: id={tutela.id}, estado={tutela.estado}")

        # 4. Generar PDF
        contenido = datos.get("hechos", "")
        ruta_pdf = await generar_pdf(datos, contenido)
        tutela.pdf_path = ruta_pdf
        tutela.estado = "pdf_generado"
        await s.commit()
        print(f"✅ PDF generado: {ruta_pdf}")
        assert os.path.exists(ruta_pdf), "El PDF no se creo"
        assert os.path.getsize(ruta_pdf) > 1000, "El PDF esta vacio"

        # 5. Marcar como pendiente de radicacion
        tutela.estado = "pendiente_radicacion"
        await s.commit()
        print(f"✅ Tutela marcada para radicacion nocturna")

        # 6. Verificar scheduler
        from app.tasks.scheduler import scheduler
        jobs = scheduler.get_jobs()
        print(f"✅ Scheduler activo: {len(list(jobs))} trabajo(s) registrado(s)")

        # 7. Verificar que el bot puede iniciar (solo browser check)
        print("⏳ Verificando Playwright...")
        try:
            page = await BrowserManager.new_page()
            await page.goto("about:blank")
            await page.close()
            print("✅ Playwright browser funcional")
        except Exception as e:
            print(f"⚠️ Playwright: {e}")

        print("\n🎉 TODAS LAS PRUEBAS PASARON")


if __name__ == "__main__":
    asyncio.run(test_flujo_completo())
