from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_number: str = "+14155238886"

    whatsapp_provider: str = "simular"
    zapi_instance: str = ""
    zapi_token: str = ""
    infobip_api_key: str = ""
    infobip_base_url: str = ""
    infobip_sender: str = "447860088970"

    meta_access_token: str = ""
    meta_phone_number_id: str = ""
    meta_verify_token: str = ""
    meta_app_secret: str = ""

    # === AI Provider: "openai", "groq", o "gemini" ===
    ai_provider: str = "openai"
    ai_api_key: str = ""
    ai_chat_model: str = "gpt-4o-mini"
    ai_whisper_model: str = "whisper-1"

    secret_key: str = "dev-key-change-in-production"
    admin_password: str = ""  # si queda vacío, el panel admin exige configurarlo
    database_url: str = "sqlite:///./storage/tutelas.db"
    host: str = "0.0.0.0"
    port: int = 8000
    app_url: str = "http://localhost:8000"

    # === Wompi (pasarela de pagos Colombia) ===
    wompi_public_key: str = ""
    wompi_private_key: str = ""
    wompi_integrity_secret: str = ""
    wompi_events_secret: str = ""
    wompi_env: str = "sandbox"  # "sandbox" | "production"
    wompi_amount_cents: int = 2000000  # $20.000 COP = 2.000.000 centavos
    wompi_currency: str = "COP"

    rama_judicial_url: str = "https://procesojudicial.ramajudicial.gov.co/TutelaEnLinea"
    browser_headless: bool = True

    filing_hour: int = 8
    filing_minute: int = 5
    simulate_bot: bool = True

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
