import httpx, os
from base64 import b64encode
from dotenv import load_dotenv
load_dotenv()

sid = os.getenv("TWILIO_ACCOUNT_SID")
token = os.getenv("TWILIO_AUTH_TOKEN")
auth = b64encode(f"{sid}:{token}".encode()).decode()

r = httpx.post(
    f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
    data={
        "Body": "Prueba de envio Twilio",
        "From": "whatsapp:+14155238886",
        "To": "whatsapp:+573106161629",
    },
    headers={
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    },
    timeout=15,
)
print(f"Status: {r.status_code}")
print(r.text[:600])
