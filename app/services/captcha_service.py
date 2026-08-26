import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

RECAPTCHA_SITEKEY = "6LcnkeUUAAAAAIzytmwnkjif8k066vQVR6EKXFw0"
TWO_CAPTCHA_API = "https://2captcha.com"


async def resolver_recaptcha_v2(page_url: str, max_wait: int = 120) -> str | None:
    """Resuelve un reCAPTCHA v2 usando el servicio 2Captcha.

    Retorna el token de respuesta o None si falla.
    """
    api_key = settings.twocaptcha_api_key
    if not api_key:
        logger.warning("twocaptcha_api_key no configurada; no se puede resolver reCAPTCHA")
        return None

    # Paso 1: Enviar tarea a 2Captcha
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            submit_resp = await client.post(
                f"{TWO_CAPTCHA_API}/in.php",
                data={
                    "key": api_key,
                    "method": "userrecaptcha",
                    "googlekey": RECAPTCHA_SITEKEY,
                    "pageurl": page_url,
                    "json": 1,
                },
            )
            submit_data = submit_resp.json()
    except Exception as e:
        logger.error(f"Error enviando reCAPTCHA a 2Captcha: {e}")
        return None

    if submit_data.get("status") != 1:
        logger.error(f"2Captcha rechazó la tarea: {submit_data.get('request')}")
        return None

    task_id = submit_data["request"]
    logger.info(f"2Captcha tarea creada: {task_id}")

    # Paso 2: Esperar resultado (polling)
    start = time.time()
    while time.time() - start < max_wait:
        await _asyncio_sleep(5)

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                result_resp = await client.get(
                    f"{TWO_CAPTCHA_API}/res.php",
                    params={"key": api_key, "action": "get", "id": task_id, "json": 1},
                )
                result_data = result_resp.json()
        except Exception as e:
            logger.error(f"Error consultando resultado 2Captcha: {e}")
            continue

        if result_data.get("status") == 1:
            token = result_data["request"]
            logger.info(f"2Captcha resolvió reCAPTCHA (token len={len(token)})")
            return token

        # status 0 = aún procesando
        if result_data.get("request") != "CAPCHA_NOT_READY":
            logger.error(f"2Captcha error: {result_data.get('request')}")
            return None

    logger.error(f"2Captcha timeout después de {max_wait}s")
    return None


async def _asyncio_sleep(seconds: float):
    """Wrapper para asyncio.sleep sin importar asyncio arriba."""
    import asyncio
    await asyncio.sleep(seconds)
