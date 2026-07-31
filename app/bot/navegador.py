import logging
import uuid

from app.bot.browser import BrowserManager
from app.config import settings
from app.utils.file_utils import path_constancia

logger = logging.getLogger(__name__)


class RadicadorBot:
    """
    SELECTORES REALES DEL PORTAL RAMA JUDICIAL (procesojudicial.ramajudicial.gov.co/TutelaEnLinea):
    
    Modal inicial:
      - Checkbox aceptar terminos: #enableCheckbox
      - Boton continuar: button con texto "Continuar" en jquery-confirm
    
    Paso 1 - Lugar de envio:
      - Departamento: #DdlDepartamento (select, carga AJAX via GetDepartamentos())
      - Ciudad: #DDlCiudad (select, carga AJAX via GetCiudades())
    
    Paso 2 - Tipo de registro:
      - Radio Tutela: #RdbTutela
      - Radio Habeas: #RdbHabeas
    
    Paso 3 - Lugar de hechos (se muestra al seleccionar Tutela):
      - Departamento hechos: #DdlDepartamentoHechos
      - Ciudad hechos: #DDlCiudadHechos
    
    Paso 4 - Accionante:
      - Tipo documento: #DDlTipodocumento (select, carga AJAX via GetTipoDocumento())
      - Numero documento: #NumeroDocumento
      - Primer nombre: #PrimerNombre
      - Segundo nombre: #SegundoNombre
      - Primer apellido: #PrimerApellido
      - Segundo apellido: #SegundoApellido
      - Telefono: #Telefono
      - Tipo discapacidad: #DDlTipodiscapacidad (select "Sin discapacidad")
      - Email: #Email
      - Confirmar email: #IdEmail1
      - Boton validar correo: #btnValidar
    
    Paso 5 - Accionado:
      - Tipo persona: #DDlTipoSujeto (select: "Juridica" o "Natural")
      - Si juridico: #NombreJuridicoAcc
      - Si natural: #PrimerNombreAcc, #PrimerApellidoAcc, etc.
      - Boton agregar: #btnAddAccionado
    
    Paso 6 - Derechos:
      - Select derecho: #DDLDerechos (carga AJAX)
      - Medida provisional SI: #RdbSiMedida / NO: #RdbNoMedida
      - Boton agregar: #btnAdd
    
    Paso 7 - Archivos:
      - Tipo archivo: #DDlTipoArchivo (select "Tutela")
      - Input file: #ArchivoFile0
      - Boton agregar: #btnAddfile
    
    Paso 8 - Juramento:
      - Checkbox: #CbManifiesto
    
    Paso 9 - Captcha:
      - Google reCAPTCHA: data-sitekey="6LcnkeUUAAAAAIzytmwnkjif8k066vQVR6EKXFw0"
      - Servicio necesario: 2Captcha o similar para resolverlo
    
    Paso 10 - Enviar:
      - Boton enviar: #enviar (llama a comprobarradio())
    
    NOTA: El portal usa ASP.NET con __RequestVerificationToken.
    Los selects se cargan via AJAX, hay que esperar las llamadas antes de interactuar.
    """

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
        # TODO: Implementar con selectores reales del portal
        # 1. Aceptar modal terminos (#enableCheckbox + boton Continuar)
        # 2. Seleccionar DdlDepartamento y DDlCiudad
        # 3. Click RdbTutela
        # 4. Llenar datos accionante
        # 5. Click btnValidar y llenar IdEmail1
        # 6. Agregar accionado
        # 7. Agregar derechos + medida provisional
        # 8. Subir PDF (#ArchivoFile0)
        # 9. Check CbManifiesto
        # 10. Resolver reCAPTCHA con 2Captcha
        # 11. Click #enviar
        return {"ok": True, "requiere_token": False}

    async def ingresar_token(self, token: str) -> dict:
        if settings.simulate_bot:
            return {"ok": True}
        # Portal no usa token SMS, usa reCAPTCHA
        return {"ok": False, "error": "Portal usa reCAPTCHA, no token SMS"}

    async def subir_archivo(self, ruta_pdf: str):
        if settings.simulate_bot:
            return
        try:
            await self.page.set_input_files("#ArchivoFile0", ruta_pdf)
            await self.page.wait_for_timeout(1000)
        except Exception as e:
            logger.error(f"Error subiendo archivo: {e}")

    async def enviar_y_descargar(self) -> dict:
        if settings.simulate_bot:
            ruta = path_constancia()
            with open(ruta, "w") as f:
                f.write("SIMULACION CONSTANCIA")
            num = "1100101020230" + str(uuid.uuid4().hex[:10])
            return {"path": ruta, "num_radicado": num}
        try:
            await self.page.click("#enviar")
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
            except Exception as e:
                logger.error(f"Error obteniendo num_radicado: {e}")

            return {"path": ruta_constancia, "num_radicado": num_radicado.strip()}

        except Exception as e:
            logger.error(f"Error en enviar_y_descargar: {e}")
            return {"path": None, "num_radicado": None, "error": str(e)}

    async def cerrar(self):
        if settings.simulate_bot:
            return
        if self.page:
            await self.page.close()
