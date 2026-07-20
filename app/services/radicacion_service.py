import json

from sqlalchemy import select

from app.bot.navegador import RadicadorBot
from app.database import SessionLocal
from app.models.radicacion import Radicacion
from app.models.tutela import Tutela


async def iniciar_radicacion(
    tutela_id: int,
    token_usuario: str | None = None,
) -> dict:
    session = SessionLocal()
    try:
        tutela = session.execute(
            select(Tutela).where(Tutela.id == tutela_id)
        ).scalar_one_or_none()
        if not tutela:
            return {"ok": False, "error": "Tutela no encontrada"}

        datos = json.loads(tutela.datos_json or "{}")

        rad = Radicacion(tutela_id=tutela.id, estado="radicando")
        session.add(rad)
        session.commit()

        bot = RadicadorBot()
        try:
            await bot.iniciar()
            await bot.navegar_portal()

            dr = {
                "tipo_tutela": datos.get("tipo", "salud"),
                "ciudad": datos.get("ciudad", ""),
                "accionante_nombre": datos.get("accionante_nombre", ""),
                "accionante_cedula": datos.get("accionante_cedula", ""),
                "accionante_telefono": datos.get("accionante_telefono", ""),
                "accionante_email": datos.get("accionante_email", ""),
                "accionado": datos.get("accionado", ""),
                "derechos": ", ".join(datos.get("derechos_vulnerados", [])),
            }
            res = await bot.llenar_formulario(dr)

            if res.get("requiere_token"):
                if token_usuario:
                    tr = await bot.ingresar_token(token_usuario)
                    if not tr.get("ok"):
                        rad.estado = "token_fallido"
                        rad.ultimo_error = tr.get("error")
                        session.commit()
                        return {"ok": False, "error": "Token inválido"}
                else:
                    rad.estado = "esperando_token"
                    session.commit()
                    return {"ok": True, "requiere_token": True, "radicacion_id": rad.id}

            if tutela.pdf_path:
                await bot.subir_archivo(tutela.pdf_path)

            c = await bot.enviar_y_descargar()

            rad.estado = "radicada"
            rad.num_radicado = c.get("num_radicado")
            rad.constancia_path = c.get("path")
            tutela.estado = "radicada"
            session.commit()

            from app.services.whatsapp_service import enviar_texto
            tel = tutela.user.telefono if tutela.user else None
            if tel:
                enviar_texto(tel, f"✅ Radicada! N: {rad.num_radicado}")

            return {"ok": True, "num_radicado": rad.num_radicado}

        except Exception as e:
            rad.estado = "fallida"
            rad.ultimo_error = str(e)
            rad.intentos = (rad.intentos or 0) + 1
            session.commit()
            return {"ok": False, "error": str(e)}

        finally:
            await bot.cerrar()

    finally:
        session.close()
