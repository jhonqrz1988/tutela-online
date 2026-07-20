import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_webhook_whatsapp_get(client):
    resp = await client.get("/webhook/whatsapp")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_webhook_whatsapp_post(client):
    resp = await client.post(
        "/webhook/whatsapp",
        data={"From": "whatsapp:+573001234567", "Body": "Hola", "NumMedia": "0"},
    )
    assert resp.status_code == 200
