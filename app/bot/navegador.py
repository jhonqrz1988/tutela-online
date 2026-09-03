import logging
import uuid
from pathlib import Path

import aiofiles

from app.bot.browser import BrowserManager
from app.config import settings
from app.utils.file_utils import path_constancia

logger = logging.getLogger(__name__)

TYPE_DELAY = 50


def _separar_nombre(nombre_completo: str) -> dict:
    """Separa un nombre completo colombiano en partes.

    Asume formato: [PrimerNombre] [SegundoNombre] [PrimerApellido] [SegundoApellido]
    Maneja 2, 3 o 4 partes.
    """
    partes = nombre_completo.strip().split()
    if len(partes) == 1:
        return {"primer_nombre": partes[0], "segundo_nombre": "", "primer_apellido": "", "segundo_apellido": ""}
    if len(partes) == 2:
        return {"primer_nombre": partes[0], "segundo_nombre": "", "primer_apellido": partes[1], "segundo_apellido": ""}
    if len(partes) == 3:
        return {"primer_nombre": partes[0], "segundo_nombre": partes[1], "primer_apellido": partes[2], "segundo_apellido": ""}
    return {"primer_nombre": partes[0], "segundo_nombre": partes[1], "primer_apellido": partes[2], "segundo_apellido": " ".join(partes[3:])}


