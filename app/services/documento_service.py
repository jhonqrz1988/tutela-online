import os
import re
from datetime import UTC, datetime

import fitz
from fpdf import FPDF
from PIL import Image

from app.services.ia_service import normalizar_dato_obligatorio
from app.utils.file_utils import path_tutela_pdf_nombre

# Títulos de sección estilo "IV. HECHOS" / "VIII. PRETENSIONES"
_TITULO_SECCION_RE = re.compile(r"^[IVXLCDM]{1,4}\.\s+\S")

# Glifos sin representación en documentos legales (emoji, variant selectors,
# zero-width) y espacios no rompibles; se limpian antes de renderizar.
_RE_GLIFOS_INVALIDOS = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200b-\u200d\ufeff]")

_MESES_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)

_VALORES_ROMANOS = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def _fecha_espanol(momento: datetime | None = None) -> str:
    """Fecha como "4 de septiembre de 2026" (meses en español, sin depender
    del locale del servidor; el strftime con %B daba meses en inglés)."""
    now = momento or datetime.now(UTC)
    return f"{now.day} de {_MESES_ES[now.month - 1]} de {now.year}"


def _valor_romano(numeral: str) -> int | None:
    try:
        total = 0
        prev = 0
        for ch in reversed(numeral):
            v = _VALORES_ROMANOS[ch]
            if v < prev:
                total -= v
            else:
                total += v
                prev = v
        return total
    except KeyError:
        return None


def _cuerpo_sin_encabezado_y_partes(contenido: str) -> str:
    """Devuelve el texto IA desde la primera sección >= III (HECHOS).

    El encabezado, accionante y accionado del texto IA se descartan porque el
    PDF los reconstruye de forma determinista desde `datos`. Si el texto IA no
    sigue la estructura I-XI, se devuelve tal cual (fallback seguro) quitando
    solo una línea inicial que parezca fecha, para no duplicar el encabezado.
    """
    lineas = contenido.splitlines() if contenido else []
    for i, linea in enumerate(lineas):
        limpia = linea.strip()
        m = re.match(r"^([IVXLCDM]{1,4})\.\s+\S", limpia)
        if m:
            val = _valor_romano(m.group(1))
            if val is not None and val >= 3:
                return "\n".join(lineas[i:]).strip()
    cuerpo = "\n".join(lineas).strip()
    lineas2 = cuerpo.splitlines() or []
    if lineas2:
        primera = lineas2[0].strip()
        es_fecha = re.match(r"^\d{1,2}\s+de\s+[a-záéíóúñ]+\s+de\s+\d{4}$", primera, re.IGNORECASE)
        es_fecha_con_ciudad = re.match(
            r"^[A-ZÁÉÍÓÚÑa-záéíóúñ ,.]+,?\s+\d{1,2}\s+de\s+[a-záéíóúñ]+\s+de\s+\d{4}$",
            primera, re.IGNORECASE,
        )
        if es_fecha or es_fecha_con_ciudad:
            return "\n".join(lineas2[1:]).strip()
    return cuerpo


