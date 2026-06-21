"""HTTP-layer tests for Bulk Compose: compose-template CRUD, preview, and
create-drafts, all in BULK_MOCK_MODE so no Outlook is needed.

The app is built against throwaway caches and mock dirs so the real project
files are never touched.
"""

import json
import shutil
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import openpyxl

import config
from mailfilter import create_app


def _xlsx(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


SHARED_INBOX = [
    {"id": "S1", "store_id": "ST", "subject": "Quarterly invoice request",
     "sender": "Alice Tan", "sender_email": "alice@acme.com",
     "received": "2026-06-20 09:15:00",
     "recipient_emails": ["shared.services@example.com"], "cc_emails": []},
]


class BulkRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        tmp = Path(self._tmp)
        self._orig = {k: getattr(config, k) for k in (
            "CACHE_FILE", "SETTINGS_FILE", "TAGS_FILE", "TEMPLATES_DIR",
            "AUTOMATIONS_FILE", "CUSTOMERS_FILE", "COMPOSE_TEMPLATES_FILE",
            "MOCK_SHARED_INBOX_FILE", "MOCK_DRAFTS_DIR", "FILE_SERVER_DIR",
            "WORKSPACE_DIR", "BULK_MOCK_MODE")}
        config.CACHE_FILE = tmp / "cache.json"
        config.SETTINGS_FILE = tmp / "settings.json"
        config.TAGS_FILE = tmp / "tags.json"
        config.TEMPLATES_DIR = tmp / "search_templates"
        config.AUTOMATIONS_FILE = tmp / "automations.json"
        config.CUSTOMERS_FILE = tmp / "customers.json"
        config.COMPOSE_TEMPLATES_FILE = tmp / "compose.json"
        config.MOCK_SHARED_INBOX_FILE = tmp / "shared_inbox.json"
        config.MOCK_DRAFTS_DIR = tmp / "drafts"
        config.FILE_SERVER_DIR = tmp / "fileserver"
        config.WORKSPACE_DIR = tmp / "workspace"
        config.BULK_MOCK_MODE = True

        config.MOCK_SHARED_INBOX_FILE.write_text(json.dumps(SHARED_INBOX), encoding="utf-8")
        config.FILE_SERVER_DIR.mkdir(parents=True, exist_ok=True)
        (config.FILE_SERVER_DIR / "Invoice.pdf").write_text("data")

        self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(config, k, v)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_template(self, body="Dear {{ sender.first_name }},\n"
                       "{% if row.uses_ftp %}Link: {{ ftp_link(row.file_name) }}"
                       "{% else %}Attached.{% endif %}",
                       attachment_expr=""):
        resp = self.client.post("/api/compose-templates", json={
            "name": "Standard", "body": body, "attachment_expr": attachment_expr})
        return resp.get_json()

    def _upload(self, rows, template_id, indices=None, path="/api/bulk/preview"):
        data = {"file": (BytesIO(_xlsx(rows)), "sheet.xlsx"),
                "template_id": template_id}
        if indices is not None:
            data["indices"] = json.dumps(indices)
        return self.client.post(path, data=data, content_type="multipart/form-data")

    # ----- template CRUD -----

    def test_template_crud(self):
        t = self._make_template()
        self.assertTrue(t["id"])
        listing = self.client.get("/api/compose-templates").get_json()
        self.assertEqual(len(listing["templates"]), 1)
        self.assertTrue(listing["mock_mode"])

        upd = self.client.put(f"/api/compose-templates/{t['id']}",
                              json={"name": "Renamed"}).get_json()
        self.assertEqual(upd["name"], "Renamed")

        after = self.client.delete(f"/api/compose-templates/{t['id']}").get_json()
        self.assertEqual(after["templates"], [])

    # ----- preview -----

    def test_preview_matches_and_renders(self):
        t = self._make_template()
        rows = [["Subject", "Datetime", "Sender", "File Name", "FTP"],
                ["Quarterly invoice request", "2026-06-20 09:15:00",
                 "alice@acme.com", "Invoice.pdf", "No"]]
        out = self._upload(rows, t["id"]).get_json()
        self.assertEqual(out["summary"]["ready"], 1)
        plan = out["plans"][0]
        self.assertEqual(plan["to"], ["alice@acme.com"])
        self.assertIn(config.SHARED_MAILBOX_ADDRESS, plan["cc"])
        self.assertIn("Dear Alice,", plan["body"])

    def test_preview_writes_no_drafts(self):
        t = self._make_template()
        rows = [["Subject", "Datetime", "Sender", "File Name", "FTP"],
                ["Quarterly invoice request", "2026-06-20 09:15:00",
                 "alice@acme.com", "Invoice.pdf", "No"]]
        self._upload(rows, t["id"])
        self.assertFalse(config.MOCK_DRAFTS_DIR.exists()
                         and any(config.MOCK_DRAFTS_DIR.iterdir()))

    def test_preview_unmatched_row_blocked(self):
        t = self._make_template()
        rows = [["Subject", "Datetime", "Sender"],
                ["No such mail", "", ""]]
        out = self._upload(rows, t["id"]).get_json()
        self.assertEqual(out["summary"]["ready"], 0)
        self.assertEqual(out["plans"][0]["status"], "blocked")

    def test_preview_missing_file_400(self):
        t = self._make_template()
        resp = self.client.post("/api/bulk/preview",
                                data={"template_id": t["id"]},
                                content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 400)

    def test_preview_unknown_template_400(self):
        rows = [["Subject"], ["x"]]
        resp = self._upload(rows, "nope")
        self.assertEqual(resp.status_code, 400)

    def test_preview_bad_xlsx_400(self):
        t = self._make_template()
        resp = self.client.post(
            "/api/bulk/preview",
            data={"file": (BytesIO(b"not a workbook"), "x.xlsx"), "template_id": t["id"]},
            content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 400)

    # ----- create-drafts -----

    def test_create_drafts_writes_drafts_and_audit(self):
        t = self._make_template()
        rows = [["Subject", "Datetime", "Sender", "File Name", "FTP"],
                ["Quarterly invoice request", "2026-06-20 09:15:00",
                 "alice@acme.com", "Invoice.pdf", "No"]]
        out = self._upload(rows, t["id"], path="/api/bulk/create-drafts").get_json()
        self.assertEqual(out["created"], 1)
        drafts = list(config.MOCK_DRAFTS_DIR.glob("*.json"))
        self.assertEqual(len(drafts), 1)
        doc = json.loads(drafts[0].read_text(encoding="utf-8"))
        self.assertEqual(doc["to"], ["alice@acme.com"])
        self.assertIn("never sent", doc["note"])
        # Audit CSV written into the dated workspace folder.
        self.assertTrue(out["audit"].endswith(".csv"))
        self.assertTrue(Path(out["audit"]).exists())

    def test_create_drafts_respects_selected_indices(self):
        t = self._make_template()
        rows = [["Subject", "Datetime", "Sender", "File Name", "FTP"],
                ["Quarterly invoice request", "2026-06-20 09:15:00",
                 "alice@acme.com", "Invoice.pdf", "No"]]
        # Select an index that isn't the matched row -> nothing created.
        out = self._upload(rows, t["id"], indices=[99],
                           path="/api/bulk/create-drafts").get_json()
        self.assertEqual(out["created"], 0)
        self.assertFalse(config.MOCK_DRAFTS_DIR.exists()
                         and any(config.MOCK_DRAFTS_DIR.glob("*.json")))


if __name__ == "__main__":
    unittest.main()
