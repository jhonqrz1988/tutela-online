import httpx, json, uuid

api_key = "451130a099aeb81329f634316f8e7f8b-f34b927e-767d-4de3-be8c-c0578ac14abc"
base_url = "4k5qr6.api.infobip.com"

payload = {
    "messages": [{
        "from": "447860088970",
        "to": "573106161629",
        "messageId": str(uuid.uuid4()),
        "content": {
            "templateName": "test_whatsapp_template_en",
            "templateData": {
                "body": {
                    "placeholders": ["Walter"]
                }
            },
            "language": "en"
        }
    }]
}

r = httpx.post(
    f"https://{base_url}/whatsapp/1/message/template",
    json=payload,
    headers={
        "Authorization": f"App {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    },
    timeout=15,
)
print(f"Status: {r.status_code}")
print(f"Response: {r.text}")