import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

META_API_BASE = "https://graph.facebook.com/v25.0/{phone_number_id}/messages"
ZAPI_BASE = "https://api.z-api.io/instances/{instance}/token/{token}"


def _zapi_url(endpoint: str) -> str | None:
    if not settings.zapi_instance or not settings.zapi_token:
        return None
    url = f"{ZAPI_BASE}/{endpoint}"
    return url.replace("{instance}", settings.zapi_instance).replace("{token}", settings.zapi_token)


def _meta_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.meta_access_token}",
        "Content-Type": "application/json",
    }


def _infobip_headers() -> dict:
    return {
        "Authorization": f"App {settings.infobip_api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _infobip_url(endpoint: str) -> str:
    return f"https://{settings.infobip_base_url}/whatsapp/1/message/{endpoint}"


def enviar_texto(telefono: str, mensaje: str) -> bool:
    telefono_limpio = telefono.replace("whatsapp:", "").replace("+", "").strip()

    provider = settings.whatsapp_provider
    if provider == "meta":
        return _enviar_meta_texto(telefono_limpio, mensaje)
    elif provider == "zapi":
        return _enviar_zapi(telefono_limpio, mensaje)
    elif provider == "twilio":
        return _enviar_twilio(telefono, mensaje)
    elif provider == "infobip":
        return _enviar_infobip_texto(telefono_limpio, mensaje)
    return True


def enviar_botones(telefono: str, texto: str, botones: list[tuple[str, str]]) -> bool:
    """Envía botones interactivos (Meta Cloud API). botones = [(id, titulo), ...] max 3."""
    telefono_limpio = telefono.replace("whatsapp:", "").replace("+", "").strip()
    provider = settings.whatsapp_provider
    if provider != "meta":
        return enviar_texto(telefono, texto)
    try:
        url = META_API_BASE.replace("{phone_number_id}", settings.meta_phone_number_id)
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono_limpio,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": texto[:1024]},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": bid, "title": btitle[:20]}}
                        for bid, btitle in botones[:3]
                    ]
                },
            },
        }
        r = httpx.post(url, json=payload, headers=_meta_headers(), timeout=15)
        return r.is_success
    except Exception as e:
        logger.error(f"Error enviar_botones: {e}")
        return False


def _enviar_meta_texto(telefono: str, mensaje: str) -> bool:
    try:
        url = META_API_BASE.replace("{phone_number_id}", settings.meta_phone_number_id)
        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "text",
            "text": {"body": mensaje},
        }
        r = httpx.post(url, json=payload, headers=_meta_headers(), timeout=15)
        return r.is_success
    except Exception as e:
        logger.error(f"Error _enviar_meta_texto: {e}")
        return False


def _enviar_zapi(telefono: str, mensaje: str) -> bool:
    try:
        url = _zapi_url("send-text")
        if not url:
            return False
        httpx.post(url, json={"phone": telefono, "message": mensaje}, timeout=15)
        return True
    except Exception as e:
        logger.error(f"Error _enviar_zapi: {e}")
        return False


def _enviar_twilio(telefono: str, mensaje: str) -> bool:
    if not settings.twilio_account_sid:
        return False
    try:
        auth = httpx.BasicAuth(settings.twilio_account_sid, settings.twilio_auth_token)
        r = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
            data={"Body": mensaje, "From": _from_whatsapp(), "To": _to_whatsapp(telefono)},
            auth=auth,
            timeout=15,
        )
        return r.is_success
    except Exception as e:
        logger.error(f"Error _enviar_twilio: {e}")
        return False


def _enviar_infobip_texto(telefono: str, mensaje: str) -> bool:
    if not settings.infobip_api_key:
        return False
    try:
        payload = {
            "messages": [{
                "from": settings.infobip_sender,
                "to": telefono,
                "content": {
                    "text": mensaje,
                },
            }]
        }
        r = httpx.post(
            _infobip_url("text"),
            json=payload,
            headers=_infobip_headers(),
            timeout=15,
        )
        return r.is_success
    except Exception as e:
        logger.error(f"Error _enviar_infobip_texto: {e}")
        return False


def _enviar_infobip_documento(telefono: str, ruta_pdf: str, filename: str) -> bool:
    if not settings.infobip_api_key:
        return False
    try:
        import re
        with open(ruta_pdf, "rb") as f:
            r_up = httpx.post("https://tmpfiles.org/api/v1/upload", files={"file": (filename, f, "application/pdf")}, timeout=20)
        if r_up.status_code != 200:
            return False
        tmp_url = r_up.json().get("data", {}).get("url", "")
        if not tmp_url:
            return False
        r_dl = httpx.get(tmp_url.replace("tmpfiles.org/", "tmpfiles.org/dl/"), follow_redirects=True, timeout=10)
        match = re.search(r'href=["\'](https://tmpfiles\.org/dl/[^"\']+\.pdf)["\']', r_dl.text)
        if not match:
            return False
        pdf_url = match.group(1)

        payload = {
            "messages": [{
                "from": settings.infobip_sender,
                "to": telefono,
                "content": {
                    "document": {
                        "url": pdf_url,
                        "filename": filename,
                    },
                },
            }]
        }
        r = httpx.post(
            _infobip_url("document"),
            json=payload,
            headers=_infobip_headers(),
            timeout=30,
        )
        return r.is_success
    except Exception as e:
        logger.error(f"Error _enviar_infobip_documento: {e}")
        return False


