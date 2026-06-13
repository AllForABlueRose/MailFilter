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
        self._orig_tags = config.TAGS_FILE
        config.CACHE_FILE = Path(self._tmpdir) / "cache.json"
        config.SETTINGS_FILE = Path(self._tmpdir) / "settings.json"
        config.TAGS_FILE = Path(self._tmpdir) / "tags.json"
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
        config.TAGS_FILE = self._orig_tags
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

    def test_api_mail_exclude_sender(self):
        # Both seeded mails are from "Alice Smith" (make_mail default).
        self.assertEqual(self.client.get("/api/mail?exclude_sender=alice").get_json()["mails"], [])

    def test_view_models_include_people(self):
        vm = self.client.get("/api/mail").get_json()["mails"][0]
        self.assertIn("name", vm["sender"])
        self.assertIsInstance(vm["recipients"], list)
        self.assertIn("cc", vm)

    def test_attachment_blacklist_omits_in_api(self):
        self.store.add_mails([
            make_mail(id="BL", attachments=[{"filename": "virus.exe"}, {"filename": "doc.pdf"}]),
        ])
        data = self.client.get("/api/mail?attachment_blacklist=.exe").get_json()
        vm = next(m for m in data["mails"] if m["id"] == "BL")
        self.assertEqual([a["filename"] for a in vm["attachments"]], ["doc.pdf"])

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

    def test_thread_returns_conversation_earliest_first(self):
        self.store.add_mails([
            make_mail(id="T1", conversation_id="CT", received="2026-06-02 08:00:00"),
            make_mail(id="T2", conversation_id="CT", received="2026-06-01 08:00:00"),
        ])
        data = self.client.get("/api/thread?id=T1").get_json()
        self.assertEqual([m["id"] for m in data["mails"]], ["T2", "T1"])

    def test_thread_unknown_id_is_empty(self):
        self.assertEqual(self.client.get("/api/thread?id=nope").get_json()["mails"], [])

    def test_download_saves_attachments_to_dated_folder(self):
        self.store.add_mails([
            make_mail(id="D1", attachments=[{"filename": "a.pdf"}, {"filename": "b.pdf"}]),
        ])
        downloads = Path(self._tmpdir) / "downloads"
        orig = config.ATTACHMENTS_DIR
        config.ATTACHMENTS_DIR = downloads
        try:
            with mock.patch(
                "mailfilter.outlook.fetch_attachment",
                side_effect=lambda mid, idx: (f"file{idx}.pdf", b"bytes"),
            ):
                resp = self.client.post(
                    "/api/download",
                    json={"items": [{"id": "D1", "index": 0}, {"id": "D1", "index": 1}]},
                )
            data = resp.get_json()
            self.assertEqual(len(data["saved"]), 2)
            self.assertEqual(data["errors"], [])
            # Each saved entry maps back to its mail (used for the UI tag).
            self.assertEqual({s["id"] for s in data["saved"]}, {"D1"})
            from datetime import datetime
            folder = downloads / datetime.now().strftime("%Y-%m-%d")
            self.assertTrue(folder.is_dir())
            self.assertEqual(len(list(folder.iterdir())), 2)
        finally:
            config.ATTACHMENTS_DIR = orig

    def test_download_reports_unknown_attachment(self):
        downloads = Path(self._tmpdir) / "downloads2"
        orig = config.ATTACHMENTS_DIR
        config.ATTACHMENTS_DIR = downloads
        try:
            resp = self.client.post("/api/download", json={"items": [{"id": "nope", "index": 0}]})
            data = resp.get_json()
            self.assertEqual(data["saved"], [])
            self.assertTrue(data["errors"])
        finally:
            config.ATTACHMENTS_DIR = orig

    def test_mail_view_models_carry_tags(self):
        mails = self.client.get("/api/mail").get_json()["mails"]
        self.assertTrue(all("tags" in m for m in mails))
        self.assertEqual(mails[0]["tags"], {})  # nothing recorded yet

    def test_post_tags_records_links(self):
        resp = self.client.post("/api/tags", json={"ids": ["ID1"], "action": "links"})
        self.assertEqual(resp.status_code, 200)
        vm = next(m for m in self.client.get("/api/mail").get_json()["mails"] if m["id"] == "ID1")
        self.assertEqual(vm["tags"].get("links"), "recent")

    def test_post_tags_rejects_non_object(self):
        self.assertEqual(self.client.post("/api/tags", json=["x"]).status_code, 400)

    def test_download_records_downloaded_tag(self):
        self.store.add_mails([make_mail(id="DT", attachments=[{"filename": "a.pdf"}])])
        orig = config.ATTACHMENTS_DIR
        config.ATTACHMENTS_DIR = Path(self._tmpdir) / "dl"
        try:
            with mock.patch("mailfilter.outlook.fetch_attachment",
                            side_effect=lambda mid, idx: ("a.pdf", b"x")):
                self.client.post("/api/download", json={"items": [{"id": "DT", "index": 0}]})
            vm = next(m for m in self.client.get("/api/mail").get_json()["mails"] if m["id"] == "DT")
            self.assertEqual(vm["tags"].get("downloaded"), "recent")
        finally:
            config.ATTACHMENTS_DIR = orig

    def test_thread_highlights_with_active_search(self):
        self.store.add_mails([
            make_mail(id="H1", conversation_id="HC", body="the server crashed"),
        ])
        data = self.client.get("/api/thread?id=H1&main=server").get_json()
        self.assertIn('class="highlight-main"', data["mails"][0]["preview"])

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