def _render_encabezado(pdf, ciudad: str, fecha: str) -> None:
    """Encabezado legal: ciudad+fecha de radicación, juez competente y E.S.D."""
    pdf.ln(2)
    pdf.set_font(pdf.fuente, "B", 12)
    pdf.cell(0, 6, f"{ciudad}, {fecha}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Señor JUEZ CONSTITUCIONAL DE {ciudad.upper()} (REPARTO)", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "E.S.D.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)


def _seccion_accionante(pdf, datos: dict) -> None:
    """Sección I. ACCIONANTE construida SIEMPRE desde `datos` (fuente de verdad)."""
    nombre = datos.get("accionante_nombre") or "___________"
    tipo_doc = datos.get("accionante_tipo_doc") or "CC"
    cedula = (datos.get("accionante_cedula") or "").strip()
    direccion = (datos.get("accionante_direccion") or "").strip()
    telefono = (datos.get("accionante_telefono") or "").strip()
    email = (datos.get("accionante_email") or "").strip()
    ciudad = (datos.get("ciudad") or "").strip()
    departamento = (datos.get("departamento") or "").strip()

    pdf.section_title("I. ACCIONANTE")
    pdf.body_text(f"Nombre: {nombre}")
    if cedula:
        pdf.body_text(f"Documento: {tipo_doc} {cedula}")
    if direccion:
        pdf.body_text(f"Dirección: {direccion}")
    pdf.body_text(f"Teléfono: {telefono or '___________'}")
    pdf.body_text(f"Correo electrónico: {email or '___________'}")
    if ciudad:
        pdf.body_text(f"Ciudad: {ciudad}" + (f", {departamento}" if departamento else ""))


def _seccion_accionado(pdf, datos: dict) -> None:
    """Sección II. ACCIONADO: NIT y correo de notificación normalizados (nunca "no sé")."""
    accionado = datos.get("accionado") or "___________"
    pdf.section_title("II. ACCIONADO")
    pdf.body_text(f"Nombre: {accionado}")
    tipo = (datos.get("accionado_tipo") or "").strip()
    if tipo:
        pdf.body_text(f"Tipo: {tipo}")
    nit = normalizar_dato_obligatorio(datos.get("accionado_nit", ""))
    if nit:
        pdf.body_text(f"NIT: {nit}")
    email_accionado = normalizar_dato_obligatorio(datos.get("accionado_email", ""))
    if email_accionado:
        pdf.body_text(f"Email notificación: {email_accionado}")


def _limpiar_texto(texto: str) -> str:
    """Quita emoji/zero-width; preserva tildes, —, «», ¿¡ y demás tipografía."""
    return _RE_GLIFOS_INVALIDOS.sub("", texto.replace("\u00a0", " "))


# Markdown que la IA deja en el texto (títulos "###", negrita, itálica y HR).
# `#` literal a mitad de texto (direcciones tipo "Calle 30a #32b-14") se preserva.
_RE_TITULO_MD = re.compile(r"^\s*#{1,6}\s*")
_RE_HR_MD = re.compile(r"^\s*([-*_])\1{2,}\s*$")
_RE_CURSIVA_MD = re.compile(r"(^|[\s])\*(\S.*?\S)\*([\s]$|[\s.,;:!?)]|$)")


def _quitar_markdown(texto: str) -> str:
    """Convierte el texto IA de Markdown a texto plano para el PDF.

    Lo que no se limpie aquí saldría literal en el documento (###, **, ---),
    y rompería además la detección de secciones en `_cuerpo_sin_encabezado_y_partes`.
    """
    lineas = []
    for raw in (texto or "").splitlines():
        linea = _RE_TITULO_MD.sub("", raw)
        linea = linea.replace("**", "").replace("__", "")
        linea = _RE_CURSIVA_MD.sub(r"\1\2\3", linea)
        if _RE_HR_MD.match(linea):
            continue
        if not linea.strip():
            continue
        lineas.append(linea.rstrip())
    return "\n".join(lineas)


# Marcadores que la IA deja cuando falta un dato: "3. **[DATO PENDIENTE: X]**, ..."
_RE_PENDIENTE = re.compile(r"\[\s*(?:DATO\s+PENDIENTE[^\]]*|NO\s+PROPORCIONADO[^\]]*|pendiente[^\]]*)\s*\]", re.IGNORECASE)
_RE_RESTOS_PENDIENTE = re.compile(r"^\s*(?:[-•]|\d{1,2}\.)\s*\**\s*,?\s*")


def _quitar_marcadores_pendientes(texto: str) -> str:
    """Elimina "[DATO PENDIENTE: X]", "[NO PROPORCIONADO]" y "[pendiente]".

    Si al quitar el marcador la línea queda como etiqueta sin valor
    (p. ej. "Radicación: [pendiente]", "Fecha: [DATO PENDIENTE]"), se elimina
    completa; si el marcador encabezaba un numeral o ítem, se retira el
    numeral/bullet ya vaciado ("3. [DATO PENDIENTE] ..." queda el texto).
    """
    lineas = []
    for raw in (texto or "").splitlines():
        tenia_marcador = bool(_RE_PENDIENTE.search(raw))
        linea = _RE_PENDIENTE.sub("", raw)
        if tenia_marcador:
            linea = _RE_RESTOS_PENDIENTE.sub("", linea)
        if not linea.strip():
            continue
        if re.search(r":\s*$", linea):
            continue
        lineas.append(linea.rstrip())
    return "\n".join(lineas)


def _sanear_contenido_ia(texto: str) -> str:
    """Pipeline previo a renderizar el cuerpo IA: quita markdown y pendientes,
    en ese orden para que el corte de secciones funcione sobre texto plano."""
    return _quitar_markdown(_quitar_marcadores_pendientes(texto))


def _render_contenido_ia(pdf: FPDF, contenido: str) -> None:
    """Renderiza el texto generado por la IA (ya verificado) como cuerpo del PDF.

    Las líneas que parecen títulos de sección (numeración romana o MAYÚSCULAS
    cortas) se resaltan en negrita; el resto va como párrafos justificados.
    """
    for linea in contenido.splitlines():
        limpia = linea.strip()
        if not limpia:
            pdf.ln(2)
            continue
        if _TITULO_SECCION_RE.match(limpia) or (limpia.isupper() and len(limpia) <= 90):
            pdf.ln(2)
            pdf.section_title(limpia)
        else:
            pdf.body_text(limpia)


class TutelaPDF(FPDF):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # DejaVu cubre Unicode completo (tildes, —, «», ¿¡…); si las fuentes
        # no están disponibles cae a Times core con saneo latin-1.
        try:
            self.add_font("DejaVu", "", os.path.join("fonts", "DejaVuSans.ttf"))
            self.add_font("DejaVu", "B", os.path.join("fonts", "DejaVuSans-Bold.ttf"))
            self.add_font("DejaVu", "I", os.path.join("fonts", "DejaVuSans-Oblique.ttf"))
            self.fuente = "DejaVu"
        except Exception:
            self.fuente = "Times"

    def header(self):
        self.set_font(self.fuente, "B", 14)
        self.cell(0, 10, "ACCIÓN DE TUTELA", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font(self.fuente, "I", 8)
        self.cell(0, 10, f"Página {self.page_no()}/{{nb}}", align="C")

    def section_title(self, title: str):
        self.set_font(self.fuente, "B", 12)
        titulo = _limpiar_texto(title)
        if self.fuente == "Times":
            titulo = titulo.encode("latin-1", errors="replace").decode("latin-1")
        self.cell(0, 8, titulo, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body_text(self, text: str):
        self.set_font(self.fuente, "", 11)
        texto = _limpiar_texto(text)
        if self.fuente == "Times":
            texto = texto.encode("latin-1", errors="replace").decode("latin-1")
        self.multi_cell(0, 5.5, texto)
        self.ln(2)


def generar_pdf(datos: dict, contenido_tutela: str | None = None) -> str:
    cedula_pdf = (datos.get("accionante_cedula") or "").strip()
    ruta = path_tutela_pdf_nombre(f"{cedula_pdf}_tutela" if cedula_pdf else "tutela")

    pdf = TutelaPDF()
    pdf.alias_nb_pages()
    pdf.add_page()

    ciudad = datos.get("ciudad", "_________")
    nombre = datos.get("accionante_nombre") or "___________"
    tipo_doc = datos.get("accionante_tipo_doc", "CC")
    cedula = datos.get("accionante_cedula", "")
    telefono = datos.get("accionante_telefono") or "__________"
    email = datos.get("accionante_email") or "__________"

    # Fecha en español (meses sin depender del locale) = fecha de radicación.
    fecha = _fecha_espanol()

    pruebas_paths = datos.get("pruebas_paths", [])
    pruebas_analizadas = datos.get("pruebas_analizadas", [])
    pruebas_fotos = _filtrar_pruebas(pruebas_paths, pruebas_analizadas)

    # Encabezado determinista: ciudad + fecha de radicación + juez competente.
    _render_encabezado(pdf, ciudad, fecha)

    # Secciones I/II SIEMPRE desde `datos` (fuente de verdad): garantiza que el
    # NIT, el correo de notificación, la dirección y los datos del accionante
    # aparezcan sin importar lo que la IA haya escrito (u omitido).
    _seccion_accionante(pdf, datos)
    _seccion_accionado(pdf, datos)

    if contenido_tutela:
        # Modo IA: el cuerpo legal (HECHOS en adelante) se toma del texto
        # verificado; encabezado/accionante/accionado ya se imprimieron arriba.
        cuerpo = _sanear_contenido_ia(contenido_tutela)
        _render_contenido_ia(pdf, _cuerpo_sin_encabezado_y_partes(cuerpo))
    else:
        # Modo plantilla: respaldo si la IA no generó texto — se arma desde `datos`.

        # III. Hechos (usar solo los hechos del caso, NO el texto completo de la tutela)
        hechos = datos.get("hechos", "").strip()
        if hechos:
            pdf.section_title("III. HECHOS")
            pdf.body_text(hechos)
        else:
            pdf.section_title("III. HECHOS")
            pdf.body_text("No se especificaron hechos.")

        # IV. Derechos vulnerados
        derechos = datos.get("derechos_vulnerados", [])
        if derechos:
            pdf.section_title("IV. DERECHOS VULNERADOS")
            for d in derechos:
                pdf.body_text(f"- {d}")

        # V. Petición
        peticion = datos.get("peticion", "")
        if peticion:
            pdf.section_title("V. PETICIÓN")
            pdf.body_text(peticion)

        # VI. Juramento
        pdf.section_title("VI. JURAMENTO")
        pdf.body_text(
            "Bajo la gravedad de juramento, afirmo que no he promovido "
            "ni promuevo otra acción de tutela por los mismos hechos y derechos "
            "ante ningún otro juez de la República, conforme al artículo "
            "37 del Decreto 2591 de 1991."
        )

        # VII. Pruebas
        if pruebas_fotos:
            pdf.section_title("VII. PRUEBAS")
            pdf.body_text(
                "Se adjuntan los soportes de la solicitud, que incluyen "
                "evidencia documental de los hechos narrados y las respuestas "
                "de la entidad accionada."
            )

        # VIII. Notificaciones
        pdf.section_title("VIII. NOTIFICACIONES")
        pdf.body_text("El accionante recibirá notificaciones en:")
        pdf.body_text(f"  Email: {email}")
        pdf.body_text(f"  Teléfono: {telefono}")
        e_notif = normalizar_dato_obligatorio(datos.get("accionado_email", ""))
        if e_notif:
            pdf.body_text(f"El accionado recibirá notificaciones en: {e_notif}")

        # IX. Firma
        pdf.ln(15)
        pdf.body_text("Atentamente,")
        pdf.ln(15)
        pdf.body_text(nombre)
        _firma_documento(pdf, tipo_doc, cedula)
        pdf.body_text(f"Email: {email}")

    # X. Anexos — imágenes de las pruebas incrustadas en el PDF
    _anexar_pruebas(pdf, pruebas_fotos)

    pdf.output(ruta)
    # Los PDFs de soporte se unen intactos al final (el texto sigue siendo
    # seleccionable; no se rasterizan ni se "interpretan").
    _fusionar_pdfs_anexos(ruta, pruebas_fotos)
    return ruta


IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
PDF_EXT = {".pdf"}


def _firma_documento(pdf, tipo_doc: str, cedula: str) -> None:
    """Imprime la línea de documento en la firma solo si el dato es real.

    Omite la línea si la cédula está vacía o es un placeholder generado por
    el bot, evitando firmar un documento con datos ficticios.
    """
    tipo = (tipo_doc or "").strip().upper()
    ced = (cedula or "").strip().upper()
    if ced and ced not in ("___________", "____________"):
        pdf.body_text(f"{tipo or 'CC'}. {ced}")
    elif tipo and tipo not in ("CC", "CE"):
        pdf.body_text(tipo)


def _filtrar_pruebas(pruebas_paths: list[str], pruebas_analizadas: list[str]) -> list[tuple[str, str, str]]:
    """Devuelve las pruebas válidas (imágenes y PDFs): (ruta, nombre, analisis)."""
    pruebas = []
    for i, ruta in enumerate(pruebas_paths or []):
        if not ruta or not os.path.exists(ruta):
            continue
        ext = os.path.splitext(ruta)[1].lower()
        if ext in IMG_EXT:
            try:
                with Image.open(ruta) as img:
                    img.verify()
            except (OSError, Image.UnidentifiedImageError):
                continue
        elif ext in PDF_EXT:
            try:
                with fitz.open(ruta) as doc:
                    if doc.page_count == 0:
                        continue
            except (fitz.FileDataError, OSError, ValueError):
                continue
        else:
            continue
        analisis = pruebas_analizadas[i] if i < len(pruebas_analizadas) else ""
        pruebas.append((ruta, os.path.basename(ruta), analisis))
    return pruebas


def _anexar_pruebas(pdf, pruebas: list[tuple[str, str, str]]) -> int:
    """Incrusta las fotos como anexos (los PDFs de soporte se saltan aqui:
    se fusionan intactos al final en _fusionar_pdfs_anexos)."""
    count = 0
    for i, (ruta, _, analisis) in enumerate(pruebas):
        ext = os.path.splitext(ruta)[1].lower()
        if ext in PDF_EXT:
            continue
        count += 1
        pdf.add_page()
        pdf.section_title(f"ANEXO {count} - PRUEBA {i + 1}")
        if analisis:
            pdf.body_text(analisis[:200])
        pdf.ln(3)
        _insertar_imagen(pdf, ruta)
    return count


def _fusionar_pdfs_anexos(ruta_tutela: str, pruebas: list[tuple[str, str, str]]) -> None:
    """Anexa los PDFs de soporte al Cál final del documento generado, intactos.

    `ruta_tutela` ya contiene el PDF generado por fpdf; lo abrimos con PyMuPDF
    y le insertamos las páginas de cada PDF de prueba sin rasterizar (el texto
    sigue siendo seleccionable). Se graba en un archivo temporal y se reemplaza
    para no dejar un PDF corrupto si algo falla a mitad de camino.
    """
    pdfs = [p for p in pruebas if os.path.splitext(p[0])[1].lower() in PDF_EXT]
    if not pdfs:
        return
    ruta_tmp = ruta_tutela + ".tmp.pdf"
    doc = fitz.open(ruta_tutela)
    try:
        for ruta, _, _ in pdfs:
            try:
                src = fitz.open(ruta)
            except (fitz.FileDataError, OSError, ValueError):
                continue
            doc.insert_pdf(src)
            src.close()
        doc.save(ruta_tmp)
    finally:
        doc.close()
    os.replace(ruta_tmp, ruta_tutela)


def _insertar_imagen(pdf, ruta: str) -> None:
    """Inserta una imagen centrada, escalada para caber en la página (A4: 210x297mm, margen 15mm)."""
    margen = 15
    ancho_max = pdf.w - 2 * margen
    alto_max = pdf.h - 2 * margen - 25
    with Image.open(ruta) as img:
        w, h = img.size
    ratio = min(ancho_max / w, alto_max / h, 1.0)
    ancho = w * ratio
    alto = h * ratio
    x = (pdf.w - ancho) / 2
    pdf.image(ruta, x=x, y=pdf.get_y(), w=ancho, h=alto)