def enviar_documento(telefono: str, ruta_pdf: str, filename: str = "tutela.pdf") -> bool:
    telefono_limpio = telefono.replace("whatsapp:", "").replace("+", "").strip()

    provider = settings.whatsapp_provider
    if provider == "meta":
        return _enviar_documento_meta(telefono_limpio, ruta_pdf, filename)
    elif provider == "zapi":
        return _enviar_documento_zapi(telefono_limpio, ruta_pdf, filename)
    elif provider == "twilio":
        return _enviar_documento_twilio(telefono, ruta_pdf, filename)
    elif provider == "infobip":
        return _enviar_infobip_documento(telefono_limpio, ruta_pdf, filename)
    return False


def _enviar_documento_meta(telefono: str, ruta_pdf: str, filename: str) -> bool:
    try:
        url = META_API_BASE.replace("{phone_number_id}", settings.meta_phone_number_id)

        # Subir PDF a tmpfiles para tener URL pública
        with open(ruta_pdf, "rb") as f:
            r_up = httpx.post(
                "https://tmpfiles.org/api/v1/upload",
                files={"file": (filename, f, "application/pdf")},
                timeout=20,
            )
        if r_up.status_code != 200:
            return False
        doc_url = r_up.json().get("data", {}).get("url", "")
        if not doc_url:
            return False

        payload = {
            "messaging_product": "whatsapp",
            "to": telefono,
            "type": "document",
            "document": {
                "link": doc_url,
                "filename": filename,
                "caption": "📄 Tutela generada",
            },
        }
        r = httpx.post(url, json=payload, headers=_meta_headers(), timeout=30)
        return r.is_success
    except Exception as e:
        logger.error(f"Error _enviar_documento_meta: {e}")
        return False


def _enviar_documento_zapi(telefono: str, ruta_pdf: str, filename: str) -> bool:
    try:
        ext = filename.rsplit(".", 1)[-1] if "." in filename else "pdf"
        api_url = _zapi_url(f"send-document/{ext}")
        if not api_url:
            return False

        # Subir PDF a tmpfiles
        import re
        with open(ruta_pdf, "rb") as f:
            r_up = httpx.post("https://tmpfiles.org/api/v1/upload", files={"file": (filename, f, "application/pdf")}, timeout=20)
        if r_up.status_code != 200:
            return False
        doc_url = r_up.json().get("data", {}).get("url", "")
        if not doc_url:
            return False

        # Extraer link real de descarga del HTML
        r_dl = httpx.get(doc_url.replace("tmpfiles.org/", "tmpfiles.org/dl/"), follow_redirects=True, timeout=10)
        match = re.search(r'href=["\'](https://tmpfiles\.org/dl/[^"\']+\.pdf)["\']', r_dl.text)
        if not match:
            return False
        real_url = match.group(1)

        payload = {
            "phone": telefono,
            "document": real_url,
            "fileName": filename,
            "caption": "📄 Tutela generada",
        }
        r = httpx.post(api_url, json=payload, timeout=30)
        return r.is_success
    except Exception as e:
        logger.error(f"Error _enviar_documento_zapi: {e}")
        return False


def _from_whatsapp() -> str:
    return f"whatsapp:{settings.twilio_whatsapp_number}"


def _to_whatsapp(telefono: str) -> str:
    if telefono.startswith("whatsapp:"):
        return telefono
    if telefono.startswith("+"):
        return f"whatsapp:{telefono}"
    return f"whatsapp:+57{telefono}"


def _enviar_documento_twilio(telefono: str, ruta_pdf: str, filename: str) -> bool:
    if not settings.twilio_account_sid:
        return False
    try:
        auth = httpx.BasicAuth(settings.twilio_account_sid, settings.twilio_auth_token)

        # Subir PDF a tmpfiles.org y obtener URL real de descarga
        import re
        with open(ruta_pdf, "rb") as f:
            r_up = httpx.post(
                "https://tmpfiles.org/api/v1/upload",
                files={"file": (filename, f, "application/pdf")},
                timeout=20,
            )
        if r_up.status_code != 200:
            return False
        tmp_url = r_up.json().get("data", {}).get("url", "")
        if not tmp_url:
            return False

        # Extraer link real de descarga del HTML
        r_dl = httpx.get(tmp_url.replace("tmpfiles.org/", "tmpfiles.org/dl/"), follow_redirects=True, timeout=10)
        match = re.search(r'href=["\'](https://tmpfiles\.org/dl/[^"\']+\.pdf)["\']', r_dl.text)
        if not match:
            return False
        pdf_url = match.group(1)

        r = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
            data={
                "Body": "📄 Tutela generada",
                "From": _from_whatsapp(),
                "To": _to_whatsapp(telefono),
                "MediaUrl": pdf_url,
            },
            auth=auth,
            timeout=30,
        )
        return r.is_success
    except Exception as e:
        logger.error(f"Error _enviar_documento_twilio: {e}")
        return False
