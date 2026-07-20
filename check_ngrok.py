import httpx, json

# Check ngrok requests
r = httpx.get("http://127.0.0.1:4040/api/requests/http", timeout=5)
data = r.json()
requests = data.get("requests", [])
print(f"Total requests: {len(requests)}")
for req in requests:
    method = req.get("method", "?")
    path = req.get("path", "?")
    status = req.get("response", {}).get("status", "?")
    print(f"  {method} {path} -> {status}")

# Check tunnels
r2 = httpx.get("http://127.0.0.1:4040/api/tunnels", timeout=5)
tunnels = r2.json().get("tunnels", [])
for t in tunnels:
    print(f"Tunnel: {t['public_url']} -> {t['config']['addr']}")