class RadicadorBot:
    """Bot de Playwright para radicar tutelas en el portal de la Rama Judicial.

    SELECTORES REALES DEL PORTAL (procesojudicial.ramajudicial.gov.co/TutelaEnLinea):

    Modal inicial:
      - Checkbox aceptar terminos: #enableCheckbox
      - Boton continuar: button con texto "Continuar" en jquery-confirm

    Paso 1 - Lugar de envio:
      - Departamento: #DdlDepartamento (select, carga AJAX via GetDepartamentos())
      - Ciudad: #DDlCiudad (select, carga AJAX via GetCiudades())

    Paso 2 - Tipo de registro:
      - Radio Tutela: #RdbTutela

    Paso 3 - Lugar de hechos:
      - Departamento hechos: #DdlDepartamentoHechos
      - Ciudad hechos: #DDlCiudadHechos

    Paso 4 - Accionante:
      - Tipo documento: #DDlTipodocumento (select)
      - Numero documento: #NumeroDocumento
      - Primer nombre: #PrimerNombre
      - Segundo nombre: #SegundoNombre
      - Primer apellido: #PrimerApellido
      - Segundo apellido: #SegundoApellido
      - Telefono: #Telefono
      - Tipo discapacidad: #DDlTipodiscapacidad
      - Email: #Email
      - Confirmar email: #IdEmail1
      - Boton validar correo: #btnValidar

    Paso 5 - Accionado:
      - Tipo persona: #DDlTipoSujeto
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
      - Google reCAPTCHA v2: sitekey="6LcnkeUUAAAAAIzytmwnkjif8k066vQVR6EKXFw0"

    Paso 10 - Enviar:
      - Boton enviar: #enviar (llama a comprobarradio())
    """

    def __init__(self):
        self.page = None
        base = settings.storage_dir or "storage"
        self._screenshot_dir = Path(base) / "screenshots"
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    async def iniciar(self):
        if settings.simulate_bot:
            return
        self.page = await BrowserManager.new_page()

    async def navegar_portal(self):
        if settings.simulate_bot:
            return
        await self.page.goto(settings.rama_judicial_url, wait_until="networkidle")
        await self.page.wait_for_timeout(2000)

    async def _esperar_select_ajax(self, selector: str, timeout_ms: int = 10000):
        """Espera a que un select AJAX tenga opciones disponibles."""
        try:
            await self.page.wait_for_function(
                f"() => document.querySelectorAll('{selector} option').length > 1",
                timeout=timeout_ms,
            )
        except Exception:
            logger.warning(f"Timeout esperando opciones en {selector}")

    async def _seleccionar_select(self, selector: str, label: str):
        """Selecciona un option por texto visible en un select, manejando AJAX y mayúsculas.

        Busca primero por JS case-insensitive + alias para evitar timeouts de select_option.
        """
        await self._esperar_select_ajax(selector)

        # Buscar el value por JS (case-insensitive / parcial / alias)
        match_value = await self.page.evaluate(
            """([sel, lbl]) => {
                const ALIASES = {
                    'cc': 'cédula de ciudadanía',
                    'ce': 'cédula de extranjería',
                    'ti': 'tarjeta de identidad',
                    'pa': 'pasaporte',
                    'pep': 'permiso especial de permanencia',
                };
                const s = document.querySelector(sel);
                if (!s) return null;
                const lower = lbl.toLowerCase().trim();
                const expanded = ALIASES[lower] || lower;
                for (const opt of s.options) {
                    const txt = opt.text.trim().toLowerCase();
                    if (txt === expanded || txt === lower ||
                        txt.includes(expanded) || expanded.includes(txt) ||
                        txt.includes(lower) || lower.includes(txt)) {
                        return opt.value;
                    }
                }
                return null;
            }""",
            [selector, label],
        )

        if match_value is not None:
            await self.page.select_option(selector, value=match_value)
        else:
            logger.warning(f"No se encontró '{label}' en {selector}")

    async def _type(self, selector: str, texto: str):
        """Escribe texto carácter por carácter (evita restricción de paste)."""
        await self.page.type(selector, texto or "", delay=TYPE_DELAY)

    async def _type_existing(self, selector: str, texto: str):
        """Escribe en un campo que puede ya tener contenido (limpia primero)."""
        await self.page.fill(selector, "")
        await self.page.type(selector, texto or "", delay=TYPE_DELAY)

    async def _cerrar_jconfirm(self):
        """Cierra cualquier modal jconfirm abierto."""
        try:
            await self.page.evaluate("""
                () => {
                    const modals = document.querySelectorAll('.jconfirm');
                    modals.forEach(m => {
                        const btn = m.querySelector('.btn');
                        if (btn) btn.click();
                        else m.remove();
                    });
                }
            """)
            await self.page.wait_for_timeout(500)
        except Exception:
            pass

    async def _js_click(self, selector: str):
        """Click via JS, ignora overlays tipo jconfirm."""
        await self.page.evaluate(f"document.querySelector('{selector}')?.click()")
        await self.page.wait_for_timeout(500)

    async def _modal_aceptar_terminos(self):
        """Paso 0: Aceptar modal de términos si aparece."""
        try:
            checkbox = await self.page.query_selector("#enableCheckbox")
            if checkbox:
                await checkbox.click()
                await self.page.wait_for_timeout(500)
                btn = await self.page.query_selector("button:has-text('Continuar')")
                if btn:
                    await btn.click()
                    await self.page.wait_for_timeout(1500)
                else:
                    await self.page.click(".jconfirm-buttons button:first-child")
                    await self.page.wait_for_timeout(1500)
        except Exception as e:
            logger.warning(f"Modal de términos no encontrado o ya aceptado: {e}")

    async def _paso_lugar_envio(self, datos: dict):
        """Paso 1: Departamento y ciudad de envío."""
        depto = datos.get("departamento", "")
        ciudad = datos.get("ciudad", "")

        if depto:
            await self._seleccionar_select("#DdlDepartamento", depto)
            await self.page.wait_for_timeout(2000)

        if ciudad:
            await self._seleccionar_select("#DDlCiudad", ciudad)

    async def _paso_tipo_registro(self):
        """Paso 2: Seleccionar Tutela."""
        await self.page.click("#RdbTutela")
        await self.page.wait_for_timeout(1000)

    async def _paso_lugar_hechos(self, datos: dict):
        """Paso 3: Departamento y ciudad de los hechos."""
        depto = datos.get("departamento", "")
        ciudad = datos.get("ciudad", "")

        if depto:
            await self._seleccionar_select("#DdlDepartamentoHechos", depto)
            await self.page.wait_for_timeout(2000)

        if ciudad:
            await self._seleccionar_select("#DDlCiudadHechos", ciudad)

    async def _paso_accionante(self, datos: dict) -> bool:
        """Paso 4: Datos del accionante. Retorna True si requiere código de email."""
        nombre = _separar_nombre(datos.get("accionante_nombre", ""))

        # Tipo documento
        tipo_doc = datos.get("accionante_tipo_doc", "CC")
        await self._seleccionar_select("#DDlTipodocumento", tipo_doc)
        await self.page.wait_for_timeout(500)

        # Número documento
        await self._type("#NumeroDocumento", datos.get("accionante_cedula", ""))

        # Nombres (typing lento para evitar bloqueo de paste)
        await self._type("#PrimerNombre", nombre["primer_nombre"])
        await self._type("#SegundoNombre", nombre["segundo_nombre"])
        await self._type("#PrimerApellido", nombre["primer_apellido"])
        await self._type("#SegundoApellido", nombre["segundo_apellido"])

        # Teléfono
        await self._type("#Telefono", datos.get("accionante_telefono", ""))

        # Tipo discapacidad (si el usuario declaró una, se usa; si no, "No Aplica")
        discapacidad = datos.get("accionante_discapacidad") or "No Aplica"
        try:
            await self._seleccionar_select("#DDlTipodiscapacidad", discapacidad)
        except Exception:
            logger.warning("No se pudo seleccionar tipo discapacidad")

        # Email
        email = datos.get("accionante_email", "")
        await self._type("#Email", email)

        # Click validar correo — activa verificación
        await self._cerrar_jconfirm()
        await self._js_click("#btnValidar")
        await self.page.wait_for_timeout(1000)

        # Detección condicional: el portal SOLO pide código de verificación
        # cuando el correo no está registrado. Si #IdEmail1 (input del código)
        # no aparece, el email ya estaba verificado y se continúa directo.
        try:
            campo_codigo = await self.page.query_selector("#IdEmail1")
            if campo_codigo is None:
                logger.info("Correo ya verificado, no se requiere código de email")
                return False
            visible = await campo_codigo.is_visible()
            return bool(visible)
        except Exception:
            # Ambiguo/error: pedir el código (mejor que radicar un email sin verificar)
            logger.warning("No se pudo detectar campo de verificación de email; se asume que aplica")
            return True

    async def ingresar_codigo_email(self, codigo: str):
        """Ingresa el código de verificación de correo en #IdEmail1."""
        await self._type_existing("#IdEmail1", codigo)
        await self.page.wait_for_timeout(500)

    async def _paso_accionado(self, datos: dict):
        """Paso 5: Agregar accionado."""
        tipo = datos.get("accionado_tipo", "juridica")
        await self._seleccionar_select("#DDlTipoSujeto", "Jurídica" if tipo == "juridica" else "Natural")
        await self.page.wait_for_timeout(500)

        if tipo == "juridica":
            # Para persona jurídica el portal exige tipo de documento (NIT) y número
            await self._seleccionar_select("#DDlTipodocumentoAccionado", "NIT")
            await self.page.wait_for_timeout(500)
            await self._type("#DocumentodeIdendificacion", datos.get("accionado_nit", ""))
            await self._type("#NombreJuridicoAcc", datos.get("accionado", ""))
            await self._type("#IdDireccion", datos.get("accionado_direccion", "") or "-")
            await self._type("#IdTelefono", datos.get("accionado_telefono", "") or "-")
            await self._type("#IdEmail", datos.get("accionado_email", ""))
        else:
            nombre = _separar_nombre(datos.get("accionado", ""))
            await self._type("#PrimerNombreAcc", nombre["primer_nombre"])
            await self._type("#PrimerApellidoAcc", nombre["primer_apellido"])

        # La acción no involucra menores de edad en el caso estándar
        try:
            await self._js_click("#RdbNoAccionMenores")
        except Exception:
            logger.warning("No se pudo seleccionar 'accionado no involucra menores'")

        await self._cerrar_jconfirm()
        await self._js_click("#btnAddAccionado")
        await self.page.wait_for_timeout(1500)

    async def _paso_derechos(self, datos: dict):
        """Paso 6: Agregar derechos vulnerados y medida provisional."""
        derechos = datos.get("derechos_vulnerados", [])
        for derecho in derechos[:5]:
            try:
                await self._seleccionar_select("#DDLDerechos", derecho)
                await self.page.wait_for_timeout(500)
            except Exception:
                logger.warning(f"No se pudo seleccionar derecho: {derecho}")
                continue

            if datos.get("medida_provisional") == "si":
                await self._js_click("#RdbSiMedida")
            else:
                await self._js_click("#RdbNoMedida")

            await self._cerrar_jconfirm()
            await self._js_click("#btnAdd")
            await self.page.wait_for_timeout(1000)

    async def _paso_archivos(self, ruta_pdf: str):
        """Paso 7: Subir el PDF de la tutela como DEMANDA (obligatorio) y como PRUEBA."""
        if not ruta_pdf:
            return

        # El portal exige el tipo de archivo DEMANDA (obligatorio) para radicar;
        # se sube primero DEMANDA, y el mismo PDF también como PRUEBA.
        for tipo_label in ("DEMANDA", "PRUEBA"):
            try:
                await self._seleccionar_select("#DDlTipoArchivo", tipo_label)
                await self.page.wait_for_timeout(500)

                # limpiar input si quedó archivo previo
                await self.page.evaluate("document.querySelector('#ArchivoFile0').value=''")
                await self.page.set_input_files("#ArchivoFile0", ruta_pdf)
                await self.page.wait_for_timeout(1500)

                await self._cerrar_jconfirm()
                await self._js_click("#btnAddfile")
                await self.page.wait_for_timeout(2000)
            except Exception as e:
                logger.error(f"Error subiendo PDF ({tipo_label}): {e}")

    async def _paso_juramento(self):
        """Paso 8: Marcar juramento."""
        try:
            await self.page.check("#CbManifiesto")
        except Exception:
            await self.page.click("#CbManifiesto")

    async def llenar_formulario(self, datos: dict) -> dict:
        """Llena el formulario del portal de Rama Judicial.

        Retorna:
            ok: True si el formulario se llenó correctamente
            requiere_codigo_email: True si el paso de verificación de email fue alcanzado
            error: str si hubo un error
        """
        if settings.simulate_bot:
            return {"ok": True, "requiere_codigo_email": False}

        try:
            logger.info("Iniciando llenado del formulario del portal...")

            # Paso 0: Modal de términos
            await self._modal_aceptar_terminos()

            # Paso 1: Lugar de envío
            await self._paso_lugar_envio(datos)
            logger.info("Paso 1 completado: lugar de envío")

            # Paso 2: Tipo registro
            await self._paso_tipo_registro()
            logger.info("Paso 2 completado: tipo tutela")

            # Paso 3: Lugar de hechos
            await self._paso_lugar_hechos(datos)
            logger.info("Paso 3 completado: lugar de hechos")

            # Paso 4: Accionante + trigger verificación email
            requiere_codigo = await self._paso_accionante(datos)
            logger.info("Paso 4 completado: accionante + verificación email activada")

            return {"ok": True, "requiere_codigo_email": requiere_codigo}

        except Exception as e:
            logger.error(f"Error en llenar_formulario: {e}")
            return {"ok": False, "error": str(e)}

    async def completar_post_codigo(self, datos: dict, ruta_pdf: str) -> dict:
        """Completa el formulario después de ingresar el código de email.

        Ejecuta pasos 5-10: accionado, derechos, archivos, juramento.
        """
        if settings.simulate_bot:
            return {"ok": True}

        try:
            logger.info("Retomando formulario post-verificación email...")

            # Paso 5: Accionado
            await self._paso_accionado(datos)
            logger.info("Paso 5 completado: accionado")

            # Paso 6: Derechos
            await self._paso_derechos(datos)
            logger.info("Paso 6 completado: derechos")

            # Paso 7: Archivos
            await self._paso_archivos(ruta_pdf)
            logger.info("Paso 7 completado: archivos")

            # Paso 8: Juramento
            await self._paso_juramento()
            logger.info("Paso 8 completado: juramento")

            return {"ok": True}

        except Exception as e:
            logger.error(f"Error en completar_post_codigo: {e}")
            return {"ok": False, "error": str(e)}

    async def resolver_recaptcha(self) -> bool:
        """Resuelve el reCAPTCHA v2 del portal usando 2Captcha.

        Retorna True si se resolvió correctamente.
        """
        if settings.simulate_bot:
            return True

        from app.services.captcha_service import resolver_recaptcha_v2

        page_url = settings.rama_judicial_url
        token = await resolver_recaptcha_v2(page_url)

        if not token:
            logger.error("No se pudo resolver el reCAPTCHA")
            return False

        # Insertar el token en el textarea oculto de reCAPTCHA
        try:
            await self.page.evaluate(f"""
                document.getElementById('g-recaptcha-response').value = '{token}';
                // Disparar callback de reCAPTCHA si existe
                if (typeof ___grecaptcha_cfg !== 'undefined') {{
                    var clients = ___grecaptcha_cfg.clients;
                    for (var key in clients) {{
                        var client = clients[key];
                        if (client && client.T) {{
                            client.T(token);
                        }}
                    }}
                }}
            """)
            logger.info("Token reCAPTCHA insertado en el formulario")
            return True
        except Exception as e:
            logger.error(f"Error insertando token reCAPTCHA: {e}")
            return False

    async def enviar_y_descargar(self) -> dict:
        """Paso 10: Envía el formulario y descarga la constancia."""
        if settings.simulate_bot:
            ruta = path_constancia()
            async with aiofiles.open(ruta, "w") as f:
                await f.write("SIMULACION CONSTANCIA")
            num = "1100101020230" + str(uuid.uuid4().hex[:10])
            return {"path": ruta, "num_radicado": num}

        try:
            await self._cerrar_jconfirm()
            await self._js_click("#enviar")
            await self.page.wait_for_timeout(5000)

            # Descargar constancia
            ruta_constancia = path_constancia()
            try:
                async with self.page.expect_download(timeout=15000) as download_info:
                    await self.page.click("#btnDescargarConstancia")
                download = await download_info.value
                await download.save_as(ruta_constancia)
            except Exception:
                logger.warning("No se pudo descargar constancia, intentando screenshot")
                ruta_constancia = str(await self.tomar_screenshot("constancia"))

            # Extraer número de radicado
            num_radicado = ""
            try:
                elemento = await self.page.query_selector("#numRadicado")
                if elemento:
                    num_radicado = (await elemento.text_content() or "").strip()
            except Exception as e:
                logger.error(f"Error obteniendo num_radicado: {e}")

            return {"path": ruta_constancia, "num_radicado": num_radicado}

        except Exception as e:
            logger.error(f"Error en enviar_y_descargar: {e}")
            return {"path": None, "num_radicado": None, "error": str(e)}

    async def tomar_screenshot(self, nombre: str = "radicacion") -> Path:
        """Toma screenshot de la página actual y retorna la ruta."""
        if settings.simulate_bot:
            ruta = self._screenshot_dir / f"{nombre}_sim.png"
            async with aiofiles.open(ruta, "w") as f:
                await f.write("SIMULACION SCREENSHOT")
            return ruta

        ruta = self._screenshot_dir / f"{nombre}.png"
        await self.page.screenshot(path=str(ruta), full_page=True)
        return ruta

    async def cerrar(self):
        if settings.simulate_bot:
            return
        if self.page:
            await self.page.close()
