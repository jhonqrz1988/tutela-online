import os, time
os.environ["SECRET_KEY"] = "test"
from pyngrok import ngrok

ngrok.set_auth_token("3GZQrEXrN3hWbxGDTw0q5BqZi9t_48hhHUwXyYgBQapefMKYZ")

tunnel = ngrok.connect(8000, bind_tls=True)
print("ngrok URL:", tunnel.public_url)

# Mantener vivo
try:
    while True:
        time.sleep(10)
except KeyboardInterrupt:
    ngrok.disconnect(tunnel.public_url)
