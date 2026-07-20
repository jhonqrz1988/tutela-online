import asyncio
from playwright.async_api import async_playwright


async def check():
    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=True)
    page = await browser.new_page()
    try:
        await page.goto(
            "https://procesojudicial.ramajudicial.gov.co/TutelaEnLinea",
            wait_until="networkidle",
            timeout=30000,
        )
        inputs = await page.evaluate("""() => {
            const els = document.querySelectorAll('input, select, textarea, button');
            return Array.from(els).map(e => ({
                tag: e.tagName,
                id: e.id,
                name: e.name,
                type: e.type || e.tagName,
                placeholder: e.placeholder || '',
                label: (e.labels && e.labels[0] ? e.labels[0].textContent : '') || '',
                class: e.className
            }));
        }""")
        print(f"Form elements found: {len(inputs)}")
        for inp in inputs:
            print(f'  {inp["tag"]:8s} id="{inp["id"]}" name="{inp["name"]}" type="{inp["type"]}" label="{inp["label"][:50]}"')

        # Also check for recaptcha
        recaptcha = await page.query_selector(".g-recaptcha")
        print(f"\nreCAPTCHA found: {recaptcha is not None}")

        # check step info
        step = await page.evaluate("""() => {
            const steps = document.querySelectorAll('.step, .wizard-step, [class*="step"]');
            return Array.from(steps).map(s => s.textContent.trim());
        }""")
        print(f"Steps: {step}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        await browser.close()
        await p.stop()


asyncio.run(check())
