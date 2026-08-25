import os
import re
from datetime import UTC, datetime

import fitz
from fpdf import FPDF
from PIL import Image

from app.utils.file_utils import path_tutela_pdf

# Títulos de sección estilo "IV. HECHOS" / "VIII. PRETENSIONES"
_TITULO_SECCION_RE = re.compile(r"^[IVXLCDM]{1,4}\.\s+\S")

# Tipografía unicode común en texto de IA que la fuente core (latin-1) no soporta
_REEMPLAZOS_TIPografICOS = {
    "\u2014": "-", "\u2013": "-", "\u2018": "'", "\u2019": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u2022": "-",
}


def _latin1_seguro(texto: str) -> str:
    """Convierte tipografía unicode a equivalentes latin-1 para la fuente Times."""
    t = "".join(_REEMPLAZOS_TIPografICOS.get(c, c) for c in texto)
    return t.encode("latin-1", errors="replace").decode("latin-1")


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
    def header(self):
        self.set_font("Times", "B", 14)
        self.cell(0, 10, "ACCION DE TUTELA", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Times", "I", 8)
        self.cell(0, 10, f"Página {self.page_no()}/{{nb}}", align="C")

    def section_title(self, title: str):
        self.set_font("Times", "B", 12)
        self.cell(0, 8, _latin1_seguro(title), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body_text(self, text: str):
        self.set_font("Times", "", 11)
        self.multi_cell(0, 5.5, _latin1_seguro(text))
        self.ln(2)


def generar_pdf(datos: dict, contenido_tutela: str | None = None) -> str:
    ruta = path_tutela_pdf()

    pdf = TutelaPDF()
    pdf.alias_nb_pages()
    pdf.add_page()

    ciudad = datos.get("ciudad", "_________")
    accionante = datos.get("accionante_nombre", "___________")
    tipo_doc = datos.get("accionante_tipo_doc", "CC")
    cedula = datos.get("accionante_cedula", "____________")
    direccion = datos.get("accionante_direccion", "")
    telefono = datos.get("accionante_telefono", "__________")
    email = datos.get("accionante_email", "__________")
    accionado = datos.get("accionado", "___________")
    accionado_tipo = datos.get("accionado_tipo", "")
    accionado_nit = datos.get("accionado_nit", "")
    accionado_email = datos.get("accionado_email", "")
    departamento = datos.get("departamento", "")

    now = datetime.now(UTC)
    fecha = now.strftime("%d de %B de %Y").lower()

    pruebas_paths = datos.get("pruebas_paths", [])
    pruebas_analizadas = datos.get("pruebas_analizadas", [])
    pruebas_fotos = _filtrar_pruebas(pruebas_paths, pruebas_analizadas)

    if contenido_tutela:
        # Modo IA: el texto generado (estructura I-XI, ya verificado) ES el documento.
        _render_contenido_ia(pdf, contenido_tutela)
        _anexar_pruebas(pdf, pruebas_fotos)
        pdf.output(ruta)
        return ruta

    # Modo plantilla: respaldo si la IA no generó texto — se arma desde `datos`.

    # Encabezado (formato legal colombiano)
    pdf.set_font("Times", "B", 14)
    pdf.cell(0, 10, "ACCION DE TUTELA", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)

    pdf.set_font("Times", "B", 12)
    pdf.cell(0, 6, f"{ciudad}, {fecha}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Señor JUEZ CONSTITUCIONAL DE {ciudad.upper()} (REPARTO)", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "E.S.D.", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)

    # I. Accionante
    pdf.section_title("I. ACCIONANTE")
    pdf.body_text(f"Nombre: {accionante}")
    pdf.body_text(f"Documento: {tipo_doc} {cedula}")
    if direccion:
        pdf.body_text(f"Dirección: {direccion}")
    pdf.body_text(f"Teléfono: {telefono}")
    pdf.body_text(f"Correo electrónico: {email}")
    pdf.body_text(f"Ciudad: {ciudad}" + (f", {departamento}" if departamento else ""))

    # II. Accionado
    pdf.section_title("II. ACCIONADO")
    pdf.body_text(f"Nombre: {accionado}")
    if accionado_tipo:
        pdf.body_text(f"Tipo: {accionado_tipo}")
    if accionado_nit and accionado_nit != "desconocido":
        pdf.body_text(f"NIT: {accionado_nit}")
    if accionado_email and accionado_email != "desconocido":
        pdf.body_text(f"Email notificación: {accionado_email}")

    # III. Hechos (usar solo los hechos del caso, NO el texto completo de la tutela)
    pdf.section_title("III. HECHOS")
    hechos = datos.get("hechos", "").strip()
    if hechos:
        pdf.body_text(hechos)
    else:
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

    # VII. Pruebas — fotos y PDFs adjuntos
    if pruebas_fotos:
        pdf.section_title("VII. PRUEBAS")
        pdf.body_text("Se adjuntan las siguientes pruebas:")
        for _, nombre, analisis in pruebas_fotos:
            texto = f"- {nombre}: {analisis[:150] if analisis else 'Documento adjunto'}"
            pdf.body_text(texto)

    # VIII. Notificaciones
    pdf.section_title("VIII. NOTIFICACIONES")
    pdf.body_text("El accionante recibirá notificaciones en:")
    pdf.body_text(f"  Email: {email}")
    pdf.body_text(f"  Teléfono: {telefono}")
    if accionado_email and accionado_email != "desconocido":
        pdf.body_text(f"El accionado recibirá notificaciones en: {accionado_email}")

    # IX. Firma
    pdf.ln(15)
    pdf.body_text("Atentamente,")
    pdf.ln(15)
    pdf.body_text(accionante)
    _firma_documento(pdf, tipo_doc, cedula)
    pdf.body_text(f"Email: {email}")

    # X. Anexos — imágenes de las pruebas incrustadas en el PDF
    _anexar_pruebas(pdf, pruebas_fotos)

    pdf.output(ruta)
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
    """Incrusta las fotos y las páginas de los PDFs como anexos al final del PDF."""
    count = 0
    for i, (ruta, _, analisis) in enumerate(pruebas):
        ext = os.path.splitext(ruta)[1].lower()
        count += 1
        pdf.add_page()
        pdf.section_title(f"ANEXO {count} - PRUEBA {i + 1}")
        if analisis:
            pdf.body_text(analisis[:200])
        pdf.ln(3)
        if ext in PDF_EXT:
            try:
                with fitz.open(ruta) as doc:
                    for pagina in doc:
                        pix = pagina.get_pixmap(matrix=fitz.Matrix(2, 2))
                        temp = os.path.join(os.path.dirname(ruta), f"_pagina_{count}_{pagina.number}.png")
                        pix.save(temp)
                        _insertar_imagen(pdf, temp)
                        os.remove(temp)
            except (fitz.FileDataError, OSError, ValueError):
                count -= 1
                continue
        else:
            _insertar_imagen(pdf, ruta)
    return count


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
