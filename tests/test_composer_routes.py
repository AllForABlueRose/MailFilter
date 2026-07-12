"""HTTP-layer tests for Composer: the block palette, the examples, the paged
cache-mail picker, and the preview.

Composer is READ-ONLY -- these tests assert not just what it returns but that it
writes nothing (no draft lands in the mock drafts dir). The app is built against
throwaway caches so the real project files are never touched.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import config
from mailfilter import create_app

from tests.factories import make_mail


class ComposerRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        tmp = Path(self._tmp)
        self._orig = {k: getattr(config, k) for k in (
            "CACHE_FILE", "SETTINGS_FILE", "TAGS_FILE", "TEMPLATES_DIR",
            "AUTOMATIONS_FILE", "CUSTOMERS_FILE", "COMPOSE_TEMPLATES_FILE",
            "PASSWORD_SETTINGS_FILE", "EXPERIMENTAL_FILE", "CUSTOMER_MATCH_FILE",
            "VAULT_FILE", "CALENDAR_PINS_FILE", "MOCK_DRAFTS_DIR", "FILE_SERVER_DIR",
            "WORKSPACE_DIR", "COMPOSER_PAGE_SIZE", "COMPOSER_PAGE_SIZE_MAX")}
        config.CACHE_FILE = tmp / "cache.json"
        config.SETTINGS_FILE = tmp / "settings.json"
        config.TAGS_FILE = tmp / "tags.json"
        config.TEMPLATES_DIR = tmp / "search_templates"
        config.AUTOMATIONS_FILE = tmp / "automations.json"
        config.CUSTOMERS_FILE = tmp / "customers.json"
        config.COMPOSE_TEMPLATES_FILE = tmp / "compose.json"
        config.PASSWORD_SETTINGS_FILE = tmp / "pwd.json"
        config.EXPERIMENTAL_FILE = tmp / "exp.json"
        config.CUSTOMER_MATCH_FILE = tmp / "cmatch.json"
        config.VAULT_FILE = tmp / "vault.json"
        config.CALENDAR_PINS_FILE = tmp / "calendar_pins.json"
        config.MOCK_DRAFTS_DIR = tmp / "drafts"
        config.WORKSPACE_DIR = tmp / "workspace"
        config.COMPOSER_PAGE_SIZE = 10
        config.COMPOSER_PAGE_SIZE_MAX = 50

        self.app = create_app()
        self.client = self.app.test_client()
        self.store = self.app.extensions["mail_store"]
        self.store.add_mails([
            make_mail(id=f"M{i}", conversation_id=f"C{i}",
                      subject=f"Subject {i}", sender=f"Person {i}",
                      sender_email=f"p{i}@acme.co.jp",
                      received=f"2026-06-{(i % 28) + 1:02d} 09:00:00",
                      body="no links here",
                      attachments=[{"filename": "report.pdf"}] if i % 2 == 0 else [])
            for i in range(25)])

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(config, k, v)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _drafts(self):
        d = Path(config.MOCK_DRAFTS_DIR)
        return list(d.glob("*.json")) if d.exists() else []

    # ----- the function palette -----

    def test_blocks_route_returns_ten_blocks_with_live_demo_output(self):
        data = self.client.get("/api/composer/blocks").get_json()
        self.assertEqual(len(data["blocks"]), 10)
        for block in data["blocks"]:
            with self.subTest(block=block["id"]):
                self.assertTrue(block["demo_output"].strip())
                self.assertNotIn("(error:", block["demo_output"])
        self.assertIn("row", data["demo_context"])

    # ----- the examples -----

    def test_samples_route_returns_ten_examples_and_the_picker_filters(self):
        data = self.client.get("/api/composer/samples").get_json()
        self.assertEqual(len(data["samples"]), 10)
        self.assertTrue(all("row" in s and "mail" in s for s in data["samples"]))
        self.assertTrue(any(f["id"] == "attachments" for f in data["filters"]))

    # ----- the paged cache-mail picker -----

    def test_mails_route_pages_ten_at_a_time_without_overlap(self):
        first = self.client.get("/api/composer/mails?offset=0").get_json()
        second = self.client.get("/api/composer/mails?offset=10").get_json()
        self.assertEqual(len(first["mails"]), 10)
        self.assertEqual(len(second["mails"]), 10)
        self.assertEqual(first["total"], 25)
        self.assertTrue(first["has_more"])
        ids1 = {m["id"] for m in first["mails"]}
        ids2 = {m["id"] for m in second["mails"]}
        self.assertFalse(ids1 & ids2)

    def test_last_page_reports_no_more(self):
        last = self.client.get("/api/composer/mails?offset=20").get_json()
        self.assertEqual(len(last["mails"]), 5)
        self.assertFalse(last["has_more"])

    def test_mails_are_newest_first(self):
        page = self.client.get("/api/composer/mails?offset=0").get_json()
        received = [m["received"] for m in page["mails"]]
        self.assertEqual(received, sorted(received, reverse=True))

    def test_attachment_filter_narrows_the_total(self):
        data = self.client.get("/api/composer/mails?filter=attachments").get_json()
        self.assertEqual(data["total"], 13)
        self.assertTrue(all(m["has_attachments"] for m in data["mails"]))

    def test_unknown_filter_falls_back_to_all(self):
        data = self.client.get("/api/composer/mails?filter=bogus").get_json()
        self.assertEqual(data["total"], 25)

    def test_limit_is_clamped_so_one_request_cannot_pull_the_whole_cache(self):
        config.COMPOSER_PAGE_SIZE_MAX = 3
        data = self.client.get("/api/composer/mails?limit=999").get_json()
        self.assertEqual(len(data["mails"]), 3)

    def test_a_junk_offset_does_not_500(self):
        data = self.client.get("/api/composer/mails?offset=abc").get_json()
        self.assertEqual(len(data["mails"]), 10)

    # ----- preview -----

    def _preview(self, **payload):
        return self.client.post("/api/composer/preview", json=payload)

    def test_preview_of_a_sample_renders_the_ftp_branch(self):
        body = ("{% if row.uses_ftp %}Link: {{ ftp_link(row.file_name) }}"
                "{% else %}Attached: {{ row.file_name }}{% endif %}")
        plan = self._preview(body=body, source="sample", ref="sample-ftp").get_json()["plan"]
        self.assertEqual(plan["status"], "ready")
        self.assertTrue(plan["uses_ftp"])
        self.assertTrue(plan["ftp_link"].startswith(config.FTP_LINK_BASE))
        self.assertIn("Link: ", plan["body"])

    def test_preview_of_a_sample_resolves_an_attachment_on_the_file_server(self):
        body = "Attached: {{ row.file_name }}"
        plan = self._preview(body=body, source="sample",
                             ref="sample-attached").get_json()["plan"]
        self.assertEqual(plan["status"], "ready")
        self.assertTrue(plan["attachment"]["exists"])
        # Confined to the file-server root, the same guard Press relies on.
        self.assertTrue(plan["attachment"]["path"].startswith(
            str(Path(config.FILE_SERVER_DIR).resolve())))

    def test_preview_uses_the_unsaved_body_not_only_a_stored_template(self):
        plan = self._preview(body="Hello {{ sender.first_name }}", source="sample",
                             ref="sample-attached").get_json()["plan"]
        self.assertEqual(plan["body"], "Hello Kenji")

    def test_preview_can_run_a_stored_template_by_id(self):
        created = self.client.post("/api/compose-templates", json={
            "name": "Stored", "body": "From the store: {{ row.ref }}"}).get_json()
        plan = self._preview(template_id=created["id"], source="sample",
                             ref="sample-attached").get_json()["plan"]
        self.assertEqual(plan["body"], "From the store: acme-1042")

    def test_preview_against_a_real_cache_mail_synthesizes_its_row(self):
        data = self._preview(body="{{ row.file_name }} / {{ mail.subject }}",
                             source="mail", ref="M0").get_json()
        self.assertEqual(data["row"]["file_name"], "report.pdf")
        self.assertEqual(data["plan"]["body"], "report.pdf / Subject 0")

    def test_a_broken_template_reports_its_error_and_blocks(self):
        data = self._preview(body="{{ upper( }}", source="sample",
                             ref="sample-ftp").get_json()
        self.assertTrue(data["template_error"])
        self.assertEqual(data["plan"]["status"], "blocked")
        self.assertEqual(data["plan"]["body"], "")

    def test_unknown_refs_and_sources_are_rejected(self):
        self.assertEqual(self._preview(body="x", source="sample", ref="nope").status_code, 404)
        self.assertEqual(self._preview(body="x", source="mail", ref="nope").status_code, 404)
        self.assertEqual(self._preview(body="x", source="bogus", ref="M0").status_code, 400)
        self.assertEqual(
            self._preview(body="x", source="sample", ref="sample-ftp",
                          template_id="nosuch").status_code, 404)

    def test_composer_never_writes_a_draft(self):
        body = "{% if row.uses_ftp %}Link{% else %}Attached{% endif %}"
        for sample in self.client.get("/api/composer/samples").get_json()["samples"]:
            self._preview(body=body, source="sample", ref=sample["id"])
        self._preview(body=body, source="mail", ref="M0")
        self.assertEqual(self._drafts(), [])


if __name__ == "__main__":
    unittest.main()
