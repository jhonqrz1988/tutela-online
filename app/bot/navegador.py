import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import uuid
from pathlib import Path

import aiofiles

from app.bot.browser import BrowserManager
from app.config import settings
from app.utils.file_utils import path_constancia

logger = logging.getLogger(__name__)

TYPE_DELAY = 50

# Tiempo máximo esperando el campo del código de email (antes colgaba el
# loop de Playwright sin límite y la radicación quedaba en 'continuando').
ESPERA_CODIGO_SELECTOR_MS = 20000
# El portal resuelve los nombres del accionante por AJAX al escribir la cédula
# (autofill del borrador que guarda para ese documento) y puede tardar en pisar
# los campos tras nuestra escritura. Antes de re-escribir la identidad se espera
# a que ese AJAX se asiente, o el autofill vuelve a borrar lo que escribimos
# (queja de prod: "E  Ramirez Montoya" y tipo doc en "Seleccione...").
AUTOFILL_SETTLE_MS = 1500

# Tope en MS para sonDEar que el autofill del portal ya llenó los nombres tras
# escribir la cédula (puede tardar más de AUTOFILL_SETTLE_MS; se espera hasta
# que aparezcan o este tope).
AUTOFILL_POLL_MAX_MS = 7000

# ¿Está abierto el CAJÓN del código de verificación? Un overlay visible
# (jquery-confirm `.jconfirm`, `.modal`, `[role=dialog]`) con un input
# habilitado es el indicador real: `#IdEmail1` es el 'confirmar correo' y
# permanece disabled hasta validar (NUNCA es el campo del código).
_JS_CAJON_ABIERTO = """() => {
    const overlays = Array.from(document.querySelectorAll('.jconfirm, .modal, [role="dialog"]'));
    return overlays.some(o => {
        if (o.offsetParent === null && o.style.display === 'none') return false;
        return Array.from(o.querySelectorAll('input')).some(i =>
            !i.disabled && !i.readOnly && i.offsetWidth > 0 && i.offsetHeight > 0);
    });
}"""

# Selector del INPUT del código dentro del cajón: el único input visible y
# habilitado de los overlays. Devuelve un selector CSS usable o null si es
# ambiguo (varios inputs) — mejor no escribir a ciegas.
_JS_SELECTOR_CODIGO = """() => {
    const overlays = Array.from(document.querySelectorAll('.jconfirm, .modal, [role="dialog"]'));
    const scope = overlays.find(o => o.offsetParent !== null || o.style.display !== 'none');
    if (!scope) return null;
    const candidatos = Array.from(scope.querySelectorAll('input')).filter(i => {
        if (i.disabled || i.readOnly) return false;
        const st = window.getComputedStyle(i);
        return i.offsetWidth > 0 && i.offsetHeight > 0 &&
               st.visibility !== 'hidden' && st.display !== 'none';
    });
    if (candidatos.length !== 1) return null;
    const c = candidatos[0];
    if (c.id) return '#' + CSS.escape(c.id);
    if (c.name) return '[name="' + CSS.escape(c.name) + '"]';
    return null;
}"""

# Texto del overlay visible (cajón/modal) si hay uno — para detectar tras
# pulsar 'Enviar' si el portal rechazó la tutela (ej. "debe seleccionar al
# menos un derecho") en vez de radicarla, y no declararla radicada por error.
_JS_OVERLAY_TEXTO = """() => {
    const overlays = Array.from(document.querySelectorAll('.jconfirm, .modal, [role="dialog"]'));
    const visible = overlays.find(o => o.offsetParent !== null || o.style.display !== 'none');
    if (!visible) return "";
    return (visible.textContent || "").trim();
}"""

# Lee los valores REALES del formulario del accionante tras llenarlo (para
# diagnosticar en el panel si el tipo de documento / nombres no aplicaron).
_JS_READBACK_ACCIONANTE = """() => {
    const v = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return '';
        if (el.tagName === 'SELECT') {
            return el.selectedIndex >= 0 && el.options[el.selectedIndex]
                ? el.options[el.selectedIndex].text.trim() : '';
        }
        return el.value || '';
    };
    return {
        tipo_doc: v('#DDlTipodocumento'),
        numero: v('#NumeroDocumento'),
        primer_nombre: v('#PrimerNombre'),
        segundo_nombre: v('#SegundoNombre'),
        primer_apellido: v('#PrimerApellido'),
        segundo_apellido: v('#SegundoApellido'),
        telefono: v('#Telefono'),
        email: v('#Email'),
    };
}"""

# Opciones del dropdown de derechos del portal (diagnóstico cuando el bot
# no encuentra la categoría mapeada desde los artículos de la IA).
_JS_DERECHOS_OPCIONES = """() =>
    Array.from(document.querySelectorAll('#DDLDerechos option'))
        .map(o => o.text.trim())
        .slice(0, 80)"""

# Diagnóstico de la sección accionante: opciones del select de tipo documento
# + estado (existe/visible/habilitado) de cada campo. Cuando el readback sale
# vacío hay que saber si escribimos en campos ocultos, si el select no cargó
# opciones o si la sección cambió de estructura (quejas de prod).
_JS_DIAGNOSTICO_ACCIONANTE = """() => {
    const info = (sel) => {
        const el = document.querySelector(sel);
        if (!el) return { selector: sel, existe: false };
        return {
            selector: sel,
            existe: true,
            disabled: el.disabled,
            readonly: el.readOnly,
            visible: el.offsetWidth > 0 && el.offsetHeight > 0,
        };
    };
    const select = document.querySelector('#DDlTipodocumento');
    const html = (sel) => {
        const el = document.querySelector(sel);
        return el ? el.outerHTML.slice(0, 1200) : null;
    };
    return {
        opciones_tipo_doc: select
            ? Array.from(select.options).map(o => o.text.trim()).slice(0, 30)
            : [],
        duplicados_tipo_doc: document.querySelectorAll('#DDlTipodocumento').length,
        html_tipo_doc: html('#DDlTipodocumento'),
        html_parent_tipo_doc: (() => {
            const el = document.querySelector('#DDlTipodocumento');
            return el && el.parentElement ? el.parentElement.outerHTML.slice(0, 2000) : null;
        })(),
        estado_correo: {
            email: (document.querySelector('#Email') || {}).value || '',
            idemail1: {
                existe: !!document.querySelector('#IdEmail1'),
                disabled: (document.querySelector('#IdEmail1') || {}).disabled,
                value: (document.querySelector('#IdEmail1') || {}).value || '',
                visible: (() => { const e = document.querySelector('#IdEmail1'); return !!e && e.offsetWidth > 0 && e.offsetHeight > 0; })(),
            },
            btn_validar: {
                existe: !!document.querySelector('#btnValidar'),
                disabled: (document.querySelector('#btnValidar') || {}).disabled,
                value: (document.querySelector('#btnValidar') || {}).value || '',
            },
        },
        campos: [
            '#DDlTipodocumento', '#NumeroDocumento', '#PrimerNombre',
            '#SegundoNombre', '#PrimerApellido', '#SegundoApellido',
            '#Telefono', '#Email', '#DDlTipodiscapacidad', '#btnValidar',
        ].map(info),
    };
}"""

