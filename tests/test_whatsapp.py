import unittest
from starlette.testclient import TestClient

from app.database import Base, engine
from app.main import app


class TestWhatsAppEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.create_all(bind=engine)
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        cls.client.close()

    def test_health(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "ok")

    def test_webhook_meta_verification(self):
        resp = self.client.get(
            "/webhook/meta?hub.mode=subscribe&hub.verify_token=mi_verify_token_123&hub.challenge=123"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.text, "123")

    def test_webhook_meta_post(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "0",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "phone_number_id": "1157524497451238",
                                    "display_phone_number": "15551540154",
                                },
                                "messages": [
                                    {
                                        "from": "573009998877",
                                        "id": "wamid.test",
                                        "timestamp": "1700000000",
                                        "type": "text",
                                        "text": {"body": "Hola"},
                                    }
                                ],
                            }
                        }
                    ],
                }
            ],
        }
        resp = self.client.post("/webhook/meta", json=payload)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))


if __name__ == "__main__":
    unittest.main()
