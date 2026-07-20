"""
Simulador de conversación WhatsApp - Tutelas Online
Prueba el flujo completo desde la terminal.
"""
import httpx
try:
    import readline  # para flechas del teclado (Unix)
except ImportError:
    pass

BASE = "http://localhost:8000"
NUMERO = "whatsapp:+573001234567"  # número simulado


def send(body: str, media: bool = False):
    data = {"From": NUMERO, "Body": body, "NumMedia": "1" if media else "0",
            "MediaUrl0": "https://fakeimg.pl/400x200" if media else "",
            "MediaContentType0": "image/jpeg" if media else ""}
    r = httpx.post(f"{BASE}/webhook/whatsapp", data=data, timeout=30)
    return r.json()


def main():
    print("=" * 50)
    print("  SIMULADOR WHATSAPP - Tutelas Online")
    print("  Escribe 'salir' para terminar")
    print("  Escribe 'foto' para simular envío de imagen")
    print("=" * 50)

    # Inicio
    r = send("Hola")
    print("\n>>> Tú: Hola")
    print(f"<<< Bot: (checkea el servidor)")

    while True:
        msg = input("\n>>> Tú: ").strip()
        if not msg:
            continue
        if msg.lower() == "salir":
            break
        if msg.lower() == "foto":
            r = send("", media=True)
            print(f"<<< Bot: respuesta {r.get('estado', 'ok')}")
        else:
            r = send(msg)
            if r.get("ok"):
                print(f"<<< Bot: ✓")
            else:
                print(f"<<< Bot: {r}")

    print("Simulación terminada.")


if __name__ == "__main__":
    main()