# La IA reporta artículos ("Art. 48 CP"); el portal usa categorías por tema.
_MAPEO_ARTICULO_CATEGORIA = {
    "2": "dignidad",
    "11": "vida",
    "12": "vida",
    "13": "igualdad",
    "16": "libre desarrollo",
    "18": "libertad",
    "20": "libertad",
    "21": "igualdad",
    "23": "petición",
    "25": "trabajo",
    "27": "educación",
    "29": "debido proceso",
    "43": "igualdad",
    "48": "salud",
    "49": "salud",
    "51": "vivienda",
    "67": "educación",
    "86": "tutela",
}

# Palabras clave del texto del derecho → categoría del portal (además del
# número de artículo: la IA puede describir el derecho sin citar el artículo).
_CATEGORIA_POR_KEYWORD = (
    ("salud", ("salud", "seguridad social en salud", "eps", "medicamento")),
    ("vida", ("vida",)),
    ("vivienda", ("vivienda",)),
    ("trabajo", ("trabajo", "despido")),
    ("educación", ("educación", "educacion", "estudiante", "colegio", "universidad")),
    ("igualdad", ("igualdad", "discriminación", "discriminacion")),
    ("petición", ("petición", "peticion")),
    ("debido proceso", ("debido proceso",)),
)

# Categoría de respaldo: si ninguna categoría de la IA matchea, se usa esta
# (la más frecuente en tutelas, sobre todo de salud) en vez de abortar la
# radicación. Nunca se inventa un derecho: es una categoría real del portal.
_FALLBACK_DERECHO = "salud"

# Busca el value de un option en un select por texto visible, case-insensitive,
# alias ("cc"->"cédula de ciudadanía") y normalizado ("C.C."=="CC").
_JS_BUSCAR_OPTION_SELECT = """([sel, lbl]) => {
    const ALIASES = {
        'cc': 'cédula de ciudadanía',
        'ce': 'cédula de extranjería',
        'ti': 'tarjeta de identidad',
        'pa': 'pasaporte',
        'pep': 'permiso especial de permanencia',
    };
    const norm = (s) => s.replace(/[^a-z0-9]/g, '');
    const s = document.querySelector(sel);
    if (!s) return null;
    const lower = lbl.toLowerCase().trim();
    const expanded = ALIASES[norm(lower)] || lower;
    const nLower = norm(lower);
    const nExpanded = norm(expanded);
    for (const opt of s.options) {
        const txt = opt.text.trim().toLowerCase();
        const nTxt = norm(txt);
        if (txt === expanded || txt === lower ||
            txt.includes(expanded) || expanded.includes(txt) ||
            txt.includes(lower) || lower.includes(txt) ||
            nTxt === nExpanded || nTxt === nLower ||
            (nExpanded.length > 2 && nTxt.includes(nExpanded)) ||
            (nLower.length > 2 && nTxt.includes(nLower))) {
            return opt.value;
        }
    }
    return null;
}"""


_TEXTO_ERROR_VALIDACION = (
    "debe ", "obligatorio", "seleccione", "verifique", "no puede", "no válido",
    "incompleto", "requerido", "rechaz", " no se pudo", "falta ", " error",
)
_TEXTO_EXITO_VALIDACION = (
    "radicad", "éxito", "exito", "constancia", "número de radicado",
    "recibid", "recibo", "recepción", "recepcion",
)


def _candidatos_derecho(derecho: str, tipo: str) -> list[str]:
    """Categorías candidatas en el portal para un derecho de la IA.

    Un artículo "Art. 48 CP" se mapea a su categoría ("salud"); queda el
    texto original como candidato y, para tutelas de salud, se añade la
    categoría típica del portal ("salud" / "salud y vida").
    """
    match = re.search(r"(\d{1,3})", derecho)
    candidatos: list[str] = []
    if match:
        categoria = _MAPEO_ARTICULO_CATEGORIA.get(match.group(1))
        if categoria:
            candidatos.append(categoria)
    texto = derecho.lower()
    for categoria, palabras in _CATEGORIA_POR_KEYWORD:
        if any(p in texto for p in palabras):
            candidatos.append(categoria)
    candidatos.append(derecho)
    if tipo == "salud":
        candidatos.extend(["salud", "salud y vida"])
    vistos: set[str] = set()
    resultado: list[str] = []
    for c in candidatos:
        clave = c.lower().strip()
        if clave and clave not in vistos:
            vistos.add(clave)
            resultado.append(c)
    return resultado


def _parece_error_validacion(texto_overlay: str) -> bool:
    """True si el texto de un overlay es un error de validación del portal
    (no una confirmación de radicación)."""
    t = texto_overlay.lower()
    if any(p in t for p in _TEXTO_EXITO_VALIDACION):
        return False
    return any(p in t for p in _TEXTO_ERROR_VALIDACION)


def _es_dialogo_confirmar_datos(texto_overlay: str) -> bool:
    """True si el overlay es la confirmación final de datos del portal
    ("Confirmar Datos" con resumen: lugar, registro, medida provisional) que
    aparece tras pulsar Enviar y que hay que confirmar para radicar."""
    t = texto_overlay.lower()
    if "confirmar datos" in t:
        return True
    # Resumen de datos sin título (textContent junta todo): lugar interpuesta +
    # registro de la tutela suele acompañar al diálogo de confirmación.
    return "lugar donde se interpone la tutela" in t and (
        "registro" in t or "medida provisional" in t
    )


def _es_dialogo_aviso_enviar(texto_overlay: str) -> bool:
    """True si el overlay es el aviso legal que el portal muestra justo antes de
    enviar la tutela ("A través de este portal solo se recibe la acción,
    demanda o solicitud que luego será enviada al juez competente..."), que
    hay que aceptar/continuar para completar la radicación.
    """
    t = texto_overlay.lower()
    return (
        "a través de este portal" in t
        or "solo se recibe" in t
        or "juez competente" in t
        or "acción, demanda o solicitud" in t
    )


def _extraer_numero_de_texto(texto: str) -> str:
    """Extrae el número de confirmación de un texto/overlay del portal.

    El portal usa varias fórmulas en producción, todas prueba de recepción:
      - "Número de radicado: 11001-2026-00009"
      - "Número de recibo: ..." (el manual habla de "recibo de la acción")
      - aviso final "su tutela ha sido recibida con éxito con el número XXX"
    """
    if not texto:
        return ""
    m = re.search(
        r"(?:n[o°º]?\.?\s*(?:radicad[oa]|recib[oa])"
        r"|n[uú]mero\s+de\s+(?:radicac[ió]n|recibo)"
        r"|recibida\s+con\s+[eé]xito(?:\s+con)?\s+(?:el\s+)?n[uú]mero)"
        r"\s*[:.\-]?\s*([0-9\- ]{6,})",
        texto,
        re.IGNORECASE,
    )
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    # Fallback por formato: el radicado/recibo colombiano usa 4-5-4/5 dígitos
    # separados por guiones (ej. 11001-2026-00009) aunque el texto no diga
    # "número".
    m2 = re.search(r"\b\d{4,5}-\d{4}-\d{4,5}\b", texto)
    return m2.group(0) if m2 else ""


