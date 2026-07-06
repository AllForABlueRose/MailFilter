"""Unit tests for calendar_ops: pin_file (copy to limbo + carry org metadata) and
materialize_due (move into today's folder, recreate the manifest, idempotent).
Uses a temp WORKSPACE_DIR + CALENDAR_PINS_FILE; stdlib only."""

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import config
from mailfilter import calendar_ops, workspace_manifest
from mailfilter.calendar_store import CalendarStore

_ISOLATED = ("WORKSPACE_DIR", "CALENDAR_PINS_FILE")


class CalendarOpsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig = {n: getattr(config, n) for n in _ISOLATED}
        config.WORKSPACE_DIR = Path(self._tmpdir) / "workspace"
        config.CALENDAR_PINS_FILE = Path(self._tmpdir) / "calendar_pins.json"
        self.store = CalendarStore(config.CALENDAR_PINS_FILE)
        self.store.load()
        self.today = datetime.now().strftime("%Y-%m-%d")

    def tearDown(self):
        for n, v in self._orig.items():
            setattr(config, n, v)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _seed(self, name="invoice.pdf", org=True):
        folder = calendar_ops.today_folder()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"DATA")
        if org:
            workspace_manifest.record(str(folder), name,
                {"org_id": "o1", "org_name": "Acme Corp", "mail_id": "m1"})
        return folder

    def test_pin_file_copies_to_limbo_and_records_org(self):
        self._seed()
        res = calendar_ops.pin_file(self.store, self.today, "invoice.pdf", "why")
        self.assertTrue(res["ok"])
        pin = res["pin"]
        self.assertEqual(pin["org_name"], "Acme Corp")
        self.assertEqual(pin["mail_id"], "m1")
        self.assertEqual(pin["description"], "why")
        limbo = calendar_ops.limbo_folder() / pin["limbo_name"]
        self.assertTrue(limbo.is_file())

    def test_pin_file_missing_file(self):
        self._seed()
        res = calendar_ops.pin_file(self.store, self.today, "nope.pdf")
        self.assertFalse(res["ok"])

    def test_pin_file_invalid_date(self):
        self._seed()
        res = calendar_ops.pin_file(self.store, "not-a-date", "invoice.pdf")
        self.assertFalse(res["ok"])

    def test_pin_file_rejects_path_traversal(self):
        self._seed()
        # A path-y filename is reduced to its basename, which isn't in the folder.
        res = calendar_ops.pin_file(self.store, self.today, "../secret.pdf")
        self.assertFalse(res["ok"])

    def test_materialize_due_moves_and_records_manifest(self):
        folder = self._seed()
        pin = calendar_ops.pin_file(self.store, self.today, "invoice.pdf")["pin"]
        (folder / "invoice.pdf").unlink()  # remove the source so the move is visible
        moved = calendar_ops.materialize_due(self.store)
        self.assertEqual(moved, ["invoice.pdf"])
        self.assertTrue((folder / "invoice.pdf").is_file())
        self.assertEqual(
            workspace_manifest.lookup(str(folder), "invoice.pdf")["org_name"], "Acme Corp")
        self.assertTrue(self.store.get(pin["id"])["materialized"])

    def test_materialize_due_idempotent(self):
        self._seed()
        calendar_ops.pin_file(self.store, self.today, "invoice.pdf")
        calendar_ops.materialize_due(self.store)
        self.assertEqual(calendar_ops.materialize_due(self.store), [])

    def test_materialize_skips_other_dates(self):
        self._seed()
        future = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
        pin = calendar_ops.pin_file(self.store, future, "invoice.pdf")["pin"]
        self.assertEqual(calendar_ops.materialize_due(self.store), [])
        self.assertFalse(self.store.get(pin["id"])["materialized"])

    def test_materialize_missing_limbo_marks_done(self):
        self._seed()
        pin = calendar_ops.pin_file(self.store, self.today, "invoice.pdf")["pin"]
        (calendar_ops.limbo_folder() / pin["limbo_name"]).unlink()
        self.assertEqual(calendar_ops.materialize_due(self.store), [])
        self.assertTrue(self.store.get(pin["id"])["materialized"])

    def test_remove_pin_deletes_limbo_copy(self):
        self._seed()
        pin = calendar_ops.pin_file(self.store, self.today, "invoice.pdf")["pin"]
        limbo = calendar_ops.limbo_folder() / pin["limbo_name"]
        self.assertTrue(calendar_ops.remove_pin(self.store, pin["id"]))
        self.assertFalse(limbo.exists())
        self.assertIsNone(self.store.get(pin["id"]))


if __name__ == "__main__":
    unittest.main()
