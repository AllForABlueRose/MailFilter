"""Unit tests for CalendarStore: coercion, mutation, and the encoded-at-rest
round-trip. Uses a tempfile pins file (never the real calendar_pins.json)."""

import tempfile
import unittest
from pathlib import Path

import config
from mailfilter import crypto
from mailfilter.calendar_store import CalendarStore


class CalendarStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._file = Path(self._tmpdir) / "calendar_pins.json"
        self.store = CalendarStore(self._file)
        self.store.load()

    def _add(self, **over):
        base = {"date": "2026-08-01", "filename": "a.pdf", "limbo_name": "a.pdf",
                "org_id": "o1", "org_name": "Acme", "mail_id": "m1"}
        base.update(over)
        return self.store.add(base)

    def test_coerce_keeps_known_fields_only(self):
        pin = self._add(bogus="x")
        self.assertNotIn("bogus", pin)
        self.assertEqual(pin["date"], "2026-08-01")
        self.assertEqual(pin["org_name"], "Acme")
        self.assertFalse(pin["materialized"])

    def test_add_mints_id_and_created(self):
        pin = self._add()
        self.assertTrue(pin["id"])
        self.assertTrue(pin["created"])

    def test_coerce_caps_description(self):
        pin = self._add(description="x" * (config.CALENDAR_PIN_DESCRIPTION_MAX + 50))
        self.assertEqual(len(pin["description"]), config.CALENDAR_PIN_DESCRIPTION_MAX)

    def test_get_and_remove(self):
        pin = self._add()
        self.assertEqual(self.store.get(pin["id"])["filename"], "a.pdf")
        self.assertTrue(self.store.remove(pin["id"]))
        self.assertIsNone(self.store.get(pin["id"]))
        self.assertFalse(self.store.remove(pin["id"]))

    def test_mark_materialized(self):
        pin = self._add()
        updated = self.store.mark_materialized(pin["id"], "/ws/2026-08-01")
        self.assertTrue(updated["materialized"])
        self.assertEqual(updated["materialized_folder"], "/ws/2026-08-01")
        self.assertIsNone(self.store.mark_materialized("nope", "/x"))

    def test_snapshot_is_creation_order_and_copies(self):
        a = self._add(filename="a.pdf")
        b = self._add(filename="b.pdf")
        snap = self.store.snapshot()
        self.assertEqual([p["id"] for p in snap], [a["id"], b["id"]])
        snap[0]["filename"] = "mutated"
        self.assertEqual(self.store.get(a["id"])["filename"], "a.pdf")

    def test_persist_encoded_and_reload(self):
        pin = self._add()
        raw = self._file.read_bytes()
        self.assertTrue(raw.startswith(crypto.MAGIC))  # encoded at rest
        fresh = CalendarStore(self._file)
        fresh.load()
        self.assertEqual(fresh.get(pin["id"])["filename"], "a.pdf")

    def test_load_missing_file_stays_empty(self):
        fresh = CalendarStore(Path(self._tmpdir) / "absent.json")
        fresh.load()
        self.assertEqual(fresh.snapshot(), [])


if __name__ == "__main__":
    unittest.main()