def _separar_nombre(nombre_completo: str) -> dict:
    """Separa un nombre completo colombiano en partes.

    Asume formato: [PrimerNombre] [SegundoNombre] [PrimerApellido] [SegundoApellido]
    Maneja 2, 3 o 4 partes y descarta iniciales/abreviaturas sueltas ("J.",
    "M. A.") que el cliente pudo registrar.
    """
    partes = [
        p for p in nombre_completo.strip().split()
        if re.fullmatch(r"[A-Za-zÁÉÍÓÚÑáéíóúñ]\.?", p) is None
    ]
    if not partes:
        partes = nombre_completo.strip().split()
    if len(partes) == 1:
        return {"primer_nombre": partes[0], "segundo_nombre": "", "primer_apellido": "", "segundo_apellido": ""}
    if len(partes) == 2:
        return {"primer_nombre": partes[0], "segundo_nombre": "", "primer_apellido": partes[1], "segundo_apellido": ""}
    if len(partes) == 3:
        return {"primer_nombre": partes[0], "segundo_nombre": partes[1], "primer_apellido": partes[2], "segundo_apellido": ""}
    return {"primer_nombre": partes[0], "segundo_nombre": partes[1], "primer_apellido": partes[2], "segundo_apellido": " ".join(partes[3:])}


def _nombres_a_campos(nombres: str, apellidos: str) -> dict:
    """Reparte nombres/apellidos estructurados (2 preguntas de WhatsApp) en los
    4 campos del portal.

    [Nombres] -> PrimerNombre (1ª palabra) + SegundoNombre (resto).
    [Apellidos] -> PrimerApellido (1ª palabra) + SegundoApellido (resto).
    """
    n = [p for p in nombres.strip().split() if p]
    a = [p for p in apellidos.strip().split() if p]
    return {
        "primer_nombre": n[0] if n else "",
        "segundo_nombre": " ".join(n[1:]),
        "primer_apellido": a[0] if a else "",
        "segundo_apellido": " ".join(a[1:]),
    }


