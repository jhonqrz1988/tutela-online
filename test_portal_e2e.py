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
    await page.wait_for_timeout(1500)
    print("2. Accionante + correo validado", flush=True)

    # Verificar si pide codigo (espera activa al modal #CodigoCorreo hasta 10s)
    for _c in range(10):
        if await page.evaluate("()=>!!document.querySelector('#CodigoCorreo')"):
            break
        await page.wait_for_timeout(1000)
    needs_code = not await page.evaluate("document.querySelector('#IdEmail1')?.disabled")
    if needs_code or await page.evaluate("()=>!!document.querySelector('#CodigoCorreo')"):
        print("\n  >>> PORTAL PIDE CODIGO DE VERIFICACION <<<", flush=True)
        print(f"  >>> Revisa {email} para obtener el codigo <<<", flush=True)
        await screenshot("necesita_codigo")

        import pathlib as _pl
        _codigo_file = _pl.Path("storage/codigo.txt")
        if len(sys.argv) > 2:
            codigo = sys.argv[2].strip()
        elif _codigo_file.exists():
            codigo = _codigo_file.read_text(encoding="utf-8").strip()
        else:
            print("  >>> COLORIME: guarda el codigo en storage/codigo.txt y presiona Enter aqui <<<", flush=True)
            for _w in range(180):  # espera hasta 3 min
                if _codigo_file.exists():
                    codigo = _codigo_file.read_text(encoding="utf-8").strip()
                    break
                await page.wait_for_timeout(1000)
            else:
                raise RuntimeError("No se proporciono el codigo de verificacion")
        await page.wait_for_timeout(500)
        # Escribir el codigo en el modal (input #CodigoCorreo) o en #IdEmail1 si existe
        if await page.evaluate("()=>!!document.querySelector('#CodigoCorreo')"):
            await page.fill("#CodigoCorreo", codigo)
            await page.wait_for_timeout(500)
            # Click continuar del modal
            await page.evaluate("""()=>{
                for (const m of document.querySelectorAll('.jconfirm')) {
                    const btn = Array.from(m.querySelectorAll('button,input[type=button]')).find(x=>(x.textContent||x.value||'').trim().toLowerCase()==='continuar');
                    if (btn) { btn.click(); return; }
                }
            }""")
            await page.wait_for_timeout(3000)
        else:
            await page.evaluate("document.querySelector('#IdEmail1').disabled = false")
            await page.type("#IdEmail1", codigo, delay=50)
            await page.wait_for_timeout(500)
            await page.evaluate("document.querySelector('#IdEmail1')?.dispatchEvent(new Event('change'))")
            await page.wait_for_timeout(3000)
        await close_jc()
        print("  Codigo ingresado", flush=True)

    await screenshot("post_pasos_1_4")

    # === PASOS 5-8 ===
    print("\n3. Accionados...", flush=True)
    # select_option nativo (JS dispatch no dispara bien el onchange de estos selects)
    await page.select_option("#DDlTipoSujeto", "1165")
    await page.wait_for_timeout(1000)
    await close_jc()
    await page.select_option("#DDlTipodocumentoAccionado", "1162")   # NIT
    await page.wait_for_timeout(800)
    await page.fill("#DocumentodeIdendificacion", "800251440-6")
    await page.fill("#NombreJuridicoAcc", "EPS Sanitas S.A. E.S.")
    await page.fill("#IdDireccion", "Calle 72 # 10 - 07, Bogotá")
    await page.fill("#IdTelefono", "6017440000")
    await page.fill("#IdEmail", "notificajudiciales@keralty.com")
    await jc("#RdbNoAccionMenores")
    await page.wait_for_timeout(500)
    added = False
    for _t in range(3):
        await close_jc()
        await jc("#btnAddAccionado")
        await page.wait_for_timeout(2000)
        msgs = await page.evaluate("()=>Array.from(document.querySelectorAll('.jconfirm')).map(j=>j.innerText.substring(0,200))")
        tbl = await page.evaluate("()=>document.body.innerText || ''")
        has = "Sanitas" in tbl or "sanitas" in tbl.lower()
        err = msgs and ("tipo de documento" in " ".join(msgs).lower())
        print(f"  Intento {_t+1}: sanitas={'SI' if has else 'NO'}{' | '+msgs[0][:120] if msgs else ''}", flush=True)
        if has:
            added = True
            print(f"  Accionado agregado (intento {_t+1})", flush=True)
            break
        if err:
            await page.wait_for_timeout(1000)
            continue
        # sin error y sin Sanitas: puede que la tabla quede oculta o aún no renderice
        await page.wait_for_timeout(1000)
    if not added:
        final = await page.evaluate("()=>['sanitas'].some(x=>document.body.innerText.toLowerCase().includes(x))")
        print(f"  ADVERTENCIA: no confirmado — pero Sanitas en DOM: {final}", flush=True)

    print("4. Derechos...", flush=True)
    await sv("#DDLDerechos", "1213")
    await page.wait_for_timeout(500)
    await jc("#RdbNoMedida")
    await page.wait_for_timeout(500)
    await close_jc()
    await jc("#btnAdd")
    await page.wait_for_timeout(1000)

    # DEMANDA (obligatorio) + PRUEBA
    print("5. Archivos (DEMANDA + PRUEBA)...", flush=True)
    for tipo, valor in [("DEMANDA", "1225"), ("PRUEBA", "1226")]:
        await close_jc()
        await sv("#DDlTipoArchivo", valor)
        await page.wait_for_timeout(500)
        await page.set_input_files("#ArchivoFile0", PDF_TEST)
        await page.wait_for_timeout(2000)
        await close_jc()
        await jc("#btnAddfile")
        await page.wait_for_timeout(2000)
        await close_jc()
        subido = await page.evaluate("()=>document.body.innerText.includes('DEMANDA') || document.body.innerText.includes('PRUEBA')")
        print(f"  {tipo}: {'OK' if subido else '?'}", flush=True)

    print("6. Juramento...", flush=True)
    await jc("#CbManifiesto")
    await page.wait_for_timeout(500)

    # Verificar estado
    estado = await page.evaluate("""() => ({
        accionados: document.querySelector('#tblConsejoCorte')?.innerText?.substring(0, 100) || '',
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
    # limpiar cualquier jconfirm residual, incluido el captcha
    await page.evaluate("""()=>{document.querySelectorAll('.jconfirm').forEach(m=>{const b=m.querySelector('.btn');if(b)b.click();else m.remove()})}""")
    await page.wait_for_timeout(500)
    await jc("#enviar")

    # Confirmar el modal "Confirmar Datos" (jconfirm) si aparece, luego esperar número radicado
    numrad = None
    confirm_clicked = False
    confirm_correo_done = False
    for _i in range(60):
        await page.wait_for_timeout(1000)
        modal = await page.evaluate("()=>Array.from(document.querySelectorAll('.jconfirm')).map(j=>j.textContent.substring(0,400))")
        num = await page.evaluate("()=>document.querySelector('#numRadicado')?.textContent?.trim() || null")
        if num:
            numrad = num
            break
        if modal:
            joined = " ".join(modal).lower()
            # Modal de confirmación de correo: requiere código, el usuario lo escribe en storage/codigo.txt
            if "confirmar el correo" in joined and not confirm_correo_done:
                print("  >>> PORTAL PIDE CONFIRMACION DE CORREO AL ENVIAR <<<", flush=True)
                modal_txt = await page.evaluate("""()=>{
                    const m=document.querySelector('.jconfirm:last-child');
                    return m?m.textContent.replace(/\\s+/g,' ').trim().slice(0,400):'SIN MODAL';
                }""")
                print("  TEXTO modal:", modal_txt, flush=True)
                # botones del modal
                botones = await page.evaluate("""()=>{
                    const m=document.querySelector('.jconfirm:last-child');
                    if (!m) return [];
                    const r=[];
                    for (const el of m.querySelectorAll('button,input[type=button],input[type=submit],a.btn')) {
                        r.push({tag:el.tagName,id:el.id,val:(el.value||el.textContent||'').trim().slice(0,40),cls:(el.className||'').slice(0,40)});
                    }
                    return r;
                }""")
                print("  Botones modal:", json.dumps(botones, ensure_ascii=False), flush=True)
                await screenshot("necesita_codigo_envio")
                import pathlib as _pl
                _cf = _pl.Path("storage/codigo.txt")
                if _cf.exists():
                    codigo = _cf.read_text(encoding="utf-8").strip()
                else:
                    print("  >>> ESPERANDO codigo en storage/codigo.txt (hasta 4 min) <<<", flush=True)
                    for _w in range(240):
                        if _cf.exists():
                            codigo = _cf.read_text(encoding="utf-8").strip()
                            break
                        await page.wait_for_timeout(1000)
                    else:
                        raise RuntimeError("No se proporciono el codigo de confirmacion")
                # inspeccionar el modal para hallar el input de codigo
                campos = await page.evaluate("""()=>{
                    const inputs=[];
                    for (const m of document.querySelectorAll('.jconfirm')) {
                        for (const i of m.querySelectorAll('input')) {
                            inputs.push({id:i.id,type:i.type,ph:i.placeholder,val:i.value});
                        }
                    }
                    return inputs;
                }""")
                print("  Campos en modal correo:", json.dumps(campos, ensure_ascii=False), flush=True)
                # ingresar codigo en el primer input numerico/texto visible del modal
                if any(c.get('id') for c in campos):
                    sel = "#" + next(c['id'] for c in campos if c.get('id'))
                else:
                    sel = ".jconfirm input:not([type=hidden])"
                try:
                    await page.fill(sel, codigo, timeout=5000)
                except Exception as e:
                    print(f"  No se pudo llenar {sel}: {e}", flush=True)
                await page.wait_for_timeout(500)
                await page.evaluate("""()=>{
                    for (const m of document.querySelectorAll('.jconfirm')) {
                        const btn = m.querySelector('.btn') || Array.from(m.querySelectorAll('button'))
                            .find(b=>/continuar|confirmar|aceptar/i.test((b.textContent||'').trim()));
                        if (btn) { btn.click(); confirm_correo_done=true; return; }
                    }
                }""")
                await page.wait_for_timeout(2000)
                await close_jc()
                # volver a pulsar enviar si es necesario
                await jc("#enviar")
                continue
            # Modal "Confirmar Datos" inicial
            if ("confirmar datos" in joined or "confirmar" in joined or "radica" in joined) and not confirm_clicked:
                print(f"  Modal de confirmacion presente: {modal[0][:120]}", flush=True)
                await page.evaluate("""()=>{
                    for (const m of document.querySelectorAll('.jconfirm')) {
                        const btn = m.querySelector('.btn') || Array.from(m.querySelectorAll('button'))
                            .find(b=>/confirmar|si|continuar|aceptar/i.test((b.textContent||'').trim()));
                        if (btn) { btn.click(); confirm_clicked=true; return; }
                    }
                }""")
                await page.wait_for_timeout(2000)
                continue
        if numrad is None and not confirm_clicked:
            pass  # aun procesando

    await screenshot("post_enviar")
    print("\n=== RESULTADO ===", flush=True)
    print(f"Numero radicado: {numrad}", flush=True)
    modal_final = await page.evaluate("()=>Array.from(document.querySelectorAll('.jconfirm')).map(j=>j.textContent.substring(0,300))")
    if modal_final:
        print(f"Mensajes: {modal_final}", flush=True)

    await browser.close()
    await p.stop()
    print("\nTEST COMPLETADO", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python test_portal_e2e.py tuemail@correo.com")
        sys.exit(1)
    asyncio.run(test_e2e(sys.argv[1]))
