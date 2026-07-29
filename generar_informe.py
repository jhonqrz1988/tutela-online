"""Genera un informe detallado del proyecto Tutelas Online AI en PDF."""
import os
from fpdf import FPDF


class InformePDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font("Times", "I", 8)
            self.cell(0, 8, "Tutelas Online AI - Informe del Proyecto", align="C", new_x="LMARGIN", new_y="NEXT")
            self.ln(2)

    def footer(self):
        self.set_y(-15)
        self.set_font("Times", "I", 8)
        self.cell(0, 10, f"Pagina {self.page_no()}/{{nb}}", align="C")

    def titulo(self, text):
        self.set_font("Times", "B", 16)
        self.cell(0, 10, text, align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(4)

    def subtitulo(self, text):
        self.set_font("Times", "B", 13)
        self.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def seccion(self, text):
        self.set_font("Times", "B", 11)
        self.cell(0, 7, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def parrafo(self, text):
        self.set_font("Times", "", 10)
        self.multi_cell(0, 5, text)
        self.ln(2)

    def bullet(self, text):
        self.set_font("Times", "", 10)
        x = self.get_x()
        self.cell(5, 5, "-")
        self.multi_cell(0, 5, text)
        self.ln(1)

    def par(self, label, value):
        self.set_font("Times", "B", 10)
        self.cell(self.get_string_width(label) + 2, 5, label + ": ")
        self.set_font("Times", "", 10)
        self.multi_cell(0, 5, value)
        self.ln(1)


def generar_informe():
    ruta = os.path.join("storage", "informe_proyecto.pdf")
    os.makedirs("storage", exist_ok=True)

    pdf = InformePDF()
    pdf.alias_nb_pages()

    # --- PORTADA ---
    pdf.add_page()
    pdf.ln(40)
    pdf.set_font("Times", "B", 24)
    pdf.cell(0, 12, "TUTELAS ONLINE AI", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)
    pdf.set_font("Times", "", 14)
    pdf.cell(0, 8, "Informe Detallado del Proyecto", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(10)
    pdf.set_font("Times", "I", 11)
    pdf.cell(0, 6, "Sistema automatizado de radicacion de Acciones de Tutela", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "via WhatsApp + Inteligencia Artificial", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(20)
    pdf.set_font("Times", "", 10)
    pdf.cell(0, 6, "Julio 2026", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "Version 0.1.0", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "Repositorio: github.com/jhonqrz1988/tutela-online", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "URL Produccion: https://tutela-online-production.up.railway.app", align="C", new_x="LMARGIN", new_y="NEXT")

    # --- INDICE ---
    pdf.add_page()
    pdf.titulo("INDICE")
    secciones = [
        "1. Resumen Ejecutivo",
        "2. Que es una Accion de Tutela",
        "3. Descripcion del Sistema",
        "4. Stack Tecnologico",
        "5. Arquitectura del Proyecto",
        "6. Flujo Completo de Operacion",
        "7. Base de Datos",
        "8. Inteligencia Artificial",
        "9. Integracion WhatsApp",
        "10. Automatizacion Rama Judicial",
        "11. Infraestructura y Despliegue",
        "12. Estado Actual del Proyecto",
        "13. Pendientes y Roadmap",
        "14. Modelo de Negocio",
        "15. Proyecciones Financieras",
    ]
    for s in secciones:
        pdf.parrafo(s)

    # --- 1. RESUMEN EJECUTIVO ---
    pdf.add_page()
    pdf.titulo("1. RESUMEN EJECUTIVO")
    pdf.parrafo(
        "Tutelas Online AI es una plataforma tecnologica que permite a cualquier "
        "ciudadano colombiano radicar una Accion de Tutela de forma automatica "
        "a traves de WhatsApp, sin necesidad de abogados ni conocimientos legales. "
        "El sistema utiliza Inteligencia Artificial (Groq Llama 3.3) para extraer "
        "automaticamente los datos del relato del usuario, generar el documento "
        "legal en formato PDF con toda la estructura juridica requerida, y radicarlo "
        "en el portal oficial de la Rama Judicial."
    )
    pdf.parrafo(
        "El proyecto opera actualmente en produccion en Railway bajo un modelo "
        "freemium: el usuario recibe el PDF de la tutela de forma gratuita, y "
        "puede pagar $20.000 COP por la radicacion automatica completa. "
        "El servicio de WhatsApp esta activo via Twilio Sandbox a la espera de "
        "migrar a un numero de produccion."
    )

    # --- 2. QUE ES UNA ACCION DE TUTELA ---
    pdf.add_page()
    pdf.titulo("2. QUE ES UNA ACCION DE TUTELA")
    pdf.parrafo(
        "La Accion de Tutela es un mecanismo constitucional colombiano (Art. 86 "
        "Constitucion Politica de Colombia, reglamentado por el Decreto 2591 de 1991) "
        "que permite a cualquier persona reclamar ante los jueces la proteccion "
        "inmediata de sus derechos fundamentales cuando estos son vulnerados o "
        "amenazados por la accion u omision de cualquier autoridad publica o "
        "particular. Es un proceso preferente, sumario e informal que no requiere "
        "abogado."
    )
    pdf.parrafo("Tipos de tutela soportados actualmente:")
    pdf.bullet("Salud: Negacion de tratamientos, citas, medicamentos por parte de EPS")
    pdf.bullet("Fotomultas: Comparendos injustos")
    pdf.bullet("Derecho de Peticion: Entidades que no responden solicitudes")

    # --- 3. DESCRIPCION DEL SISTEMA ---
    pdf.add_page()
    pdf.titulo("3. DESCRIPCION DEL SISTEMA")
    pdf.parrafo(
        "El sistema funciona como un asistente legal virtual alojado en WhatsApp. "
        "El usuario interactua mediante mensajes de texto y voz, y el bot lo guia "
        "paso a paso a traves de todo el proceso de tutela. No requiere "
        "instalacion de aplicaciones ni conocimientos tecnicos."
    )
    pdf.seccion("Funcionalidades principales:")
    pdf.bullet("Chat conversacional WhatsApp con menu de opciones")
    pdf.bullet("Recepcion y transcripcion de notas de voz")
    pdf.bullet("Extraccion automatica de datos mediante IA")
    pdf.bullet("Validacion inteligente de campos (cedula, email, telefono)")
    pdf.bullet("Generacion de PDF legal con estructura juridica completa")
    pdf.bullet("Analisis de imagenes con IA (fotos de documentos)")
    pdf.bullet("Radicacion automatica en el portal de la Rama Judicial")
    pdf.bullet("Dashboard administrativo con historial y estadisticas")
    pdf.bullet("Soporte para 3 proveedores WhatsApp: Twilio, Meta, Z-API")
    pdf.bullet("Consentimiento de datos (Ley 1581 de 2012) y eliminacion de datos")

    # --- 4. STACK TECNOLOGICO ---
    pdf.add_page()
    pdf.titulo("4. STACK TECNOLOGICO")

    pdf.seccion("Lenguaje y Framework")
    pdf.par("Lenguaje", "Python 3.12")
    pdf.par("Framework Web", "FastAPI 0.139.0")
    pdf.par("Servidor ASGI", "Uvicorn 0.51.0")

    pdf.seccion("Base de Datos")
    pdf.par("Motor", "SQLite 3")
    pdf.par("ORM", "SQLAlchemy 2.0.51 (sync)")
    pdf.par("Migraciones", "Creacion automatica via Base.metadata.create_all()")

    pdf.seccion("Inteligencia Artificial")
    pdf.par("Proveedor", "Groq Cloud")
    pdf.par("Modelo Chat", "llama-3.3-70b-versatile")
    pdf.par("Modelo Whisper", "whisper-large-v3 (transcripcion audio)")
    pdf.par("SDK", "OpenAI Python 2.45.0 (compatible con API de Groq)")
    pdf.par("Vision IA", "Analisis de imagenes de documentos via GPT-4o-mini/Groq")

    pdf.seccion("WhatsApp")
    pdf.par("Proveedor Activo", "Twilio Sandbox (+14155238886)")
    pdf.par("Libreria", "Twilio Python 9.10.9")
    pdf.par("Proveedores Alternativos", "Meta WhatsApp Business API, Z-API")

    pdf.seccion("Automatizacion (RPA)")
    pdf.par("Libreria", "Playwright 1.61.0 (Chromium)")
    pdf.par("Portal Objetivo", "procesojudicial.ramajudicial.gov.co/TutelaEnLinea")
    pdf.par("Estado", "Simulacion (SIMULATE_BOT=true)")

    pdf.seccion("PDF")
    pdf.par("Libreria", "fpdf2 2.8.7")
    pdf.par("Estructura", "8 secciones: Accionante, Accionado, Hechos, Derechos, Peticion, Juramento, Pruebas, Notificaciones")
    pdf.par("Fuente", "DejaVuSans (Unicode, soporte espanol)")

    pdf.seccion("Infraestructura")
    pdf.par("Hosting", "Railway (Docker)")
    pdf.par("Contenedor", "python:3.12-slim")
    pdf.par("Orquestacion", "Dockerfile + railway.json")
    pdf.par("URL", "https://tutela-online-production.up.railway.app")
    pdf.par("Health Check", "GET /admin (reinicio automatico)")

    pdf.seccion("Otras Dependencias")
    pdf.bullet("APScheduler 3.11.3 - Tareas programadas (radicacion automatica)")
    pdf.bullet("python-multipart 0.0.32 - Recepcion de archivos en webhooks")
    pdf.bullet("Jinja2 3.1.6 - Templates HTML para dashboard")
    pdf.bullet("httpx 0.28.1 - Cliente HTTP async para descargas y APIs externas")
    pdf.bullet("pydantic-settings 2.14.2 - Gestion de configuracion via .env")
    pdf.bullet("aiofiles 25.1.0 - Operaciones de archivo async")
    pdf.bullet("python-dotenv 1.1 - Carga de variables de entorno")

    # --- 5. ARQUITECTURA ---
    pdf.add_page()
    pdf.titulo("5. ARQUITECTURA DEL PROYECTO")
    pdf.parrafo("Estructura de directorios del proyecto:")
    pdf.set_font("Courier", "", 8)
    arbol = """app/
  api/
    admin.py          Dashboard admin + chat web + descarga PDF
    health.py         Health check
    tutelas.py        CRUD de tutelas via API REST
    webhook_whatsapp.py  Webhooks Twilio/Meta/Z-API + maquina de estados
  bot/
    browser.py        Administrador singleton de Playwright
    navegador.py      RadicadorBot: selectores reales del portal
  models/
    user.py           User: id, telefono, nombre, email, estado, consentimiento
    tutela.py         Tutela: tipo, estado, datos_json, pdf_path
    whatsapp.py       MensajeWhatsApp: body, tipo, media_url, tutela_id
    radicacion.py     Radicacion: estado, num_radicado, constancia_path
  services/
    whatsapp_service.py  Envio de mensajes multi-proveedor
    ia_service.py        IA: extraccion, generacion tutela, transcripcion, vision
    documento_service.py  Generacion PDF con fpdf2
    radicacion_service.py Orquestador de radicacion
  tasks/
    scheduler.py      APScheduler (cron diario)
    jobs.py           Tareas programadas
  config.py           Settings con pydantic-settings
  database.py         SQLAlchemy engine + session factory
  main.py             FastAPI app + routers + lifespan"""
    for linea in arbol.split("\n"):
        if linea.strip():
            pdf.cell(0, 3.5, linea, new_x="LMARGIN", new_y="NEXT")

    pdf.ln(5)
    pdf.set_font("Times", "", 10)
    pdf.seccion("Patrones de diseno utilizados:")
    pdf.bullet("Maquina de estados: La conversacion WhatsApp transita por estados (borrador, recogiendo_datos, datos_listos, confirmada, pdf_generado, esperando_confirmacion)")
    pdf.bullet("Strategy: Multiples proveedores WhatsApp (Twilio, Meta, Z-API) con misma interfaz")
    pdf.bullet("Singleton: BrowserManager (una unica instancia de Playwright)")
    pdf.bullet("Repository: Acceso a datos via SQLAlchemy ORM con session por request")

    # --- 6. FLUJO COMPLETO ---
    pdf.add_page()
    pdf.titulo("6. FLUJO COMPLETO DE OPERACION")
    pdf.parrafo("El siguiente es el flujo detallado que sigue un usuario desde que escribe 'Hola' hasta que recibe su tutela radicada:")
    pdf.ln(2)
    pasos = [
        ("1. Inicio", "Usuario envia 'Hola' al numero de WhatsApp. El bot responde con aviso de privacidad (Ley 1581/2012) y solicita consentimiento."),
        ("2. Consentimiento", "Usuario responde 'Acepto' o 'No'. Si acepta, se marca consentimiento=True en la BD."),
        ("3. Menu", "Bot muestra menu: 1. Salud / 2. Fotomultas / 3. Derecho de Peticion. Usuario selecciona."),
        ("4. Narracion", "Bot pide 'Cuentame tu caso'. Usuario escribe o envia audio. Si es audio: se transcribe con Whisper, se muestra texto y se pide confirmacion."),
        ("5. Extraccion IA", "Texto enviado a Groq Llama 3.3 que extrae automaticamente: nombre, cedula, ciudad, entidad, hechos, derechos vulnerados, etc. en formato JSON."),
        ("6. Campos faltantes", "Si faltan datos obligatorios, bot pregunta uno por uno con validacion (solo numeros para cedula, email valido, telefono 10+ digitos)."),
        ("7. Resumen", "Bot muestra resumen con todos los datos y pregunta 'Es correcto? Responde SI o corrige'."),
        ("8. Juramento", "Usuario confirma. Bot pregunta 'Bajo juramento, no has interpuesto otra tutela? 1. Si juro / 2. No'."),
        ("9. Pruebas", "Usuario jura. Bot pide enviar fotos/soportes (opcional). Si no tiene, escribe 'Continuar'."),
        ("10. Generacion PDF", "Bot genera PDF legal con estructura de 8 secciones, incluyendo jurisprudencia citada (T-760/2008 para salud)."),
        ("11. Envio PDF", "Bot envia PDF por WhatsApp al usuario (via tmpfiles.org para URL publica)."),
        ("12. Confirmacion", "Bot pregunta 'Deseas radicar? 1. Si / 2. No'."),
        ("13. Radicacion", "Si acepta: bot inicia Playwright, navega al portal Rama Judicial, llena formulario, resuelve reCAPTCHA, sube PDF, envia."),
        ("14. Resultado", "Bot entrega numero de radicado y constancia en PDF. Guarda en BD."),
    ]
    for tit, desc in pasos:
        pdf.seccion(tit)
        pdf.parrafo(desc)

    # --- 7. BASE DE DATOS ---
    pdf.add_page()
    pdf.titulo("7. BASE DE DATOS")
    pdf.parrafo("El sistema utiliza SQLite con SQLAlchemy ORM (sync). Motor ligero ideal para prototipo, escalable a PostgreSQL.")

    pdf.seccion("Tabla: users")
    pdf.parrafo("id (PK, int), telefono (unique, str), nombre (nullable), email (nullable), estado (str: nuevo/activo/rechazado), consentimiento (bool), created_at, updated_at")

    pdf.seccion("Tabla: tutelas")
    pdf.parrafo("id (PK, int), user_id (FK), tipo (str: salud/fotomultas/derecho_peticion), estado (str: borrador/recogiendo_datos/datos_listos/juramento_pendiente/confirmada/pdf_generado/esperando_confirmacion/pendiente_radicacion/radicada/fallida), datos_json (Text), pdf_path (nullable), created_at, updated_at")

    pdf.seccion("Tabla: radicaciones")
    pdf.parrafo("id (PK, int), tutela_id (FK), estado (str: pendiente/procesando/completada/fallida), num_radicado (nullable), constancia_path (nullable), token_verificacion (nullable), intentos (int), ultimo_error (nullable), created_at, updated_at")

    pdf.seccion("Tabla: mensajes_whatsapp")
    pdf.parrafo("id (PK, int), from_number, body, tipo_mensaje (texto/audio), media_url (nullable), metadata_json (nullable), tutela_id (FK nullable), es_recibido (bool), created_at")

    # --- 8. IA ---
    pdf.add_page()
    pdf.titulo("8. INTELIGENCIA ARTIFICIAL")
    pdf.seccion("Proveedor: Groq Cloud")
    pdf.parrafo(
        "Groq Cloud ofrece inferencia de LLMs a alta velocidad gracias a sus chips LPU "
        "(Language Processing Units). Usamos el modelo llama-3.3-70b-versatile para "
        "todas las tareas de texto y whisper-large-v3 para transcripcion de audio."
    )

    pdf.seccion("Tareas de IA implementadas:")
    pdf.par("Extraccion de datos", "Analiza el relato del usuario y extrae 15 campos estructurados en JSON: tipo, nombre, cedula, tipo_doc, telefono, email, ciudad, departamento, accionado, accionado_tipo, accionado_nit, accionado_email, hechos, derechos_vulnerados, peticion. Detecta genero automaticamente.")
    pdf.par("Generacion de tutela", "Redacta el texto legal completo con 10 secciones, citando articulos constitucionales (Art. 86, 49, 23) y jurisprudencia (T-760/2008 para salud). Adapta genero (masculino/femenino) en todo el texto. Lenguaje formal y juridicamente solido.")
    pdf.par("Transcripcion de audio", "Convierte notas de voz a texto usando Whisper-large-v3 (Groq). El usuario confirma la transcripcion antes de procesar.")
    pdf.par("Analisis de imagenes", "Vision IA para analizar fotos de documentos: extrae tipo de documento, numero, nombres, fechas, entidad emisora.")

    pdf.seccion("System Prompts destacados:")
    pdf.bullet("SISTEMA_EXTRACCION: Prompt especializado para extraer datos legales en JSON desde texto libre")
    pdf.bullet("SISTEMA_TUTELA: Prompt de 20+ lineas con estructura legal exacta, articulos constitucionales, jurisprudencia obligatoria y formato de juez")

    # --- 9. WHATSAPP ---
    pdf.add_page()
    pdf.titulo("9. INTEGRACION WHATSAPP")
    pdf.parrafo(
        "El sistema soporta 3 proveedores de WhatsApp configurables via WHATSAPP_PROVIDER. "
        "Actualmente activo: Twilio Sandbox."
    )

    pdf.seccion("Twilio Sandbox (activo)")
    pdf.parrafo(
        "Numero: +14155238886. Sandbox permite hasta 5 numeros de telefono para pruebas. "
        "La cuenta tiene $13 de saldo pero esta sujeta a un limite diario de mensajes "
        "que se resetea a las 7pm (hora Colombia). Se usa para desarrollo y pruebas MVP."
    )

    pdf.seccion("Meta WhatsApp Business API")
    pdf.parrafo(
        "Alternativa de produccion. Requiere un numero de telefono verificado y aprobacion "
        "de Meta. La cuenta fue suspendida, esta pendiente de reactivacion."
    )

    pdf.seccion("Z-API")
    pdf.parrafo(
        "Plataforma brasileira de API WhatsApp con credenciales configuradas. Usa QR code "
        "para conectar un numero real."
    )

    pdf.seccion("Servicio de mensajeria (whatsapp_service.py)")
    pdf.parrafo(
        "Funciones: enviar_texto() y enviar_documento(). Para envio de PDF, el archivo se "
        "sube a tmpfiles.org, se extrae la URL de descarga real del HTML de respuesta, "
        "y se envia via Twilio Media API."
    )

    # --- 10. RAMA JUDICIAL ---
    pdf.add_page()
    pdf.titulo("10. AUTOMATIZACION RAMA JUDICIAL")
    pdf.parrafo(
        "El modulo de RPA (Robotic Process Automation) utiliza Playwright con Chromium "
        "para navegar el portal oficial de la Rama Judicial y radicar la tutela."
    )

    pdf.seccion("Portal objetivo")
    pdf.par("URL", "procesojudicial.ramajudicial.gov.co/TutelaEnLinea")
    pdf.par("Tecnologia", "ASP.NET con formularios, validacion AJAX, __RequestVerificationToken")
    pdf.par("reCAPTCHA", "Google reCAPTCHA v2 (site-key: 6LcnkeUUAAAAAIzytmwnkjif8k066vQVR6EKXFw0)")

    pdf.seccion("Pasos del formulario (documentados en navegador.py):")
    pdf.bullet("Modal inicial: Checkbox terminos (#enableCheckbox) + boton Continuar")
    pdf.bullet("Paso 1 - Lugar de envio: Departamento (#DdlDepartamento) + Ciudad (#DDlCiudad) - carga AJAX")
    pdf.bullet("Paso 2 - Tipo: Radio Tutela (#RdbTutela)")
    pdf.bullet("Paso 3 - Lugar hechos: Departamento (#DdlDepartamentoHechos) + Ciudad (#DDlCiudadHechos)")
    pdf.bullet("Paso 4 - Accionante: Tipo doc, Numero, Nombres, Telefono, Email, validacion correo")
    pdf.bullet("Paso 5 - Accionado: Tipo persona, Nombre/NIT, boton agregar")
    pdf.bullet("Paso 6 - Derechos: Select derecho, medida provisional, agregar")
    pdf.bullet("Paso 7 - Archivos: Tipo 'Tutela', input file (#ArchivoFile0), agregar")
    pdf.bullet("Paso 8 - Juramento: Checkbox (#CbManifiesto)")
    pdf.bullet("Paso 9 - Captcha: reCAPTCHA de Google")
    pdf.bullet("Paso 10 - Enviar: Boton #enviar")

    pdf.seccion("Estado actual")
    pdf.parrafo(
        "Modo simulacion (SIMULATE_BOT=true): genera numero de radicado falso y constancia "
        "ficticia. Los selectores estan documentados pero llenar_formulario() es un stub "
        "pendiente de implementar. Requiere integracion con 2Captcha (~$1/1000 tutelas) "
        "para resolver el reCAPTCHA."
    )

    # --- 11. INFRAESTRUCTURA ---
    pdf.add_page()
    pdf.titulo("11. INFRAESTRUCTURA Y DESPLIEGUE")
    pdf.seccion("Railway")
    pdf.parrafo(
        "Hosting cloud con despliegue via Docker. Build automatico desde GitHub. "
        "Balanceo, SSL automatico, health checks, y reinicio automatico en fallos."
    )
    pdf.par("URL", "https://tutela-online-production.up.railway.app")
    pdf.par("Region", "EE.UU. (us-west)")
    pdf.par("Plan", "Hobby ($5/mes aprox)")

    pdf.seccion("Docker")
    pdf.parrafo(
        "Dockerfile basado en python:3.12-slim con todas las dependencias del sistema "
        "para Playwright Chromium. Instala chromium via playwright install."
    )

    pdf.seccion("Variables de Entorno (Railway)")
    pdf.bullet("WHATSAPP_PROVIDER=twilio")
    pdf.bullet("TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER")
    pdf.bullet("AI_PROVIDER=groq, AI_API_KEY, AI_CHAT_MODEL=llama-3.3-70b-versatile")
    pdf.bullet("APP_URL=https://tutela-online-production.up.railway.app")
    pdf.bullet("DATABASE_URL=sqlite:///./storage/tutelas.db")
    pdf.bullet("SIMULATE_BOT=true")

    # --- 12. ESTADO ACTUAL ---
    pdf.add_page()
    pdf.titulo("12. ESTADO ACTUAL DEL PROYECTO")
    pdf.seccion("Funcionalidades completadas:")
    pdf.bullet("Flujo completo de conversacion WhatsApp con maquina de estados")
    pdf.bullet("3 tipos de tutela: Salud, Fotomultas, Derecho de Peticion")
    pdf.bullet("Extraccion de datos via IA (Groq Llama 3.3)")
    pdf.bullet("Generacion de PDF legal con estructura juridica (8 secciones)")
    pdf.bullet("Validacion de campos: cedula solo numeros, email valido, telefono 10+ digitos")
    pdf.bullet("Adaptacion de genero (masculino/femenino) en texto legal")
    pdf.bullet("Analisis de imagenes con Vision IA")
    pdf.bullet("Envio de PDF por WhatsApp via tmpfiles.org")
    pdf.bullet("Transcripcion de audios con Whisper + confirmacion usuario")
    pdf.bullet("Consentimiento de datos (Ley 1581/2012) y eliminacion de datos")
    pdf.bullet("Juramento con opciones numeradas (1. Si juro / 2. No)")
    pdf.bullet("Confirmacion antes de radicar: 'Deseas radicar? 1. Si / 2. No'")
    pdf.bullet("Dashboard admin con historial, filtros, detalle, descarga PDF")
    pdf.bullet("Chat web de prueba en /admin/chat")
    pdf.bullet("Despliegue en Railway con Docker")

    pdf.seccion("En pruebas:")
    pdf.bullet("Twilio Sandbox activo, flujo funcional")
    pdf.bullet("Radicacion en modo simulacion (SIMULATE_BOT=true)")

    pdf.seccion("Bloqueado temporalmente:")
    pdf.bullet("Twilio Sandbox: limite diario de mensajes (reset 7pm Colombia)")
    pdf.bullet("Meta API: cuenta suspendida")
    pdf.bullet("WATI: requiere plan pago, no tiene free trial util")

    # --- 13. PENDIENTES ---
    pdf.add_page()
    pdf.titulo("13. PENDIENTES Y ROADMAP")
    pdf.seccion("Alta prioridad:")
    pdf.bullet("Probar flujo completo con Twilio (cuando se resetee el limite diario)")
    pdf.bullet("Arreglar PDF: fuente Unicode para soporte completo de tildes/")
    pdf.bullet("Crear landing page publica (GET /)")
    pdf.bullet("Dashboard con embudo de conversion: generadas vs radicadas + ingresos")
    pdf.bullet("Implementar llenar_formulario() real en navegador.py")

    pdf.seccion("Media prioridad:")
    pdf.bullet("Modelo Payment + tabla de ingresos")
    pdf.bullet("Integrar 2Captcha para resolver reCAPTCHA del portal Rama Judicial")
    pdf.bullet("Migrar de SQLite a PostgreSQL")
    pdf.bullet("Agregar autenticacion al admin panel")
    pdf.bullet("Base de datos de entidades (EPS, NITs, correos notificacion)")
    pdf.bullet("Mas tipos de tutela: pensiones, vivienda, educativo, laboral")

    pdf.seccion("Baja prioridad / Futuro:")
    pdf.bullet("Migrar Twilio Sandbox a numero de produccion")
    pdf.bullet("Landing page completa con SEO")
    pdf.bullet("App movil")
    pdf.bullet("Notificaciones al admin (nueva tutela, pago, error)")
    pdf.bullet("Pruebas automaticas (unitarias e integracion)")

    # --- 14. MODELO DE NEGOCIO ---
    pdf.add_page()
    pdf.titulo("14. MODELO DE NEGOCIO")
    pdf.seccion("Estrategia Freemium")
    pdf.parrafo(
        "El modelo de negocio se basa en una estrategia freemium para maximizar "
        "la conversion y generar confianza en los usuarios:"
    )

    pdf.set_font("Times", "B", 10)
    col1 = "Producto"
    col2 = "Precio"
    col3 = "Descripcion"
    pdf.cell(50, 6, col1, border=1, align="C")
    pdf.cell(25, 6, col2, border=1, align="C")
    pdf.cell(105, 6, col3, border=1, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Times", "", 9)
    pdf.cell(50, 6, "PDF tutela + guia", border=1, align="C")
    pdf.cell(25, 6, "GRATIS", border=1, align="C")
    pdf.cell(105, 6, "PDF listo para radicar manualmente + instrucciones", border=1, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.cell(50, 6, "PDF + radicacion automatica", border=1, align="C")
    pdf.cell(25, 6, "$20.000 COP", border=1, align="C")
    pdf.cell(105, 6, "Radicacion completa + numero + constancia oficial", border=1, align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(5)
    pdf.set_font("Times", "", 10)
    pdf.seccion("Justificacion:")
    pdf.bullet("Gratis genera confianza: el usuario obtiene su tutela al instante y ve que funciona")
    pdf.bullet("Sin friccion de cobro: $20k es el precio de un domicilio en Colombia, facil de decidir")
    pdf.bullet("Sin costo operativo si no paga: si no radica, no gastamos recursos (IA + Playwright)")

    pdf.seccion("Pasarela de pago:")
    pdf.parrafo(
        "Wompi (plataforma colombiana de pagos) integrada via API. Acepta Nequi, "
        "tarjetas de credito/debito, y PSE. Flujo: bot envia link de pago por "
        "WhatsApp, usuario paga, webhook de Wompi confirma, bot procede a radicar."
    )

    # --- 15. PROYECCIONES ---
    pdf.add_page()
    pdf.titulo("15. PROYECCIONES FINANCIERAS")
    pdf.seccion("Escenario base (conservador):")
    pdf.par("Tutelas por dia", "10")
    pdf.par("Tasa de conversion a pago", "30% (3 de cada 10 usuarios pagan)")
    pdf.par("Ingreso por tutela paga", "$20.000 COP")
    pdf.par("Ingreso diario", "$60.000 COP (3 x $20k)")
    pdf.par("Ingreso mensual", "$1.800.000 COP (30 dias)")
    pdf.par("Ingreso anual", "$21.600.000 COP")

    pdf.seccion("Escenario medio:")
    pdf.par("Tutelas por dia", "20")
    pdf.par("Tasa de conversion", "35%")
    pdf.par("Ingreso mensual", "$4.200.000 COP")
    pdf.par("Ingreso anual", "$50.400.000 COP")

    pdf.seccion("Escenario optimista:")
    pdf.par("Tutelas por dia", "50")
    pdf.par("Tasa de conversion", "40%")
    pdf.par("Ingreso mensual", "$12.000.000 COP")
    pdf.par("Ingreso anual", "$144.000.000 COP")

    pdf.ln(5)
    pdf.set_font("Times", "I", 10)
    pdf.parrafo(
        "Nota: Los costos operativos incluyen API de Groq (~$0.10 por tutela), "
        "2Captcha (~$0.001 por resolucion), hosting Railway (~$5/mes), y "
        "comision de Wompi (~2.5% + $300 COP por transaccion). Rentabilidad estimada: "
        ">90% margen en escenario base."
    )

    # --- Guardar PDF ---
    pdf.output(ruta)
    print(f"Informe generado: {ruta}")
    return ruta


if __name__ == "__main__":
    generar_informe()