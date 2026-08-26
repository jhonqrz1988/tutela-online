"""
Test E2E del portal de Rama Judicial.
Ejecutar en horario habil (8am-12pm, 2pm-4pm lun-vie).

Uso:
    python test_portal_e2e.py tuemail@correo.com
"""
import asyncio
import json
import sys
from pathlib import Path
from playwright.async_api import async_playwright

PDF_TEST = str(Path("storage").resolve() / "test_prueba.pdf")

def generar_pdf_prueba():
    from fpdf import FPDF
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "PRUEBA DE LA ACCION DE TUTELA", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 10, "Documento de prueba para testing del portal", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 10, "Fecha: 2026-08-26", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 10, "Accionante: Carlos Andres Restrepo Martinez", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 10, "Cedula: 1032456789", new_x="LMARGIN", new_y="NEXT")
    pdf.output(PDF_TEST)
    print(f"PDF generado: {PDF_TEST}", flush=True)


async def test_e2e(email: str):
    generar_pdf_prueba()

    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=True, args=[
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
    ])
    ctx = await browser.new_context(viewport={"width": 1366, "height": 768}, locale="es-CO")
    page = await ctx.new_page()

    SS = "storage/screenshots"

    async def sv(sel, val):
        await page.evaluate(
            "([s,v])=>{const el=document.querySelector(s);if(el){el.value=v;el.dispatchEvent(new Event('change',{bubbles:true}))}}",
            [sel, val],
        )
        await page.wait_for_timeout(1500)

    async def jc(sel):
        await page.evaluate(f"document.querySelector('{sel}')?.click()")
        await page.wait_for_timeout(500)

    async def close_jc():
        await page.evaluate(
            "document.querySelectorAll('.jconfirm').forEach(m=>{const b=m.querySelector('.btn');if(b)b.click();else m.remove()})"
        )
        await page.wait_for_timeout(500)

    async def screenshot(name):
        await page.screenshot(path=f"{SS}/{name}.png", full_page=True)
        print(f"  Screenshot: {SS}/{name}.png", flush=True)

    # === PORTAL ===
    print("Navegando al portal...", flush=True)
    await page.goto("https://procesojudicial.ramajudicial.gov.co/TutelaEnLinea", wait_until="networkidle")
    await page.wait_for_timeout(3000)

    # Modal
    cb = await page.query_selector("#enableCheckbox")
    if cb:
        await cb.click()
        await page.wait_for_timeout(500)
        for b in await page.query_selector_all("button"):
            t = await b.text_content()
            if t and "Continuar" in t:
                await b.click()
                break
    await page.wait_for_timeout(3000)
    print("1. Modal OK", flush=True)

    # === PASOS 1-4 ===
    # Depto envio
    await sv("#DdlDepartamento", "5")
    await page.wait_for_timeout(2000)
    cv = await page.evaluate("()=>{for(const o of document.querySelector('#DDlCiudad').options){if(o.text.includes('BOGOTA'))return o.value}return null}")
    if cv:
        await sv("#DDlCiudad", cv)

    # Tutela
    await jc("#RdbTutela")

    # Hechos
    await sv("#DdlDepartamentoHechos", "5")
    await page.wait_for_timeout(2000)
    ch = await page.evaluate("()=>{for(const o of document.querySelector('#DDlCiudadHechos').options){if(o.text.includes('BOGOTA'))return o.value}return null}")
    if ch:
        await sv("#DDlCiudadHechos", ch)

    # Accionante
    await sv("#DDlTipodocumento", "1161")
    await page.evaluate("document.querySelector('#NumeroDocumento').value='1032456789'")
    await page.type("#PrimerNombre", "Carlos", delay=50)
    await page.type("#SegundoNombre", "Andres", delay=50)
    await page.type("#PrimerApellido", "Restrepo", delay=50)
    await page.type("#SegundoApellido", "Martinez", delay=50)
    await page.type("#Telefono", "3001234567", delay=50)
    await sv("#DDlTipodiscapacidad", "1263")
    await page.type("#Email", email, delay=50)
    await page.wait_for_timeout(500)

    # Validar correo
    await close_jc()
    await jc("#btnValidar")
    await page.wait_for_timeout(3000)
    await close_jc()
    print("2. Accionante + correo validado", flush=True)

    # Verificar si pide codigo
    needs_code = not await page.evaluate("document.querySelector('#IdEmail1')?.disabled")
    if needs_code:
        print("\n  >>> PORTAL PIDE CODIGO DE VERIFICACION <<<", flush=True)
        print(f"  >>> Revisa {email} para obtener el codigo <<<", flush=True)
        await screenshot("necesita_codigo")

        codigo = await asyncio.to_thread(
            lambda: input("  Escribe el codigo de verificacion: ").strip()
        )

        # Ingresar codigo
        await page.evaluate("document.querySelector('#IdEmail1').disabled = false")
        await page.type("#IdEmail1", codigo, delay=50)
        await page.wait_for_timeout(500)

        # Buscar boton de verificar codigo
        verify_btn = await page.evaluate("""() => {
            const btns = document.querySelectorAll('input[type=button], button');
            for (const b of btns) {
                const txt = (b.value || b.textContent || '').toLowerCase();
                if (txt.includes('verificar') || txt.includes('validar codigo') || txt.includes('confirmar')) {
                    return b.id || b.outerHTML.substring(0, 100);
                }
            }
            return null;
        }""")
        print(f"  Boton verificar: {verify_btn}", flush=True)

        if verify_btn and verify_btn.startswith('#'):
            await jc(verify_btn)
        else:
            # Intentar click directo por JS
            await page.evaluate("document.querySelector('#IdEmail1')?.dispatchEvent(new Event('change'))")
        await page.wait_for_timeout(3000)
        await close_jc()
        print("  Codigo ingresado", flush=True)

    await screenshot("post_pasos_1_4")

    # === PASOS 5-8 ===
    print("\n3. Accionados...", flush=True)
    await sv("#DDlTipoSujeto", "1165")
    await page.wait_for_timeout(1000)
    await page.type("#NombreJuridicoAcc", "EPS Sanitas", delay=50)
    await page.wait_for_timeout(500)
    await close_jc()
    await jc("#btnAddAccionado")
    await page.wait_for_timeout(2000)

    print("4. Derechos...", flush=True)
    await sv("#DDLDerechos", "1213")
    await page.wait_for_timeout(500)
    await jc("#RdbNoMedida")
    await page.wait_for_timeout(500)
    await close_jc()
    await jc("#btnAdd")
    await page.wait_for_timeout(1000)

    print("5. Archivo PRUEBA...", flush=True)
    await sv("#DDlTipoArchivo", "1226")
    await page.wait_for_timeout(500)
    await page.set_input_files("#ArchivoFile0", PDF_TEST)
    await page.wait_for_timeout(2000)
    await close_jc()
    await jc("#btnAddfile")
    await page.wait_for_timeout(2000)
    await close_jc()

    print("6. Juramento...", flush=True)
    await jc("#CbManifiesto")
    await page.wait_for_timeout(500)

    # Verificar estado
    estado = await page.evaluate("""() => ({
        accionados: document.querySelector('#tblAccionados')?.innerText?.substring(0, 100) || '',
        derechos: document.querySelector('#tblDerechos')?.innerText?.substring(0, 100) || '',
        archivos: document.querySelector('#tblArchivos')?.innerText?.substring(0, 100) || '',
        juramento: document.querySelector('#CbManifiesto')?.checked || false,
    })""")
    print(f"  Estado: {json.dumps(estado, ensure_ascii=False)}", flush=True)
    await screenshot("pre_recaptcha")

    # === reCAPTCHA ===
    print("\n7. reCAPTCHA...", flush=True)
    from app.services.captcha_service import resolver_recaptcha_v2

    sitekey = await page.evaluate("()=>{const el=document.querySelector('.g-recaptcha');return el?el.getAttribute('data-sitekey'):null}")
    if sitekey:
        token = await resolver_recaptcha_v2(page.url)
        if token:
            await page.evaluate("""([t])=>{
                document.getElementById('g-recaptcha-response').value=t;
                if(typeof ___grecaptcha_cfg!=='undefined'){
                    for(var k in ___grecaptcha_cfg.clients){
                        var c=___grecaptcha_cfg.clients[k];
                        if(c&&c.T)c.T(t);
                    }
                }
            }""", [token])
            print("  Token reCAPTCHA insertado", flush=True)
        else:
            print("  ERROR: No se pudo resolver reCAPTCHA", flush=True)
    else:
        print("  No se encontro reCAPTCHA", flush=True)

    await screenshot("pre_enviar")

    # === ENVIAR ===
    print("\n8. Enviando...", flush=True)
    await close_jc()
    await jc("#enviar")

    # Esperar respuesta
    for _i in range(30):
        await page.wait_for_timeout(1000)
        r = await page.evaluate("""() => ({
            numRad: document.querySelector('#numRadicado')?.textContent?.trim() || null,
            jc: Array.from(document.querySelectorAll('.jconfirm')).map(j=>j.textContent.substring(0,200)),
            url: window.location.href,
        })""")
        if r['numRad'] or r['jc']:
            break

    await screenshot("post_enviar")
    print("\n=== RESULTADO ===", flush=True)
    print(f"Numero radicado: {r['numRad']}", flush=True)
    if r['jc']:
        print(f"Mensajes: {r['jc']}", flush=True)

    await browser.close()
    await p.stop()
    print("\nTEST COMPLETADO", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python test_portal_e2e.py tuemail@correo.com")
        sys.exit(1)
    asyncio.run(test_e2e(sys.argv[1]))
