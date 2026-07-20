import httpx, json

# Test local
r = httpx.post("http://localhost:8000/webhook/zapi",
    json={"phone": "573106161629", "message": {"text": "Hola", "type": "text"}},
    timeout=10)
print(f"Local: {r.status_code} - {r.text[:200]}")

# Test via ngrok
r2 = httpx.post("https://stoning-shimmer-cleaver.ngrok-free.dev/webhook/zapi",
    json={"phone": "573106161629", "message": {"text": "Hola", "type": "text"}},
    timeout=10)
print(f"Ngrok: {r2.status_code} - {r2.text[:200]}")
