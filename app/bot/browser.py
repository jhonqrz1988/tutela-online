from playwright.async_api import Browser, Page, async_playwright

from app.config import settings


class BrowserManager:
    _instance: Browser | None = None

    @classmethod
    async def get_browser(cls) -> Browser:
        if cls._instance and cls._instance.is_connected():
            return cls._instance
        p = await async_playwright().start()
        cls._instance = await p.chromium.launch(
            headless=settings.browser_headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        return cls._instance

    @classmethod
    async def new_page(cls) -> Page:
        browser = await cls.get_browser()
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="es-CO",
            timezone_id="America/Bogota",
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)
        return await context.new_page()

    @classmethod
    async def close(cls):
        if cls._instance:
            await cls._instance.close()
            cls._instance = None
