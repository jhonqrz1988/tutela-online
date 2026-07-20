import uuid

from app.bot.browser import BrowserManager
from app.config import settings
from app.utils.file_utils import path_constancia


class RadicadorBot:

    def __init__(self):
        self.page = None

    async def iniciar(self):
        if settings.simulate_bot:
            return
        self.page = await BrowserManager.new_page()

    async def navegar_portal(self):
        if settings.simulate_bot:
            return
        await self.page.goto(settings.rama_judicial_url, wait_until="networkidle")
        await self.page.wait_for_timeout(2000)

    async def llenar_formulario(self, datos: dict) -> dict:
        if settings.simulate_bot:
            return {"ok": True, "requiere_token": False}
        page = self.page
        mapeo = {
            "tipo_tutela": "#tipoTutela",
            "ciudad": "#ciudad",
            "accionante_nombre": "#nombreAccionante",
            "accionante_cedula": "#cedulaAccionante",
            "accionante_telefono": "#telefonoAccionante",
            "accionante_email": "#emailAccionante",
            "accionado": "#entidadAccionada",
            "derechos": "#derechosVulnerados",
        }
        for campo, valor in datos.items():
            selector = mapeo.get(campo)
            if selector and valor:
                try:
                    await page.fill(selector, valor)
                except Exception:
                    pass
        return {"ok": True, "requiere_token": True}

    async def ingresar_token(self, token: str) -> dict:
        if settings.simulate_bot:
            return {"ok": True}
        try:
            await self.page.fill("#codigoVerificacion", token)
            await self.page.click("#btnVerificar")
            await self.page.wait_for_timeout(2000)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def subir_archivo(self, ruta_pdf: str):
        if settings.simulate_bot:
            return
        try:
            await self.page.set_input_files("#archivoTutela", ruta_pdf)
            await self.page.wait_for_timeout(1000)
        except Exception:
            pass

    async def enviar_y_descargar(self) -> dict:
        if settings.simulate_bot:
            ruta = path_constancia()
            with open(ruta, "w") as f:
                f.write("SIMULACION CONSTANCIA")
            num = "1100101020230" + str(uuid.uuid4().hex[:10])
            return {"path": ruta, "num_radicado": num}
        try:
            await self.page.click("#btnRadicar")
            await self.page.wait_for_timeout(5000)

            ruta_constancia = path_constancia()
            async with self.page.expect_download() as download_info:
                await self.page.click("#btnDescargarConstancia")
            download = await download_info.value
            await download.save_as(ruta_constancia)

            num_radicado = ""
            try:
                elemento = await self.page.query_selector("#numRadicado")
                if elemento:
                    num_radicado = await elemento.text_content() or ""
            except Exception:
                pass

            return {"path": ruta_constancia, "num_radicado": num_radicado.strip()}

        except Exception as e:
            return {"path": None, "num_radicado": None, "error": str(e)}

    async def cerrar(self):
        if settings.simulate_bot:
            return
        if self.page:
            await self.page.close()
