import os
from datetime import UTC, datetime

from fpdf import FPDF

from app.utils.file_utils import path_tutela_pdf


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
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def body_text(self, text: str):
        self.set_font("Times", "", 11)
        self.multi_cell(0, 5.5, text)
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

    # VII. Pruebas
    pruebas_paths = datos.get("pruebas_paths", [])
    pruebas_urls = datos.get("pruebas_urls", [])
    pruebas_analizadas = datos.get("pruebas_analizadas", [])
    num_pruebas = max(len(pruebas_paths), len(pruebas_urls), len(pruebas_analizadas))
    if num_pruebas:
        pdf.section_title("VII. PRUEBAS")
        pdf.body_text("Se adjuntan los siguientes documentos:")
        for i in range(num_pruebas):
            analisis = pruebas_analizadas[i] if i < len(pruebas_analizadas) else ""
            nombre = ""
            if i < len(pruebas_paths):
                nombre = os.path.basename(pruebas_paths[i])
            elif i < len(pruebas_urls):
                nombre = pruebas_urls[i].split("/")[-1][:30]
            texto = f"- {nombre or f'Documento {i+1}'}: {analisis[:150] if analisis else 'Documento adjunto'}"
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
    pdf.body_text(f"{tipo_doc}. {cedula}")
    pdf.body_text(f"Email: {email}")

    pdf.output(ruta)
    return ruta
