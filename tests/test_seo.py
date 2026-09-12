import unittest

from starlette.testclient import TestClient

from app.config import settings
from app.main import app


class TestSEO(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)
        self.base = settings.app_url.rstrip("/")

    def test_robots_txt_permite_indice_y_bloquea_admin(self):
        r = self.client.get("/robots.txt")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/plain", r.headers["content-type"])
        self.assertIn("Disallow: /admin", r.text)
        self.assertIn(f"Sitemap: {self.base}/sitemap.xml", r.text)

    def test_sitemap_xml_lista_paginas_publicas(self):
        r = self.client.get("/sitemap.xml")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<urlset", r.text)
        self.assertIn(f"<loc>{self.base}/</loc>", r.text)
        self.assertIn(f"<loc>{self.base}/privacidad</loc>", r.text)

    def test_landing_open_graph_y_twitter(self):
        r = self.client.get("/")
        self.assertIn('<meta property="og:title"', r.text)
        self.assertIn('property="og:image"', r.text)
        self.assertIn('name="twitter:card"', r.text)
        self.assertIn('<link rel="canonical"', r.text)

    def test_landing_json_ld_legal_service_y_faq(self):
        r = self.client.get("/")
        self.assertIn("application/ld+json", r.text)
        self.assertIn('"@type": "LegalService"', r.text)
        self.assertIn('"@type": "FAQPage"', r.text)


if __name__ == "__main__":
    unittest.main()