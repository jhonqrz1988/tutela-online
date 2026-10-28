from pydantic_settings import BaseSettings
from secrets import token_hex


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

    # === TikTok (pixel + Events API 2.0) ===
    tiktok_pixel_id: str = ""
    tiktok_access_token: str = ""
    # Código de test temporal de TikTok (expira ~24h) para validar los eventos
    # en modo "Test Events" antes de producción. Vacío = tráfico real.
    tiktok_test_event_code: str = ""
    # Token compartido para proteger los webhooks legacy (/webhook/whatsapp,
    # /webhook/zapi). Si se configura, estos endpoints exigen
    # "Authorization: Bearer <token>".
    webhook_auth_token: str = ""
    # Temporal mientras se prueba sin META_APP_SECRET: si True exige y valida
    # la firma HMAC del webhook (rechaza si no hay secret); si False la omite.
    strict_webhook_firma: bool = False

    # === AI Provider: "openai", "groq", o "gemini" ===
    ai_provider: str = "openai"
    ai_api_key: str = ""
    ai_chat_model: str = "gpt-4o-mini"
    ai_whisper_model: str = "whisper-1"

    secret_key: str = "dev-key-change-in-production"
    admin_password: str = ""  # si queda vacío, el panel admin exige configurarlo
    database_url: str = "sqlite:///./storage/tutelas.db"
    storage_dir: str = "storage"
    host: str = "0.0.0.0"
    port: int = 8000
    app_url: str = "http://localhost:8000"
    # Orígenes CORS adicionales separados por coma (además de app_url).
    cors_origins: str = ""

    # === Mercado Pago (pasarela de pagos Colombia) ===
    mercadopago_access_token: str = ""
    mercadopago_webhook_secret: str = ""
    mercadopago_env: str = "sandbox"  # "sandbox" | "production"
    mercadopago_amount: float = 29000.0  # $29.000 COP
    mercadopago_currency: str = "COP"

    rama_judicial_url: str = "https://procesojudicial.ramajudicial.gov.co/TutelaEnLinea"
    browser_headless: bool = True

    filing_hour: int = 8
    filing_minute: int = 5
    simulate_bot: bool = True
    enable_scheduler: bool = False  # radicación automática nocturna (bot) — desactivada

    # === Retención de datos de tráfico (visitas/clics de la landing) ===
    # Días que se conservan las filas de visitas_landing y clics_whatsapp.
    # 0 = desactivado (no borra nada). En Supabase Free el techo es 500 MB por
    # proyecto: la purga evita que la tabla de visitas lo alcance algún día.
    retencion_visitas_dias: int = 90
    retencion_clics_dias: int = 90
    # Borrado por lotes: un DELETE gigante bloquea la tabla en Postgres (la
    # instancia Micro de Supabase) y puede tumbar el health check.
    retencion_lote: int = 5000
    retencion_max_por_corrida: int = 50000
    # Job diario de purga. Independiente del switch de radicación: si el admin
    # pausa el bot, el mantenimiento de la BD debe seguir corriendo.
    enable_retencion: bool = False

    twocaptcha_api_key: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


_settings = Settings()


# Si SECRET_KEY quedó vacía o con el default de desarrollo, genera una
# aleatoria en cada arranque: la cookie del admin solo es válida mientras
# el proceso viva (se invalida al reiniciar), evitando que un tercero
# pueda forjar una sesión con la clave por defecto.
if not _settings.secret_key or _settings.secret_key == "dev-key-change-in-production":
    _settings.secret_key = token_hex(32)

settings = _settings
