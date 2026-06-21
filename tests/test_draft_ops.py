"""Tests for the Bulk Compose mock seam: shared_mailbox.read_inbox and
draft_ops.create_drafts in BULK_MOCK_MODE (the live COM paths need Outlook and
are exercised on that host)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from mailfilter import draft_ops, shared_mailbox


class SharedMailboxMockTests(unittest.TestCase):
    def test_reads_mock_inbox_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "inbox.json"
            path.write_text(json.dumps([
                {"id": "X1", "subject": "Hi", "sender_email": "a@b.com",
                 "received": "2026-06-20 10:00:00", "recipient_emails": ["s@x.com"]},
            ]), encoding="utf-8")
            with mock.patch.object(config, "BULK_MOCK_MODE", True), \
                    mock.patch.object(config, "MOCK_SHARED_INBOX_FILE", path):
                mails = shared_mailbox.read_inbox()
        self.assertEqual(len(mails), 1)
        self.assertEqual(mails[0]["id"], "X1")
        self.assertEqual(mails[0]["cc_emails"], [])  # normalized in

    def test_missing_mock_file_is_empty(self):
        with mock.patch.object(config, "BULK_MOCK_MODE", True), \
                mock.patch.object(config, "MOCK_SHARED_INBOX_FILE",
                                  Path(tempfile.mkdtemp()) / "nope.json"):
            self.assertEqual(shared_mailbox.read_inbox(), [])

    def test_read_limit_applied(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "inbox.json"
            path.write_text(json.dumps([{"id": f"X{i}"} for i in range(10)]),
                            encoding="utf-8")
            with mock.patch.object(config, "BULK_MOCK_MODE", True), \
                    mock.patch.object(config, "MOCK_SHARED_INBOX_FILE", path), \
                    mock.patch.object(config, "BULK_SHARED_READ_LIMIT", 4):
                self.assertEqual(len(shared_mailbox.read_inbox()), 4)


def _ready_plan(idx=0, **over):
    plan = {
        "row_index": idx, "status": "ready", "mail_id": "M1", "store_id": "S1",
        "to": ["alice@acme.com"], "cc": ["shared.services@example.com"],
        "subject": "RE: Quarterly invoice", "body": "Dear Alice,\nAttached.",
        "uses_ftp": False, "ftp_link": "",
        "attachment": {"name": "inv.pdf", "path": "/srv/inv.pdf", "exists": True},
    }
    plan.update(over)
    return plan


class DraftOpsMockTests(unittest.TestCase):
    def test_creates_one_json_per_ready_plan(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(config, "BULK_MOCK_MODE", True), \
                    mock.patch.object(config, "MOCK_DRAFTS_DIR", d):
                results = draft_ops.create_drafts([_ready_plan(0), _ready_plan(1)])
            created = [r for r in results if r["status"] == "created"]
            self.assertEqual(len(created), 2)
            files = list(Path(d).glob("*.json"))
            self.assertEqual(len(files), 2)
            doc = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(doc["from"], config.SHARED_MAILBOX_ADDRESS)
            self.assertIn("never sent", doc["note"])

    def test_blocked_plans_are_skipped_not_created(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(config, "BULK_MOCK_MODE", True), \
                    mock.patch.object(config, "MOCK_DRAFTS_DIR", d):
                results = draft_ops.create_drafts([
                    _ready_plan(0),
                    {"row_index": 1, "status": "blocked"},
                ])
            statuses = {r["row_index"]: r["status"] for r in results}
            self.assertEqual(statuses[0], "created")
            self.assertEqual(statuses[1], "skipped")
            self.assertEqual(len(list(Path(d).glob("*.json"))), 1)

    def test_ftp_plan_records_no_attachment(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(config, "BULK_MOCK_MODE", True), \
                    mock.patch.object(config, "MOCK_DRAFTS_DIR", d):
                draft_ops.create_drafts([
                    _ready_plan(0, uses_ftp=True, ftp_link="ftp://x/y.pdf"),
                ])
            doc = json.loads(next(Path(d).glob("*.json")).read_text(encoding="utf-8"))
            self.assertIsNone(doc["attachment"])
            self.assertEqual(doc["ftp_link"], "ftp://x/y.pdf")


if __name__ == "__main__":
    unittest.main()
