import httpx, os
os.environ["SECRET_KEY"] = "test"
from app.config import settings

instance = settings.zapi_instance
token = settings.zapi_token
webhook_url = "https://stoning-shimmer-cleaver.ngrok-free.dev/webhook/zapi"

base = f"https://api.z-api.io/instances/{instance}/token/{token}"

# Configure webhook for received messages
r = httpx.put(f"{base}/update-webhook-received", json={"value": webhook_url}, timeout=15)
print(f"Webhook received: {r.status_code} - {r.text[:200]}")

# Configure webhook for message status
r2 = httpx.put(f"{base}/update-webhook-message-status", json={"value": webhook_url}, timeout=15)
print(f"Webhook status: {r2.status_code} - {r2.text[:200]}")
