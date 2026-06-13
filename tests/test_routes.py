"""HTTP-layer tests via Flask's test_client.

The app is built against a throwaway cache file (config.CACHE_FILE is patched
before create_app) so the real mail_cache.json is never touched, and no
scheduler or Outlook initializer is ever started.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from mailfilter import create_app
from tests.factories import make_mail

try:
    import win32com  # noqa: F401
    HAVE_PYWIN32 = True
except ImportError:
    HAVE_PYWIN32 = False


class RouteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig_cache = config.CACHE_FILE
        self._orig_settings = config.SETTINGS_FILE
        config.CACHE_FILE = Path(self._tmpdir) / "cache.json"
        config.SETTINGS_FILE = Path(self._tmpdir) / "settings.json"
        self.app = create_app()
        self.store = self.app.extensions["mail_store"]
        self.store.add_mails([
            make_mail(id="ID1", subject="server error"),
            make_mail(id="ID2", subject="newsletter", body="no urls", attachments=[]),
        ])
        self.client = self.app.test_client()

    def tearDown(self):
        config.CACHE_FILE = self._orig_cache
        config.SETTINGS_FILE = self._orig_settings
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_index_renders(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Mail Analyzer", resp.data)

    def test_api_mail_returns_mails_and_status(self):
        data = self.client.get("/api/mail").get_json()
        self.assertEqual(len(data["mails"]), 2)
        for key in ("last_refresh", "fetch_status", "fetch_error"):
            self.assertIn(key, data)

    def test_api_mail_applies_filters(self):
        hit = self.client.get("/api/mail?main=server").get_json()
        self.assertEqual([m["subject"] for m in hit["mails"]], ["server error"])
        miss = self.client.get("/api/mail?main=doesnotexist").get_json()
        self.assertEqual(miss["mails"], [])

    def test_api_mail_resources_filter(self):
        # Only ID1 carries links/attachments.
        data = self.client.get("/api/mail?resources=1").get_json()
        self.assertEqual(len(data["mails"]), 1)
        self.assertEqual(data["mails"][0]["subject"], "server error")

    def test_api_mail_valid_query_has_empty_error(self):
        data = self.client.get("/api/mail?main=server").get_json()
        self.assertEqual(data["query_error"], "")

    def test_api_mail_reports_malformed_query(self):
        data = self.client.get("/api/mail?main=a;").get_json()  # trailing operator
        self.assertEqual(data["mails"], [])
        self.assertTrue(data["query_error"])

    def test_refresh_starts_a_fetch(self):
        # Patch the actual fetch so the spawned thread does no real work.
        with mock.patch("mailfilter.outlook.refresh") as fake:
            resp = self.client.post("/refresh")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json(), {"status": "started"})
        # The detached thread should have invoked outlook.refresh(store).
        for _ in range(100):
            if fake.called:
                break
            import time
            time.sleep(0.01)
        fake.assert_called_once_with(self.store)

    def test_attachment_unknown_mail_is_404(self):
        self.assertEqual(self.client.get("/attachments/NOPE/0").status_code, 404)

    def test_attachment_index_out_of_range_is_404(self):
        # ID1 has exactly one attachment (index 0); index 5 is unknown.
        self.assertEqual(self.client.get("/attachments/ID1/5").status_code, 404)

    @unittest.skipIf(HAVE_PYWIN32, "needs a machine without Outlook/pywin32")
    def test_attachment_download_unavailable_is_503(self):
        # Known (id, index) but Outlook can't be reached -> 503.
        self.assertEqual(self.client.get("/attachments/ID1/0").status_code, 503)

    def test_get_settings_returns_defaults_initially(self):
        data = self.client.get("/api/settings").get_json()
        self.assertEqual(data["main"], "")
        self.assertFalse(data["resources"])

    def test_post_settings_persists_and_get_returns_them(self):
        resp = self.client.post(
            "/api/settings", json={"main": "server", "resources": True}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["main"], "server")
        again = self.client.get("/api/settings").get_json()
        self.assertEqual(again["main"], "server")
        self.assertTrue(again["resources"])

    def test_settings_survive_an_app_restart(self):
        self.client.post("/api/settings", json={"sender": "alice@example.com"})
        # A fresh app against the same (patched) SETTINGS_FILE reloads them.
        restarted = create_app().test_client()
        data = restarted.get("/api/settings").get_json()
        self.assertEqual(data["sender"], "alice@example.com")

    def test_post_settings_rejects_non_object(self):
        resp = self.client.post("/api/settings", json=["not", "a", "dict"])
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