def _info_archivo(ruta: str) -> dict:
    """Identidad del PDF que se va a subir al portal (para verificar en el
    panel que el archivo demandado es el correcto y no uno viejo del disco)."""
    if not os.path.isfile(ruta):
        return {"ruta": ruta, "error": "archivo no existe"}
    with open(ruta, "rb") as f:
        digest = hashlib.sha1(f.read()).hexdigest()
    return {
        "ruta": ruta,
        "basename": os.path.basename(ruta),
        "bytes": os.path.getsize(ruta),
        "sha1": digest,
    }


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
        # Callback opcional on_paso(paso, estado, detalle="") para monitoreo.
        self.on_paso = None

    def _reportar_paso(self, paso: str, estado: str, detalle: str = ""):
        """Emite el estado de un paso al callback de monitoreo si está configurado."""
        if self.on_paso:
            try:
                self.on_paso(paso, estado, detalle)
            except Exception as e:  # noqa: BLE001 - el monitoreo nunca debe romper el flujo
                logger.warning(f"No se pudo reportar paso {paso}: {e}")

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

    async def _seleccionar_select(self, selector: str, label: str) -> str | None:
        """Selecciona un option por texto visible en un select, manejando AJAX y mayúsculas.

        Busca primero por JS case-insensitive + alias + normalizado (sin puntos
        ni espacios: "C.C." == "CC") para evitar timeouts de select_option.
        Retorna el value del option elegido, o None si no se encontró.
        """
        await self._esperar_select_ajax(selector)

        # Buscar el value por JS (case-insensitive / parcial / alias / normalizado)
        match_value = await self.page.evaluate(_JS_BUSCAR_OPTION_SELECT, [selector, label])

        if match_value is not None:
            await self.page.select_option(selector, value=match_value)
            return match_value
        logger.warning(f"No se encontró '{label}' en {selector}")
        return None

    async def _fijar_select_sin_postback(self, selector: str, label: str) -> str | None:
        """Fija el value de un option en un select SIN disparar el evento change.

        En este portal #DDlTipodocumento (y los selects del accionante en
        general) tienen `onchange=__doPostBack`: `page.select_option` dispara el
        postback y el servidor re-renderiza la sección, BORRANDO todo lo escrito
        (cédula, nombres, teléfono, email) y dejando el select en 'Seleccione...'.

        Evidencia del plantel (corrida 20:15 y reintentos): el readback
        "accionante_3_tras_escritura" salía con TODO bien escrito, y el readback
        final con TODO vacío — la única diferencia era la re-selección del tipo
        de documento vía `select_option`.

        Al fijar `value` directo en el DOM (sin eventos) el valor se envía con el
        formulario en el submit y la sección no se re-renderiza. Retorna el value
        fijado, o None si no se encontró la opción.
        """
        match_value = await self.page.evaluate(_JS_BUSCAR_OPTION_SELECT, [selector, label])
        if match_value is not None:
            try:
                await self.page.evaluate(
                    """([sel, val]) => {
                        const s = document.querySelector(sel);
                        if (!s) return;
                        s.value = val;
                        for (let i = 0; i < s.options.length; i++) {
                            if (String(s.options[i].value) === String(val)) {
                                s.selectedIndex = i;
                                break;
                            }
                        }
                    }""",
                    [selector, match_value],
                )
                # Verificar que SÍ quedó seleccionado (el portal puede estar
                # usando un widget que no refleja el value directo).
                verificado = await self.page.evaluate(
                    """([sel]) => {
                        const s = document.querySelector(sel);
                        if (!s) return null;
                        const i = s.selectedIndex;
                        return i >= 0 && s.options[i] ? s.options[i].text.trim() : null;
                    }""",
                    [selector],
                )
                if verificado and str(verificado).strip() not in ("", "Seleccione..."):
                    return match_value
                logger.warning(
                    f"Fijado {selector}='{label}' pero el select quedó en '{verificado}' — widget no sincronizado"
                )
                return None
            except Exception as e:  # noqa: BLE001 - el diagnóstico nunca rompe el flujo
                logger.warning(f"No se pudo fijar {selector} = '{label}': {e}")
                return None
        logger.warning(f"No se encontró '{label}' en {selector}")
        return None

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

    async def _confirmar_dialogo_final(self) -> bool:
        """Confirma el diálogo (Confirmar Datos / aviso legal) que el portal
        abre antes de radicar. Marca checkboxes de aceptación y pulsa el botón
        afirmativo del overlay visible. Devuelve True si hizo clic en algún
        botón."""
        try:
            return bool(await self.page.evaluate("""
                () => {
                    const overlays = Array.from(
                        document.querySelectorAll('.jconfirm, .modal, [role="dialog"]')
                    ).filter(o => o.offsetParent !== null || o.style.display !== 'none');
                    for (const m of overlays) {
                        // Aceptar cualquier checkbox del diálogo (términos).
                        m.querySelectorAll('input[type=checkbox]').forEach(cb => {
                            if (!cb.checked) { try { cb.click(); } catch(e) {} }
                        });
                        const btns = Array.from(m.querySelectorAll('button, a.btn'))
                            .filter(b => {
                                const txt = (b.textContent || '').trim();
                                return txt && !/cerrar|close|×|cancelar/i.test(txt);
                            });
                        if (btns.length === 0) continue;
                        const afir = btns.find(b =>
                            /confirmar|enviar|aceptar|continuar|s[ií]|ok|guardar|de acuerdo|de acuerdo/i.test(
                                b.textContent || ''
                            )
                        );
                        const target = afir || btns[btns.length - 1];
                        target.click();
                        return true;
                    }
                    return false;
                }
            """))
        except Exception as e:
            logger.warning(f"No se pudo confirmar el diálogo final: {e}")
            return False

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

    async def _readback_accionante(self, tag: str) -> dict | None:
        """Vuelca el estado real de la sección accionante en el portal.

        Los usamos en cada fase del llenado para ubicar QUÉ acción la limpia
        (los campos existen/visibles per diagnóstico, pero a veces quedan en
        blanco): escribir la cédula dispara el autofill y cambiar el select del
        tipo de documento puede disparar un postback que re-renderiza la sección.
        """
        try:
            readback = await self.page.evaluate(_JS_READBACK_ACCIONANTE)
            logger.info(f"[readback {tag}] {json.dumps(readback, ensure_ascii=False)}")
            return readback
        except Exception as e:  # noqa: BLE001 - el diagnóstico nunca rompe el flujo
            logger.warning(f"No se pudo leer el accionante ({tag}): {e}")
            return None

    async def _aplicar_identidad_accionante(self, datos: dict):
        """(Re)escribe la identidad del accionante con NUESTRO dato.

        El portal re-renderiza la sección del accionante al resolver el AJAX del
        tipo de documento y de la verificación del correo, con resultados
        conocidos en prod: tipo doc en 'Seleccione...', nombres autocompletados
        del borrador por cédula ('E  Ramirez Montoya') y correo/teléfono vacíos.

        A propósito NO se re-escribe la cédula: escribirla relanza el autofill
        del portal, que vuelve a pisar los nombres (fue la causa de que el
        readback de prod siguiera viéndose mal tras la re-aplicación vieja).
        """
        nombres_s = (datos.get("accionante_nombres") or "").strip()
        apellidos_s = (datos.get("accionante_apellidos") or "").strip()
        if nombres_s and apellidos_s:
            nombre = _nombres_a_campos(nombres_s, apellidos_s)
        else:
            nombre = _separar_nombre(datos.get("accionante_nombre", ""))
        tipo_doc = datos.get("accionante_tipo_doc", "CC")
        email = datos.get("accionante_email", "")
        self._email_accionante = email

        # Se fija el tipo documento SIN disparar postback: en este portal el
        # select tiene onchange=__doPostBack y re-renderizar la sección borra
        # todo lo escrito (ver _fijar_select_sin_postback).
        await self._fijar_select_sin_postback("#DDlTipodocumento", tipo_doc)
        await self.page.wait_for_timeout(400)
        await self._type_existing("#PrimerNombre", nombre["primer_nombre"])
        await self._type_existing("#SegundoNombre", nombre["segundo_nombre"])
        await self._type_existing("#PrimerApellido", nombre["primer_apellido"])
        await self._type_existing("#SegundoApellido", nombre["segundo_apellido"])
        await self._type_existing("#Telefono", datos.get("accionante_telefono", ""))
        await self._type_existing("#Email", email)

        discapacidad = datos.get("accionante_discapacidad") or "No Aplica"
        try:
            await self._fijar_select_sin_postback("#DDlTipodiscapacidad", discapacidad)
        except Exception:
            logger.warning("No se pudo seleccionar tipo discapacidad")

        # El autofill del portal deja el select del tipo de documento en
        # 'Seleccione...': re-fijarlo al final (sin postback, no borra nada).
        await self._fijar_select_sin_postback("#DDlTipodocumento", tipo_doc)

    async def _paso_accionante(self, datos: dict) -> bool:
        """Paso 4: Datos del accionante. Retorna True si requiere código de email."""
        nombres_s = (datos.get("accionante_nombres") or "").strip()
        apellidos_s = (datos.get("accionante_apellidos") or "").strip()
        if nombres_s and apellidos_s:
            nombre = _nombres_a_campos(nombres_s, apellidos_s)
        else:
            nombre = _separar_nombre(datos.get("accionante_nombre", ""))

        # Tipo documento: se usa el que el cliente registró (CC por defecto);
        # el normalizado del alias resuelve variantes ("C.C.", "CC"). Se fija
        # SIN postback: `select_option` dispara __doPostBack y el portal
        # re-renderiza la sección del accionante borrando lo escrito.
        tipo_doc = datos.get("accionante_tipo_doc", "CC")
        await self._fijar_select_sin_postback("#DDlTipodocumento", tipo_doc)
        await self.page.wait_for_timeout(500)

        # Número documento: sin puntos ni espacios, limpiando el campo primero
        # (un campo con valor previo truncaría o duplicaría el dato).
        cedula = re.sub(r"[\s.]", "", str(datos.get("accionante_cedula", "")))
        await self._type_existing("#NumeroDocumento", cedula)
        await self._readback_accionante("accionante_1_tras_cedula")

        # Esperar el autofill del portal (nombres resueltos por cédula): si
        # escribimos los nombre antes de que asiente, el AJAX los pisa (queja
        # de prod: nombre queda "E  Ramirez Montoya" en vez del completo).
        # Sondeamos hasta que el primer nombre aparezca (o un tope) en vez de
        # una espera fija: el autofill puede tardar más de AUTOFILL_SETTLE_MS.
        try:
            for _ in range(int(AUTOFILL_POLL_MAX_MS / 500)):
                value = await self.page.evaluate(
                    "() => (document.querySelector('#PrimerNombre') || {}).value || ''"
                )
                if str(value or "").strip():
                    break
                await self.page.wait_for_timeout(500)
        except Exception:  # noqa: BLE001 - el autofill es solo una espera
            pass
        await self._readback_accionante("accionante_2_tras_autofill")

        # Nombres (typing lento para evitar bloqueo de paste)
        await self._type_existing("#PrimerNombre", nombre["primer_nombre"])
        await self._type_existing("#SegundoNombre", nombre["segundo_nombre"])
        await self._type_existing("#PrimerApellido", nombre["primer_apellido"])
        await self._type_existing("#SegundoApellido", nombre["segundo_apellido"])

        # Teléfono
        await self._type_existing("#Telefono", datos.get("accionante_telefono", ""))

        # Tipo discapacidad (si el usuario declaró una, se usa; si no, "No Aplica")
        discapacidad = datos.get("accionante_discapacidad") or "No Aplica"
        try:
            await self._fijar_select_sin_postback("#DDlTipodiscapacidad", discapacidad)
        except Exception:
            logger.warning("No se pudo seleccionar tipo discapacidad")

        # Email
        email = datos.get("accionante_email", "")
        # Se guarda en el bot: tras validar el código, el portal vuelve a pedir
        # el correo y `ingresar_codigo_email` necesita re-ingresarlo.
        self._email_accionante = email
        await self._type_existing("#Email", email)
        await self._readback_accionante("accionante_3_tras_escritura")

        # Re-aplicar identidad (tipo documento + nombres + teléfono + email +
        # discapacidad): al resolver el AJAX del tipo de documento, el portal
        # puede re-renderizar estos campos y dejar el select en 'Seleccione...'
        # (queja de prod) o los nombres en blanco / autocompletados del borrador
        # que guarda para esa cédula. Se re-escribe la identidad de NUESTROS
        # datos (sin tocar de nuevo la cédula: re-escribirla relanzaría el
        # autofill y volvería a pisar los nombres).
        await self._aplicar_identidad_accionante(datos)

        # Readback: volcar qué quedó realmente en el formulario del portal
        # (tipo documento, nombres, apellidos, cédula) para diagnosticar en el
        # panel si el llenado no aplicó (quejas: "no selecciona el tipo de
        # documento" y "no pone los nombres bien").
        try:
            readback = await self.page.evaluate(_JS_READBACK_ACCIONANTE)
            logger.info(f"[readback accionante] {json.dumps(readback, ensure_ascii=False)}")
            fuente = {
                "tipo_doc": tipo_doc,
                "cedula": datos.get("accionante_cedula"),
                "nombre": datos.get("accionante_nombre"),
                "nombres": datos.get("accionante_nombres"),
                "apellidos": datos.get("accionante_apellidos"),
            }
            logger.info(f"[readback fuente] {json.dumps(fuente, ensure_ascii=False)}")
            if readback and (
                str(readback.get("tipo_doc") or "").strip() in ("", "Seleccione...")
                or not str(readback.get("primer_nombre") or "").strip()
            ):
                logger.warning(
                    "[readback accionante] el portal NO quedó con el tipo de documento/identidad correcto"
                )
                await self._log_diagnostico_accionante()
                await self._capturar_evidencia("accionante_vacio")
        except Exception as e:  # noqa: BLE001 - el diagnóstico nunca rompe el flujo
            logger.warning(f"No se pudo leer el formulario del accionante: {e}")

        # Click validar correo — activa verificación
        await self._cerrar_jconfirm()
        await self._js_click("#btnValidar")
        await self.page.wait_for_timeout(1000)

        # Detección condicional: el portal SOLO pide código cuando el correo
        # no está registrado. El indicador real es el CAJÓN visible con un
        # input habilitado (no `#IdEmail1`/`#btnValidar`, que están siempre en
        # el DOM; `#IdEmail1` es el 'confirmar correo' y queda disabled hasta
        # validar el código).
        try:
            await self.page.wait_for_function(_JS_CAJON_ABIERTO, timeout=5000)
            return True
        except Exception:
            logger.info("Correo ya verificado, no se requiere código de email")
            # Aún así el portal exige cerrar el paso de confirmación del correo:
            # hay que re-ingresar el email + confirmar (#IdEmail1) + Validar de
            # nuevo (quejas de prod: "Debe Confirmar el correo electrónico" al
            # enviar). Esta re-validación usa campos habilitados, no reabre el
            # cajón del código.
            email = getattr(self, "_email_accionante", "")
            if email:
                try:
                    overlay = await self.page.evaluate(_JS_OVERLAY_TEXTO)
                    logger.warning(
                        "[diag correo] 'ya verificado' pero overlay ahora: "
                        f"{json.dumps(overlay, ensure_ascii=False)[:400]!r} — dump del accionante:"
                    )
                    await self._log_diagnostico_accionante()
                except Exception:  # noqa: BLE001 - el diagnóstico nunca rompe
                    pass
                try:
                    await self._reingresar_email(email)
                except Exception as e:  # noqa: BLE001 - el flujo continúa igual
                    logger.warning(f"No se pudo completar la confirmación del correo: {e}")
            return False

    async def ingresar_codigo_email(self, codigo: str) -> dict:
        """Valida el código de verificación en el cajón que abre el portal.

        FLUJO REAL del portal (Rama Judicial), confirmado en pruebas manuales:
          1. Al dar "Validar" el correo (#btnValidar), el portal abre un cajón
             ("ingrese el código") con un INPUT y un botón.
          2. El código se escribe en ese INPUT del cajón (NUNCA en #IdEmail1:
             es el 'confirmar correo' y queda disabled hasta validar).
          3. Se pulsa el botón "Continuar" y el cajón valida el código.
          4. El cajón cierra y el portal vuelve a pedir el correo (email +
             confirmar); se re-ingresa y se da "Validar": ya NO vuelve a pedir
             código y el flujo continúa.

        Todo acotado con timeouts: nunca cuelga el loop esperando un campo que
        no aparece (bug de producción: radicación colgada en 'continuando').
        """
        try:
            await self.page.wait_for_function(_JS_CAJON_ABIERTO, timeout=ESPERA_CODIGO_SELECTOR_MS)
        except Exception as e:
            await self._capturar_evidencia("codigo_sin_cajon")
            await self._log_diagnostico_cajon()
            logger.warning(f"No se detectó el cajón del código de email: {e}")
            return {"ok": False, "error": "No se detectó el cajón del código de verificación en el portal"}

        # Identificar el input del código dentro del cajón (único habilitado).
        try:
            selector_codigo = await self.page.evaluate(_JS_SELECTOR_CODIGO)
        except Exception as e:
            selector_codigo = None
            logger.warning(f"No se pudo identificar el campo del código: {e}")
        if not selector_codigo:
            await self._capturar_evidencia("codigo_sin_input")
            await self._log_diagnostico_cajon()
            return {"ok": False, "error": "No se identificó el campo del código en el cajón del portal"}

        try:
            await self._type_existing(selector_codigo, codigo)
            await self.page.wait_for_timeout(400)
        except Exception as e:
            await self._capturar_evidencia("codigo_no_escrito")
            await self._log_diagnostico_cajon()
            logger.warning(f"No se pudo escribir el código de verificación: {e}")
            return {"ok": False, "error": f"No se pudo escribir el código en el portal: {e}"}

        # Pulsar "Continuar" en el cajón para que valide el código.
        confirmado = await self._click_continuar_cajon()
        if not confirmado:
            await self._capturar_evidencia("codigo_sin_continuar")
            await self._log_diagnostico_cajon()
            logger.warning("No se encontró el botón 'Continuar' del cajón de verificación")
            return {"ok": False, "error": "No se encontró el botón 'Continuar' del cajón de verificación"}

        # El cajón valida el código, se cierra y el portal vuelve a pedir correo.
        await self.page.wait_for_timeout(1200)
        email = getattr(self, "_email_accionante", "")
        if email:
            try:
                await self._reingresar_email(email)
            except Exception as e:
                await self._capturar_evidencia("codigo_reingreso_correo_error")
                await self._log_diagnostico_cajon()
                logger.warning(f"No se pudo re-ingresar el correo tras el código: {e}")
                return {"ok": False, "error": f"No se pudo re-ingresar el correo tras el código: {e}"}

        # Verificar que el cajón ya no está abierto (correo quedó verificado).
        try:
            sigue_abierto = bool(await self.page.evaluate(_JS_CAJON_ABIERTO))
        except Exception:
            sigue_abierto = False
        if sigue_abierto:
            await self._capturar_evidencia("codigo_pide_de_nuevo")
            await self._log_diagnostico_cajon()
            logger.warning("El portal volvió a pedir el código tras el re-ingreso del correo")
            return {"ok": False, "error": "El portal volvió a pedir el código de verificación tras el re-ingreso del correo"}

        logger.info("Código de email validado: verificación completada")
        return {"ok": True}

    async def _reingresar_email(self, email: str):
        """Re-ingresa el correo en lo que el portal dejó disponible tras
        validar el código: #Email (si quedó vacío) y #IdEmail1 (el 'confirmar
        correo' que se habilita al quedar verificado). Solo si está habilitado."""
        await self._rellenar_si_habilitado("#Email", email)
        await self._rellenar_si_habilitado("#IdEmail1", email)
        await self._js_click("#btnValidar")
        await self.page.wait_for_timeout(1000)

    async def _rellenar_si_habilitado(self, selector: str, texto: str):
        """Escribe un campo SOLO si existe y está habilitado (un campo disabled
        haría bloquear `page.fill` 30s)."""
        try:
            disponible = bool(await self.page.evaluate(
                """([sel]) => {
                    const el = document.querySelector(sel);
                    if (!el) return false;
                    return !el.disabled && !el.readOnly &&
                           el.offsetWidth > 0 && el.offsetHeight > 0;
                }""",
                [selector],
            ))
        except Exception:
            disponible = False
        if disponible:
            await self._type_existing(selector, texto)

    async def _log_diagnostico_cajon(self):
        """Vuelca a los logs la estructura del cajón (inputs con su estado)
        para diagnosticar el portal sin depender de capturas de pantalla."""
        try:
            info = await self.page.evaluate("""() => {
                const overlays = Array.from(
                    document.querySelectorAll('.jconfirm, .modal, [role="dialog"]'));
                const visibles = overlays.filter(
                    o => o.offsetParent !== null || o.style.display !== 'none');
                return visibles.map(o => ({
                    clases: o.className,
                    inputs: Array.from(o.querySelectorAll('input')).map(i => ({
                        id: i.id,
                        name: i.name,
                        type: i.type,
                        disabled: i.disabled,
                        readonly: i.readOnly,
                        visible: i.offsetWidth > 0 && i.offsetHeight > 0,
                    })),
                }));
            }""")
            logger.warning(f"[diagnóstico cajón código] {json.dumps(info)[:2500]}")
        except Exception as e:  # noqa: BLE001 - el diagnóstico nunca rompe el flujo
            logger.warning(f"No se pudo diagnosticar el cajón del código: {e}")

    async def _log_diagnostico_accionante(self):
        """Vuelca a los logs el estado de la sección accionante (opciones del
        select de tipo doc + existencia/visibilidad/habilitado de cada campo)
        para diagnosticar sin capturas por qué el llenado no aplica. Nunca
        rompe el flujo."""
        try:
            info = await self.page.evaluate(_JS_DIAGNOSTICO_ACCIONANTE)
            logger.warning(f"[diagnóstico accionante] {json.dumps(info, ensure_ascii=False)[:3500]}")
        except Exception as e:  # noqa: BLE001 - el diagnóstico nunca rompe el flujo
            logger.warning(f"No se pudo diagnosticar la sección accionante: {e}")

    async def _click_continuar_cajon(self) -> bool:
        """Pulsa el botón 'Continuar' del cajón de verificación de email.

        Busca el botón por su texto (case-insensitive) priorizando los estilos
        de los modales jquery-confirm que usa el portal. Retorna False si no
        encontró ninguno (para no avanzar a ciegas).
        """
        for selector in (
            ".jconfirm .btn:has-text('Continuar')",
            ".jconfirm-buttons button:has-text('Continuar')",
            "button:has-text('Continuar')",
        ):
            try:
                btn = await self.page.query_selector(selector)
                if btn is not None and await btn.is_visible():
                    await btn.click()
                    return True
            except Exception:  # noqa: BLE001 - probar el siguiente candidato
                continue
        return False

    async def _capturar_evidencia(self, tag: str):
        """Toma un screenshot de diagnóstico SIEMPRE sin romper el flujo."""
        try:
            await self.tomar_screenshot(tag)
        except Exception as e:  # noqa: BLE001 - la evidencia nunca debe romper el flujo
            logger.warning(f"No se pudo capturar screenshot {tag}: {e}")

    async def verificar_conexion(self, timeout_ms: int = 12000) -> bool:
        """Verifica que el navegador siga respondiendo (ping acotado).

        Un navegador puede quedar 'vivo aparente' pero con la conexión muerta
        sin disparar errores: Playwright ignora la cancelación de asyncio y un
        await queda colgado para siempre (bug de producción que colgaba el
        webhook esperando el código). Antes de escribir en una página retomada
        se verifica que responda con un ``page.evaluate`` acotado.

        El timeout por defecto es generoso (12s): el portal es pesado y un ping
        lento NO significa navegador muerto (por eso radicacion_service lo
        sondea varias veces espaciadas). Se loguea el motivo real del fallo
        (target cerrado vs timeout) para diagnosticar en producción.
        """
        if self.page is None:
            return False
        try:
            # `page.evaluate()` NO acepta el kwarg `timeout` (TypeError en
            # Playwright): el acotado lo hace `asyncio.wait_for`. Sin esto el
            # ping fallaba SIEMPRE y un navegador vivo parecía muerto — la
            # raíz de todos los 'Navegador sin responder' en producción.
            await asyncio.wait_for(self.page.evaluate("1 + 1"), timeout=timeout_ms / 1000)
            return True
        except Exception as e:  # noqa: BLE001 - cualquier fallo = conexión no usable
            logger.warning(f"Navegador sin responder (timeout={timeout_ms}ms): {e}")
            return False

    async def _paso_accionado(self, datos: dict):
        """Paso 5: Agregar accionado.

        El accionado SIEMPRE es persona jurídica: la tutela se dirige contra
        una EPS. El portal exige seleccionar "Jurídica" en #DDlTipoSujeto con
        documento NIT, sin importar lo que cargue el cliente en el chat.
        """
        await self._seleccionar_select("#DDlTipoSujeto", "Jurídica")
        await self.page.wait_for_timeout(500)

        # Para persona jurídica el portal exige tipo de documento (NIT) y número
        await self._seleccionar_select("#DDlTipodocumentoAccionado", "NIT")
        await self.page.wait_for_timeout(500)
        await self._type("#DocumentodeIdendificacion", datos.get("accionado_nit", ""))
        await self._type("#NombreJuridicoAcc", datos.get("accionado", ""))

        await self._type("#IdDireccion", datos.get("accionado_direccion", "") or "-")
        await self._type("#IdTelefono", datos.get("accionado_telefono", "") or "-")
        await self._type("#IdEmail", datos.get("accionado_email", ""))

        # La acción no involucra menores de edad en el caso estándar
        try:
            await self._js_click("#RdbNoAccionMenores")
        except Exception:
            logger.warning("No se pudo seleccionar 'accionado no involucra menores'")

        await self._cerrar_jconfirm()
        await self._js_click("#btnAddAccionado")
        await self.page.wait_for_timeout(1500)

    async def _paso_derechos(self, datos: dict) -> int:
        """Paso 6: Agregar derechos vulnerados (mapeados a categorías del portal).

        La IA trae artículos ("Art. 48 CP"); el portal usa categorías ("salud").
        Se mapea cada artículo a categoría (y se expande por palabras clave), se
        deduplican y se agregan. Si ninguno coincide se vuelca el dropdown al
        log y se usa la categoría de respaldo `_FALLBACK_DERECHO` (no se aborta
        la tutela ni se inventa un derecho). Retorna cuántos se seleccionaron.
        """
        derechos = datos.get("derechos_vulnerados", [])
        tipo = datos.get("tipo", "")
        elegidos: set[str] = set()
        seleccionados = 0
        for derecho in derechos[:5]:
            for candidato in _candidatos_derecho(derecho, tipo):
                try:
                    value = await self._seleccionar_select("#DDLDerechos", candidato)
                except Exception:
                    value = None
                if value is None or value in elegidos:
                    continue
                elegidos.add(value)
                await self._agregar_derecho_seleccionado(datos)
                seleccionados += 1
                break

        if seleccionados < 1:
            await self._log_opciones_derechos()
            logger.warning(
                "Ningún derecho de la IA matcheó una categoría del portal: usando fallback '%s'",
                _FALLBACK_DERECHO,
            )
            try:
                value = await self._seleccionar_select("#DDLDerechos", _FALLBACK_DERECHO)
            except Exception:
                value = None
            if value is None:
                raise ValueError("No se pudo seleccionar ningún derecho vulnerado en el portal")
            await self._capturar_evidencia("derechos_fallback")
            await self._agregar_derecho_seleccionado(datos)
            seleccionados = 1
        return seleccionados

    async def _agregar_derecho_seleccionado(self, datos: dict):
        """Marca medida provisional y agrega el derecho ya seleccionado a la lista."""
        if datos.get("medida_provisional") == "si":
            await self._js_click("#RdbSiMedida")
        else:
            await self._js_click("#RdbNoMedida")
        await self._cerrar_jconfirm()
        await self._js_click("#btnAdd")
        await self.page.wait_for_timeout(1000)

    async def _log_opciones_derechos(self):
        """Vuelca las opciones reales de #DDLDerechos para diagnosticar el mapeo."""
        try:
            opciones = await self.page.evaluate(_JS_DERECHOS_OPCIONES)
            logger.warning(f"[diagnóstico derechos] opciones en #DDLDerechos: {json.dumps(opciones)[:2000]}")
        except Exception as e:  # noqa: BLE001 - el diagnóstico nunca rompe el flujo
            logger.warning(f"No se pudo volcar las opciones del dropdown de derechos: {e}")

    async def _paso_archivos(self, ruta_pdf: str):
        """Paso 7: Subir el PDF de la tutela como DEMANDA (obligatorio) y como PRUEBA."""
        if not ruta_pdf or not await asyncio.to_thread(os.path.isfile, ruta_pdf):
            raise ValueError(
                f"PDF de la tutela no disponible para subir al portal: {ruta_pdf!r}"
            )

        # Identidad exacta del archivo que se va a subir (path + sha1). Sirve
        # para confirmar/descartar que el portal recibe el PDF correcto y no
        # uno viejo del disco con datos de otra tutela.
        detalle_pdf = await asyncio.to_thread(_info_archivo, ruta_pdf)
        logger.info(f"[uploads] subiendo como DEMANDA/PRUEBA: {json.dumps(detalle_pdf, ensure_ascii=False)}")

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

                # Readback: nombre real que el portal asigna al adjunto subido
                try:
                    nombre_input = await self.page.evaluate(
                        "() => { const i = document.querySelector('#ArchivoFile0');"
                        " return i && i.files && i.files[0] ? i.files[0].name : ''; }"
                    )
                    logger.info(f"[uploads] #{tipo_label}: input #ArchivoFile0 ahora -> {nombre_input!r}")
                except Exception as ex:  # noqa: BLE001 - diagnóstico, nunca rompe
                    logger.warning(f"[uploads] no se pudo leer el input tras subir {tipo_label}: {ex}")

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
            self._paso_actual = "paso_0_terminos"
            await self._modal_aceptar_terminos()
            self._reportar_paso("paso_0_terminos", "ok")

            # Paso 1: Lugar de envío
            self._paso_actual = "paso_1_lugar_envio"
            await self._paso_lugar_envio(datos)
            self._reportar_paso("paso_1_lugar_envio", "ok")
            logger.info("Paso 1 completado: lugar de envío")

            # Paso 2: Tipo registro
            self._paso_actual = "paso_2_tipo_registro"
            await self._paso_tipo_registro()
            self._reportar_paso("paso_2_tipo_registro", "ok")
            logger.info("Paso 2 completado: tipo tutela")

            # Paso 3: Lugar de hechos
            self._paso_actual = "paso_3_lugar_hechos"
            await self._paso_lugar_hechos(datos)
            self._reportar_paso("paso_3_lugar_hechos", "ok")
            logger.info("Paso 3 completado: lugar de hechos")

            # Paso 4: Accionante + trigger verificación email
            self._paso_actual = "paso_4_accionante"
            requiere_codigo = await self._paso_accionante(datos)
            self._reportar_paso("paso_4_accionante", "ok")
            logger.info("Paso 4 completado: accionante + verificación email activada")

            return {"ok": True, "requiere_codigo_email": requiere_codigo}

        except Exception as e:
            logger.error(f"Error en llenar_formulario: {e}")
            self._reportar_paso(getattr(self, "_paso_actual", "llenar_formulario"), "error", str(e))
            return {"ok": False, "error": str(e)}

    async def completar_post_codigo(self, datos: dict, ruta_pdf: str) -> dict:
        """Completa el formulario después de ingresar el código de email.

        Ejecuta pasos 5-10: accionado, derechos, archivos, juramento.
        """
        if settings.simulate_bot:
            return {"ok": True}

        try:
            logger.info("Retomando formulario post-verificación email...")

            # El postback de #btnValidar (y el re-ingreso del correo) re-renderiza
            # la sección del accionante desde el servidor y borra lo que
            # escribimos (quejas de prod). Re-aplicar la identidad ANTES del
            # accionado para que en "Confirmar Datos" quede NUESTRO dato.
            await self._aplicar_identidad_accionante(datos)
            try:
                readback = await self.page.evaluate(_JS_READBACK_ACCIONANTE)
                logger.info(f"[readback post-email] {json.dumps(readback, ensure_ascii=False)}")
                if readback and (
                    str(readback.get("tipo_doc") or "").strip() in ("", "Seleccione...")
                    or not str(readback.get("primer_nombre") or "").strip()
                ):
                    logger.warning(
                        "[readback post-email] el portal NO quedó con el tipo de documento/identidad correcto"
                    )
                    await self._log_diagnostico_accionante()
                    await self._capturar_evidencia("accionante_vacio_post_email")
            except Exception as e:  # noqa: BLE001 - el diagnóstico nunca rompe el flujo
                logger.warning(f"No se pudo leer el formulario del accionante post-email: {e}")

            # Paso 5: Accionado
            self._paso_actual = "paso_5_accionado"
            await self._paso_accionado(datos)
            self._reportar_paso("paso_5_accionado", "ok")
            logger.info("Paso 5 completado: accionado")

            # Paso 6: Derechos
            self._paso_actual = "paso_6_derechos"
            await self._paso_derechos(datos)
            self._reportar_paso("paso_6_derechos", "ok")
            logger.info("Paso 6 completado: derechos")

            # Paso 7: Archivos
            self._paso_actual = "paso_7_archivos"
            await self._paso_archivos(ruta_pdf)
            self._reportar_paso("paso_7_archivos", "ok")
            logger.info("Paso 7 completado: archivos")

            # Paso 8: Juramento
            self._paso_actual = "paso_8_juramento"
            await self._paso_juramento()
            self._reportar_paso("paso_8_juramento", "ok")
            logger.info("Paso 8 completado: juramento")

            return {"ok": True}

        except Exception as e:
            logger.error(f"Error en completar_post_codigo: {e}")
            self._reportar_paso(getattr(self, "_paso_actual", "completar_post_codigo"), "error", str(e))
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
            self._reportar_paso("paso_9_captcha", "error", "No se pudo resolver el reCAPTCHA")
            return False

        # Insertar el token en el textarea oculto de reCAPTCHA y disparar el
        # callback registrado por el portal.  Google renombra las propiedades
        # internas (client.T, client.L, …) con cada versión, así que no
        # dependemos de un nombre fijo: buscamos en el árbol de
        # ___grecaptcha_cfg.clients CUALQUIER objeto con propiedad .callback
        # (la config del widget guarda ahí el callback del sitio, estructura
        # estable en reCAPTCHA v2) y además disparamos eventos input/change en
        # el textarea por si el portal lo lee directo.
        try:
            await self.page.evaluate(f"""
                document.getElementById('g-recaptcha-response').value = '{token}';
                (function() {{
                    var hecho = false;
                    var ta = document.getElementById('g-recaptcha-response');
                    if (ta) {{
                        ta.dispatchEvent(new Event('input', {{bubbles: true}}));
                        ta.dispatchEvent(new Event('change', {{bubbles: true}}));
                    }}
                    if (typeof ___grecaptcha_cfg === 'undefined') return;
                    var clients = ___grecaptcha_cfg.clients;
                    for (var key in clients) {{
                        var cola = [clients[key]];
                        var vistos = 0;
                        while (cola.length > 0 && vistos < 300 && !hecho) {{
                            var obj = cola.shift();
                            vistos++;
                            if (!obj || typeof obj !== 'object') continue;
                            try {{
                                if (typeof obj.callback === 'function') {{
                                    obj.callback('{token}');
                                    hecho = true;
                                    break;
                                }}
                            }} catch(e) {{}}
                            for (var p in obj) {{
                                try {{
                                    var val = obj[p];
                                    if (typeof val === 'object' && val !== null) cola.push(val);
                                }} catch(e) {{}}
                            }}
                        }}
                        if (hecho) break;
                    }}
                }})();
            """)
            logger.info("Token reCAPTCHA insertado en el formulario")
            self._reportar_paso("paso_9_captcha", "ok")
            return True
        except Exception as e:
            logger.error(f"Error insertando token reCAPTCHA: {e}")
            self._reportar_paso("paso_9_captcha", "error", str(e))
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

            # Verificación de éxito REAL: si el portal quedó en un overlay de
            # validación (ej. "debe seleccionar al menos un derecho"), la tutela
            # NO se radicó — antes marcábamos 'radicada' igual (bug en prod).
            # El portal además encadena diálogos antes de radicar: primero
            # "Confirmar Datos" y luego un aviso legal ("A través de este portal
            # solo se recibe..."). Los confirmamos en bucle hasta que salga el
            # número de radicado.
            num_radicado = ""
            overlay_texto = ""
            for intento in range(5):
                num_radicado = await self._leer_num_radicado()
                overlay_texto = await self._leer_overlay()
                logger.info(
                    f"Tras envío (ronda {intento}): num_radicado={num_radicado or '(vacío)'} "
                    f"overlay={'SÍ' if overlay_texto else 'no'}"
                )
                if num_radicado:
                    break
                if not overlay_texto:
                    break
                if _es_dialogo_confirmar_datos(overlay_texto) or _es_dialogo_aviso_enviar(overlay_texto):
                    # El resumen "Confirmar Datos" muestra EXACTAMENTE lo que el
                    # portal registró (tipo documento, nombres, cédula...):
                    # volcarlo completo para poder contrastarlo en el panel.
                    logger.info(
                        f"Diálogo previo a radicar detectado (ronda {intento}) — confirmando.\n"
                        f"[overlay completo] {overlay_texto[:1500]}"
                    )
                    if await self._confirmar_dialogo_final():
                        await self.page.wait_for_timeout(5000)
                        continue
                break

            if not num_radicado and overlay_texto:
                if _parece_error_validacion(overlay_texto):
                    await self._capturar_evidencia("envio_validacion_error")
                    logger.warning(f"El portal rechazó la tutela: {overlay_texto[:200]}")
                    return {"path": None, "num_radicado": None, "error": f"El portal rechazó el envío: {overlay_texto[:200]}"}
                # Success en overlay: recuperar el número del texto ("Número de
                # radicado: 11001-2026-00009") que el selector de la página no trae.
                num_radicado = _extraer_numero_de_texto(overlay_texto)

            if not num_radicado:
                # Tras confirmar los diálogos el portal puede tardar en pintar
                # el radicado: sondeamos unos segundos más antes de declarar
                # fallo (ronda 2 del último intento salió con overlay=no y el
                # número aún vacío).
                for _ in range(3):
                    await self.page.wait_for_timeout(5000)
                    num_radicado = await self._leer_num_radicado()
                    if num_radicado:
                        break
                    overlay_texto = await self._leer_overlay()
                    if overlay_texto:
                        num_radicado = _extraer_numero_de_texto(overlay_texto)
                        if num_radicado:
                            break

            # Descargar constancia
            ruta_constancia = path_constancia()
            try:
                async with self.page.context.expect_download(timeout=15000) as download_info:
                    await self.page.click("#btnDescargarConstancia")
                download = await download_info.value
                await download.save_as(ruta_constancia)
            except Exception:
                logger.warning("No se pudo descargar constancia, intentando screenshot")
                ruta_constancia = str(await self.tomar_screenshot("constancia"))

            # Extraer número de radicado (si aún viene vacío)
            if not num_radicado:
                num_radicado = await self._leer_num_radicado()

            self._reportar_paso("paso_10_enviar", "ok", num_radicado or "")
            return {"path": ruta_constancia, "num_radicado": num_radicado}

        except Exception as e:
            logger.error(f"Error en enviar_y_descargar: {e}")
            self._reportar_paso("paso_10_enviar", "error", str(e))
            return {"path": None, "num_radicado": None, "error": str(e)}

    async def _leer_num_radicado(self) -> str:
        try:
            elemento = await self.page.query_selector("#numRadicado")
            if elemento:
                return (await elemento.text_content() or "").strip()
        except Exception as e:  # noqa: BLE001 - el número es un extra, nunca rompe el flujo
            logger.error(f"Error obteniendo num_radicado: {e}")
        return ""

    async def _leer_overlay(self) -> str:
        try:
            texto = await self.page.evaluate(_JS_OVERLAY_TEXTO)
            return str(texto or "").strip()
        except Exception as e:  # noqa: BLE001 - el overlay es un extra, nunca rompe el flujo
            logger.warning(f"No se pudo leer el overlay tras el envío: {e}")
            return ""

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
            # Cerrar TODO el contexto, no solo la pestaña: cada intento crea un
            # contexto aislado (BrowserManager.new_page -> new_context) para
            # "empezar de cero"; si solo cerramos la page, el contexto queda
            # huérfano en el chromium singleton y acumula memoria entre
            # intentos (problema real en Render con muchos reintentos).
            with contextlib.suppress(Exception):  # contexto ya pudo estar cerrado
                await self.page.context.close()
            with contextlib.suppress(Exception):  # context.close ya cierra sus pages
                await self.page.close()
            self.page = None
