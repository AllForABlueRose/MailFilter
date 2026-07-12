"""HTTP-layer tests via Flask's test_client.

The app is built against a throwaway cache file (config.CACHE_FILE is patched
before create_app) so the real mail_cache.json is never touched, and no
scheduler or Outlook initializer is ever started.
"""

import io
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
        self._orig_templates = config.TEMPLATES_DIR
        self._orig_automations = config.AUTOMATIONS_FILE
        self._orig_customers = config.CUSTOMERS_FILE
        self._orig_customer_match = config.CUSTOMER_MATCH_FILE
        self._orig_experimental = config.EXPERIMENTAL_FILE
        self._orig_categories = config.CATEGORIES_FILE
        self._orig_compose = config.COMPOSE_TEMPLATES_FILE
        self._orig_mailbox = config.MAILBOX_FILE
        config.CACHE_FILE = Path(self._tmpdir) / "cache.json"
        config.SETTINGS_FILE = Path(self._tmpdir) / "settings.json"
        config.TAGS_FILE = Path(self._tmpdir) / "tags.json"
        config.TEMPLATES_DIR = Path(self._tmpdir) / "search_templates"
        # Isolate the list-backed stores too, so the suite never reads or writes
        # the real automations/customers/customer-match/experimental caches.
        config.AUTOMATIONS_FILE = Path(self._tmpdir) / "automations.json"
        config.CUSTOMERS_FILE = Path(self._tmpdir) / "customers.json"
        config.CUSTOMER_MATCH_FILE = Path(self._tmpdir) / "customer_match.json"
        config.EXPERIMENTAL_FILE = Path(self._tmpdir) / "experimental.json"
        # create_app() SEEDS these on a first run (the categories, the starter reply
        # template), so they must be temp files or the suite would write the user's own.
        config.CATEGORIES_FILE = Path(self._tmpdir) / "categories.json"
        config.COMPOSE_TEMPLATES_FILE = Path(self._tmpdir) / "compose_templates.json"
        config.MAILBOX_FILE = Path(self._tmpdir) / "mailbox.json"
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
        config.TEMPLATES_DIR = self._orig_templates
        config.AUTOMATIONS_FILE = self._orig_automations
        config.CUSTOMERS_FILE = self._orig_customers
        config.CUSTOMER_MATCH_FILE = self._orig_customer_match
        config.EXPERIMENTAL_FILE = self._orig_experimental
        config.CATEGORIES_FILE = self._orig_categories
        config.COMPOSE_TEMPLATES_FILE = self._orig_compose
        config.MAILBOX_FILE = self._orig_mailbox
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_index_renders(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Mail Analyzer 3.0", resp.data)

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

    def test_api_mail_org_labels_default_empty(self):
        # No org maps the seeded senders, so every mail carries an empty label list.
        for vm in self.client.get("/api/mail").get_json()["mails"]:
            self.assertEqual(vm["org_labels"], [])

    def test_api_mail_org_labels_resolve_sender(self):
        # Map the seeded sender's domain to an org; its display-name/colour pill
        # then appears on every mail from that domain.
        cs = self.app.extensions["customer_store"]
        org = cs.create({"name": "Example Inc", "display_name": "Ex", "color": "#0a0b0c"})
        cs.set_domain(org["id"], "example.com", "member")
        vm = self.client.get("/api/mail").get_json()["mails"][0]
        self.assertEqual(vm["org_labels"], [{"name": "Ex", "color": "#0a0b0c",
                                             "card_style": "outline", "card_ink": "white"}])

    def test_api_mail_org_label_is_single_winner_rep_beats_member(self):
        # Seeded sender is example.com: member of Base, represented by Acme -> one pill.
        cs = self.app.extensions["customer_store"]
        base = cs.create({"name": "Base Inc"})
        cs.set_domain(base["id"], "example.com", "member")
        acme = cs.create({"name": "Acme Corp", "display_name": "Acme", "color": "#abcdef"})
        cs.set_domain(acme["id"], "example.com", "representative")
        vm = self.client.get("/api/mail").get_json()["mails"][0]
        self.assertEqual(vm["org_labels"], [{"name": "Acme", "color": "#abcdef",
                                             "card_style": "outline", "card_ink": "white"}])

    def test_api_mail_pill_reflects_brute_force_only_when_enabled(self):
        cs = self.app.extensions["customer_store"]
        glo = cs.create({"name": "Globex Inc", "display_name": "Globex", "color": "#00ff00"})
        self.app.extensions["customer_match_store"].update(
            [{"keyword": "server error", "org_id": glo["id"]}])  # matches ID1's subject
        # Feature disabled -> keyword tier off -> no pill (sender in no org).
        vm = next(m for m in self.client.get("/api/mail").get_json()["mails"] if m["id"] == "ID1")
        self.assertEqual(vm["org_labels"], [])
        # Enable the experimental feature -> the keyword org now drives the pill.
        self.app.extensions["experimental_store"].update({"resolve_customer_name": True})
        vm = next(m for m in self.client.get("/api/mail").get_json()["mails"] if m["id"] == "ID1")
        self.assertEqual(vm["org_labels"], [{"name": "Globex", "color": "#00ff00",
                                             "card_style": "outline", "card_ink": "white"}])

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

    def test_download_single_item_saves_and_tags(self):
        # Req 12: a per-attachment download reuses /api/download with a one-item list,
        # inheriting the workspace save + the "downloaded" tag.
        self.store.add_mails([make_mail(id="S1", attachments=[{"filename": "a.pdf"}])])
        downloads = Path(self._tmpdir) / "one"
        orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = downloads
        try:
            with mock.patch("mailfilter.outlook.fetch_attachment",
                            side_effect=lambda mid, idx: ("a.pdf", b"bytes")):
                resp = self.client.post("/api/download",
                                        json={"items": [{"id": "S1", "index": 0}]})
            self.assertEqual(len(resp.get_json()["saved"]), 1)
            from datetime import datetime
            folder = downloads / datetime.now().strftime("%Y-%m-%d")
            self.assertTrue((folder / "a.pdf").exists())
            m = next(x for x in self.client.get("/api/mail").get_json()["mails"] if x["id"] == "S1")
            self.assertIn("downloaded", m["tags"])
        finally:
            config.WORKSPACE_DIR = orig

    def test_download_saves_attachments_to_dated_folder(self):
        self.store.add_mails([
            make_mail(id="D1", attachments=[{"filename": "a.pdf"}, {"filename": "b.pdf"}]),
        ])
        downloads = Path(self._tmpdir) / "downloads"
        orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = downloads
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
            # Two saved attachments plus the sidecar org manifest.
            saved_files = [p.name for p in folder.iterdir()
                           if p.name != config.WORKSPACE_MANIFEST_NAME]
            self.assertEqual(len(saved_files), 2)
        finally:
            config.WORKSPACE_DIR = orig

    def test_report_exports_csv_to_dated_folder(self):
        import csv
        from datetime import datetime
        self.store.add_mails([
            make_mail(id="R1", subject="alpha", received="2026-06-10 09:30:00",
                      sender="Alice", sender_email="alice@x.com",
                      recipient_names=["Bob"], recipient_emails=["bob@x.com"]),
        ])
        out = Path(self._tmpdir) / "wreport"
        orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = out
        try:
            resp = self.client.post("/api/report", json={"ids": ["R1", "nope"]})
            data = resp.get_json()
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(data["count"], 1)  # unknown id skipped
            folder = out / datetime.now().strftime("%Y-%m-%d")
            saved = folder / data["name"]
            self.assertTrue(saved.is_file())
            self.assertIn(datetime.now().strftime("%Y-%m-%d"), data["name"])
            with saved.open(encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows[0], ["Datetime", "subject", "recipient", "sender",
                                       "customer organization"])
            # "Bob"/"Alice" are substrings of their emails -> the email is used.
            self.assertEqual(rows[1], ["2026-06-10 09:30:00", "alpha",
                                       "bob@x.com", "alice@x.com", ""])
        finally:
            config.WORKSPACE_DIR = orig

    def test_report_brute_force_gated_by_experimental_flag(self):
        import csv
        from datetime import datetime
        self.store.add_mails([make_mail(id="RB", subject="server error alpha",
                                        sender_email="x@nobody.com")])
        glo = self.app.extensions["customer_store"].create({"name": "Globex Inc"})
        self.app.extensions["customer_match_store"].update(
            [{"keyword": "server error", "org_id": glo["id"]}])
        out = Path(self._tmpdir) / "wreport2"
        orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = out

        def org_cell():
            self.client.post("/api/report", json={"ids": ["RB"]})
            folder = out / datetime.now().strftime("%Y-%m-%d")
            csv_file = next(p for p in folder.iterdir() if p.suffix == ".csv")
            with csv_file.open(encoding="utf-8-sig", newline="") as f:
                return list(csv.reader(f))[1][4]
        try:
            self.assertEqual(org_cell(), "")   # feature disabled -> keyword tier off
            self.app.extensions["experimental_store"].update({"resolve_customer_name": True})
            self.assertEqual(org_cell(), "Globex Inc")  # enabled -> keyword org (real name)
        finally:
            config.WORKSPACE_DIR = orig

    def test_download_append_gated_by_experimental_flag(self):
        self.store.add_mails([make_mail(id="DA", sender_email="bob@example.com",
                                        attachments=[{"filename": "a.pdf"}])])
        cs = self.app.extensions["customer_store"]
        org = cs.create({"name": "Example Inc"})
        cs.set_domain(org["id"], "example.com", "member")
        out = Path(self._tmpdir) / "wdl"
        orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = out

        def saved_name():
            with mock.patch("mailfilter.outlook.fetch_attachment",
                            side_effect=lambda mid, idx: ("a.pdf", b"bytes")):
                r = self.client.post("/api/download", json={
                    "items": [{"id": "DA", "index": 0}], "append_customer_name": True})
            return r.get_json()["saved"][0]["name"]
        try:
            # Append feature disabled -> the request's append flag is ignored.
            self.assertEqual(saved_name(), "a.pdf")
            self.app.extensions["experimental_store"].update({"append_customer_name": True})
            self.assertEqual(saved_name(), "a_Example Inc.pdf")
        finally:
            config.WORKSPACE_DIR = orig

    def test_report_rejects_non_object(self):
        self.assertEqual(self.client.post("/api/report", json=["x"]).status_code, 400)

    def test_workspace_cleanup_deletes_only_app_files(self):
        from datetime import datetime
        from mailfilter import workspace_manifest
        out = Path(self._tmpdir) / "wclean"
        orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = out
        try:
            folder = out / datetime.now().strftime("%Y-%m-%d")
            folder.mkdir(parents=True)
            (folder / "app.png").write_bytes(b"downloaded-through-the-app!!")
            workspace_manifest.record(str(folder), "app.png",
                                      {"org_id": "", "org_name": "", "mail_id": "m1"})
            (folder / "notes.txt").write_text("incidental file")

            resp = self.client.post("/api/workspace/cleanup")
            data = resp.get_json()
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(data["deleted"], ["app.png"])
            self.assertEqual(data["kept_count"], 1)
            self.assertFalse((folder / "app.png").exists())
            self.assertTrue((folder / "notes.txt").exists())
        finally:
            config.WORKSPACE_DIR = orig

    def test_workspace_bring_last_renames_previous_folder_to_today(self):
        from datetime import datetime
        out = Path(self._tmpdir) / "wbring"
        orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = out
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            (out / "2020-01-02").mkdir(parents=True)
            (out / "2020-01-02" / "carried.txt").write_text("older workspace")

            resp = self.client.post("/api/workspace/bring-last")
            data = resp.get_json()
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(data["ok"])
            self.assertEqual(data["source"], "2020-01-02")
            self.assertTrue((out / today / "carried.txt").exists())
            self.assertFalse((out / "2020-01-02").exists())
        finally:
            config.WORKSPACE_DIR = orig

    def test_workspace_bring_last_409_when_today_exists(self):
        from datetime import datetime
        out = Path(self._tmpdir) / "wbring2"
        orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = out
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            (out / today).mkdir(parents=True)
            (out / "2020-01-02").mkdir(parents=True)

            resp = self.client.post("/api/workspace/bring-last")
            data = resp.get_json()
            self.assertEqual(resp.status_code, 409)
            self.assertFalse(data["ok"])
            self.assertIn("error", data)
            self.assertTrue((out / "2020-01-02").exists())  # untouched
        finally:
            config.WORKSPACE_DIR = orig

    def test_workspace_file_org_sets_overwrites_and_clears(self):
        from datetime import datetime
        from mailfilter import workspace_manifest
        out = Path(self._tmpdir) / "wstamp"
        orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = out
        try:
            a = self.client.post("/api/organizations", json={"name": "Acme Corp"}).get_json()
            b = self.client.post("/api/organizations", json={"name": "Orion"}).get_json()
            folder = out / datetime.now().strftime("%Y-%m-%d")
            folder.mkdir(parents=True)
            (folder / "doc.pdf").write_text("x")

            r = self.client.post("/api/workspace/file-org",
                                 json={"filename": "doc.pdf", "org_id": a["id"]})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(workspace_manifest.lookup(str(folder), "doc.pdf")["org_name"], "Acme Corp")

            # Overwrite to another org.
            self.client.post("/api/workspace/file-org",
                             json={"filename": "doc.pdf", "org_id": b["id"]})
            self.assertEqual(workspace_manifest.lookup(str(folder), "doc.pdf")["org_id"], b["id"])

            # Clear.
            self.client.post("/api/workspace/file-org",
                             json={"filename": "doc.pdf", "org_id": ""})
            self.assertIsNone(workspace_manifest.lookup(str(folder), "doc.pdf"))
        finally:
            config.WORKSPACE_DIR = orig

    def test_workspace_file_org_unknown_org_400_and_missing_file_404(self):
        from datetime import datetime
        out = Path(self._tmpdir) / "wstamp2"
        orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = out
        try:
            folder = out / datetime.now().strftime("%Y-%m-%d")
            folder.mkdir(parents=True)
            (folder / "doc.pdf").write_text("x")
            self.assertEqual(self.client.post("/api/workspace/file-org",
                json={"filename": "doc.pdf", "org_id": "nope"}).status_code, 400)
            self.assertEqual(self.client.post("/api/workspace/file-org",
                json={"filename": "ghost.pdf", "org_id": ""}).status_code, 404)
        finally:
            config.WORKSPACE_DIR = orig

    def test_mail_dedupe_hides_notification_and_grafts_link(self):
        self.store.add_mails([
            make_mail(id="ORIG", subject="Server error report", body="Disk full on node 3",
                      received="2026-06-10 09:30:00"),
            make_mail(id="NOTE", subject="New ticket created", received="2026-06-10 09:40:00",
                      body="Ticket opened.\nSubject: Server error report\n"
                           "Body: Disk full on node 3\nSee https://zendesk.example/tickets/42"),
        ])
        # Off: both the original and the notification are present.
        off = {m["id"] for m in self.client.get("/api/mail").get_json()["mails"]}
        self.assertIn("NOTE", off)
        self.assertIn("ORIG", off)
        # On: the notification is hidden and its link is grafted onto the twin.
        on = self.client.get(
            "/api/mail?dedupe=1&dedupe_subject=New ticket created").get_json()["mails"]
        by_id = {m["id"]: m for m in on}
        self.assertNotIn("NOTE", by_id)
        self.assertIn("https://zendesk.example/tickets/42",
                      [l["url"] for l in by_id["ORIG"]["links"]])
        # The processed twin carries the 🧬 "deduped" tag on the same response.
        self.assertEqual(by_id["ORIG"]["tags"].get("deduped"), "recent")

    def test_mail_dedupe_grafted_link_respects_links_blacklist(self):
        self.store.add_mails([
            make_mail(id="ORIGB", subject="Server error report", body="Disk full on node 3",
                      received="2026-06-10 09:30:00"),
            make_mail(id="NOTEB", subject="New ticket created", received="2026-06-10 09:40:00",
                      body="Ticket opened.\nSubject: Server error report\n"
                           "Body: Disk full on node 3\nSee https://zendesk.example/tickets/42"),
        ])
        # With the notification's link on the Links blacklist, the grafted link is
        # hidden on the twin (same rule as a mail's own links).
        on = self.client.get(
            "/api/mail?dedupe=1&dedupe_subject=New ticket created&links_blacklist=zendesk"
        ).get_json()["mails"]
        by_id = {m["id"]: m for m in on}
        self.assertNotIn("NOTEB", by_id)
        self.assertEqual(by_id["ORIGB"]["links"], [])

    def test_mail_dedupe_tag_persists_and_records_once(self):
        self.store.add_mails([
            make_mail(id="O2", subject="Server error report", body="Disk full on node 3",
                      received="2026-06-10 09:30:00"),
            make_mail(id="N2", subject="New ticket created", received="2026-06-10 09:40:00",
                      body="Ticket opened.\nSubject: Server error report\n"
                           "Body: Disk full on node 3"),
        ])
        url = "/api/mail?dedupe=1&dedupe_subject=New ticket created"
        self.client.get(url)
        # Recorded server-side in the tag store, and shown even without the dedupe
        # toggle on a later request (a persistent tag, like downloaded/links).
        self.assertIn("deduped", self.app.extensions["tag_store"].tags_for("O2"))
        vm = next(m for m in self.client.get("/api/mail").get_json()["mails"] if m["id"] == "O2")
        self.assertEqual(vm["tags"].get("deduped"), "recent")
        # A second dedupe pass is idempotent (record-once), still "recent".
        on = {m["id"]: m for m in self.client.get(url).get_json()["mails"]}
        self.assertEqual(on["O2"]["tags"].get("deduped"), "recent")

    def test_download_reports_unknown_attachment(self):
        downloads = Path(self._tmpdir) / "downloads2"
        orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = downloads
        try:
            resp = self.client.post("/api/download", json={"items": [{"id": "nope", "index": 0}]})
            data = resp.get_json()
            self.assertEqual(data["saved"], [])
            self.assertTrue(data["errors"])
        finally:
            config.WORKSPACE_DIR = orig

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

    def test_post_tags_marks_and_unmarks(self):
        self.client.post("/api/tags", json={"ids": ["ID1"], "action": "marked"})
        vm = next(m for m in self.client.get("/api/mail").get_json()["mails"] if m["id"] == "ID1")
        self.assertEqual(vm["tags"].get("marked"), "recent")
        self.client.post("/api/tags", json={"ids": ["ID1"], "action": "marked", "op": "remove"})
        vm = next(m for m in self.client.get("/api/mail").get_json()["mails"] if m["id"] == "ID1")
        self.assertNotIn("marked", vm["tags"])

    def test_download_records_downloaded_tag(self):
        self.store.add_mails([make_mail(id="DT", attachments=[{"filename": "a.pdf"}])])
        orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = Path(self._tmpdir) / "dl"
        try:
            with mock.patch("mailfilter.outlook.fetch_attachment",
                            side_effect=lambda mid, idx: ("a.pdf", b"x")):
                self.client.post("/api/download", json={"items": [{"id": "DT", "index": 0}]})
            vm = next(m for m in self.client.get("/api/mail").get_json()["mails"] if m["id"] == "DT")
            self.assertEqual(vm["tags"].get("downloaded"), "recent")
        finally:
            config.WORKSPACE_DIR = orig

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

    # ----- search templates -----

    def test_templates_empty_initially(self):
        data = self.client.get("/api/templates").get_json()
        self.assertEqual(data, {"names": [], "templates": {}})

    def test_save_template_then_list(self):
        self.client.post("/api/templates", json={"name": "Work", "settings": {"main": "report"}})
        data = self.client.get("/api/templates").get_json()
        self.assertEqual(data["names"], ["Work"])
        self.assertEqual(data["templates"]["Work"]["main"], "report")

    def test_save_template_requires_name(self):
        resp = self.client.post("/api/templates", json={"settings": {"main": "x"}})
        self.assertEqual(resp.status_code, 400)

    def test_save_template_rejects_non_object(self):
        self.assertEqual(self.client.post("/api/templates", json=[1, 2]).status_code, 400)

    def test_delete_template(self):
        self.client.post("/api/templates", json={"name": "A", "settings": {}})
        data = self.client.delete("/api/templates/A").get_json()
        self.assertEqual(data["names"], [])

    def test_export_returns_png_image(self):
        self.client.post("/api/templates", json={"name": "A", "settings": {"main": "x"}})
        resp = self.client.post("/api/templates/export", json={"name": "A"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "image/png")
        self.assertTrue(resp.data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_export_unknown_template_is_404(self):
        resp = self.client.post("/api/templates/export", json={"name": "ghost"})
        self.assertEqual(resp.status_code, 404)

    def test_export_then_import_round_trips_the_template(self):
        self.client.post(
            "/api/templates",
            json={"name": "RoundTrip", "settings": {"main": "needle", "resources": True}},
        )
        png = self.client.post("/api/templates/export", json={"name": "RoundTrip"}).data
        # Drop it, then import the image back.
        self.client.delete("/api/templates/RoundTrip")
        self.assertEqual(self.client.get("/api/templates").get_json()["names"], [])

        resp = self.client.post(
            "/api/templates/import",
            data={"file": (io.BytesIO(png), "RoundTrip.png")},
            content_type="multipart/form-data",
        )
        data = resp.get_json()
        self.assertEqual(data["imported"], "RoundTrip")
        self.assertEqual(data["templates"]["RoundTrip"]["main"], "needle")
        self.assertIs(data["templates"]["RoundTrip"]["resources"], True)

    def test_import_rejects_non_template_image(self):
        resp = self.client.post(
            "/api/templates/import",
            data={"file": (io.BytesIO(b"not a png"), "x.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)

    def test_import_without_file_is_400(self):
        resp = self.client.post(
            "/api/templates/import", data={}, content_type="multipart/form-data"
        )
        self.assertEqual(resp.status_code, 400)

    # ----- organization categories -----

    def test_categories_are_seeded_on_the_first_run(self):
        data = self.client.get("/api/categories").get_json()
        self.assertEqual(data["categories"], list(config.ORG_DEFAULT_CATEGORIES))
        self.assertEqual(data["partner"], config.ORG_PARTNER_CATEGORY)

    def test_typing_a_new_category_on_an_org_creates_it(self):
        self.client.post("/api/organizations", json={"name": "Acme",
                                                     "category": "Reseller"})
        self.assertIn("Reseller",
                      self.client.get("/api/categories").get_json()["categories"])

    def test_an_existing_category_is_not_duplicated_by_a_different_case(self):
        self.client.post("/api/organizations", json={"name": "A", "category": "partner"})
        cats = self.client.get("/api/categories").get_json()["categories"]
        self.assertEqual(cats, list(config.ORG_DEFAULT_CATEGORIES))   # "Partner" stands

    def test_updating_an_org_with_a_new_category_creates_it(self):
        org = self.client.post("/api/organizations", json={"name": "Acme"}).get_json()
        self.client.put("/api/organizations/" + org["id"], json={"category": "Prospect"})
        self.assertIn("Prospect",
                      self.client.get("/api/categories").get_json()["categories"])

    def test_a_category_can_be_added_directly(self):
        data = self.client.post("/api/categories", json={"name": "Reseller"}).get_json()
        self.assertTrue(data["created"])
        self.assertIn("Reseller", data["categories"])
        # Adding it again is a no-op, not an error.
        self.assertFalse(
            self.client.post("/api/categories", json={"name": "Reseller"})
            .get_json()["created"])

    def test_the_category_list_can_be_replaced(self):
        data = self.client.put("/api/categories",
                               json={"categories": ["Only"]}).get_json()
        self.assertEqual(data["categories"], ["Only"])

    def test_category_routes_reject_a_non_object(self):
        self.assertEqual(self.client.post("/api/categories", json=[1]).status_code, 400)
        self.assertEqual(self.client.put("/api/categories", json=[1]).status_code, 400)

    # ----- customer management -----

    def test_organizations_empty_initially(self):
        self.assertEqual(self.client.get("/api/organizations").get_json()["organizations"], [])

    def test_create_organization_is_empty_by_default(self):
        org = self.client.post("/api/organizations", json={"name": "Acme"}).get_json()
        self.assertEqual(org["name"], "Acme")
        self.assertEqual(org["domains"], [])
        self.assertEqual(org["contacts"], [])
        self.assertEqual(org["category"], "")

    def test_create_organization_rejects_non_object(self):
        self.assertEqual(self.client.post("/api/organizations", json=[1]).status_code, 400)

    def test_update_unknown_organization_is_404(self):
        resp = self.client.put("/api/organizations/nope", json={"name": "X"})
        self.assertEqual(resp.status_code, 404)

    def test_delete_organization(self):
        oid = self.client.post("/api/organizations", json={"name": "Gone"}).get_json()["id"]
        data = self.client.delete(f"/api/organizations/{oid}").get_json()
        self.assertEqual(data["organizations"], [])

    def test_contacts_directory_aggregates_seeded_mail(self):
        # The two seeded mails are both from alice@example.com (make_mail default).
        contacts = self.client.get("/api/contacts").get_json()["contacts"]
        alice = next(c for c in contacts if c["email"] == "alice@example.com")
        self.assertEqual(alice["count"], 2)
        self.assertIsNone(alice["member_org_id"])
        self.assertIsNone(alice["rep_org_id"])

    def test_domain_mapping_resolves_member(self):
        oid = self.client.post("/api/organizations", json={"name": "Example"}).get_json()["id"]
        self.client.put(f"/api/organizations/{oid}", json={
            "category": "Customer",
            "domains": [{"domain": "example.com", "role": "member"}],
        })
        contacts = {c["email"]: c for c in self.client.get("/api/contacts").get_json()["contacts"]}
        self.assertEqual(contacts["alice@example.com"]["member_org_id"], oid)
        self.assertEqual(contacts["alice@example.com"]["member_category"], "Customer")
        self.assertIsNone(contacts["alice@example.com"]["rep_org_id"])

    def test_representative_pin_coexists_with_member_base(self):
        # acme owns example.com (members); beta pins alice as a representative.
        # Both axes resolve: alice is a Member of acme AND a Representative of beta.
        acme = self.client.post("/api/organizations", json={"name": "Acme"}).get_json()["id"]
        self.client.post(f"/api/organizations/{acme}/domains",
                         json={"domain": "example.com", "role": "member"})
        beta = self.client.post("/api/organizations", json={"name": "Beta"}).get_json()["id"]
        resp = self.client.post("/api/contacts/assign",
                                json={"email": "alice@example.com", "org_id": beta, "role": "representative"})
        self.assertEqual(resp.status_code, 200)
        contacts = {c["email"]: c for c in self.client.get("/api/contacts").get_json()["contacts"]}
        self.assertEqual(contacts["alice@example.com"]["member_org_id"], acme)
        self.assertEqual(contacts["alice@example.com"]["rep_org_id"], beta)
        self.assertTrue(contacts["alice@example.com"]["rep_pinned"])

    def test_assign_representative_requires_base(self):
        # No base membership for alice yet -> representative assignment is rejected.
        oid = self.client.post("/api/organizations", json={"name": "Beta"}).get_json()["id"]
        resp = self.client.post("/api/contacts/assign",
                                json={"email": "alice@example.com", "org_id": oid, "role": "representative"})
        self.assertEqual(resp.status_code, 409)

    def test_assign_unknown_org_is_404(self):
        resp = self.client.post("/api/contacts/assign",
                                json={"email": "x@y.com", "org_id": "nope", "role": "member"})
        self.assertEqual(resp.status_code, 404)

    def test_unassign_clears_pin(self):
        oid = self.client.post("/api/organizations", json={"name": "Acme"}).get_json()["id"]
        self.client.post("/api/contacts/assign",
                         json={"email": "alice@example.com", "org_id": oid, "role": "member"})
        self.client.post("/api/contacts/unassign", json={"email": "alice@example.com"})
        contacts = {c["email"]: c for c in self.client.get("/api/contacts").get_json()["contacts"]}
        self.assertIsNone(contacts["alice@example.com"]["member_org_id"])
        self.assertIsNone(contacts["alice@example.com"]["rep_org_id"])

    def test_assign_rejects_non_object(self):
        self.assertEqual(self.client.post("/api/contacts/assign", json=[1]).status_code, 400)

    def test_add_domain_makes_everyone_a_member(self):
        # Dragging the example.com domain onto an org maps both seeded senders.
        oid = self.client.post("/api/organizations", json={"name": "Example"}).get_json()["id"]
        org = self.client.post(f"/api/organizations/{oid}/domains",
                               json={"domain": "example.com", "role": "member"}).get_json()
        self.assertEqual(org["domains"], [{"domain": "example.com", "role": "member"}])
        contacts = {c["email"]: c for c in self.client.get("/api/contacts").get_json()["contacts"]}
        self.assertEqual(contacts["alice@example.com"]["member_org_id"], oid)

    def test_add_domain_unknown_org_is_404(self):
        resp = self.client.post("/api/organizations/nope/domains", json={"domain": "x.com"})
        self.assertEqual(resp.status_code, 404)

    def test_add_domain_rejects_non_object(self):
        oid = self.client.post("/api/organizations", json={"name": "A"}).get_json()["id"]
        self.assertEqual(self.client.post(f"/api/organizations/{oid}/domains", json=[1]).status_code, 400)


if __name__ == "__main__":
    unittest.main()
