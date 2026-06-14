"""Tests for mailfilter.tag_store: recording, recency, and persistence."""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from mailfilter import crypto
from mailfilter.tag_store import RECENT_DAYS, TagStore

_TS = "%Y-%m-%d %H:%M:%S"


def _store():
    return TagStore(Path(tempfile.mkdtemp()) / "tags.json")


class RecordTests(unittest.TestCase):
    def test_record_marks_recent(self):
        store = _store()
        store.record("m1", "downloaded")
        self.assertEqual(store.tags_for("m1"), {"downloaded": "recent"})

    def test_unknown_action_ignored(self):
        store = _store()
        store.record("m1", "bogus")
        self.assertEqual(store.tags_for("m1"), {})

    def test_unknown_mail_has_no_tags(self):
        self.assertEqual(_store().tags_for("nope"), {})

    def test_both_actions_coexist(self):
        store = _store()
        store.record("m1", "downloaded")
        store.record("m1", "links")
        self.assertEqual(store.tags_for("m1"), {"downloaded": "recent", "links": "recent"})


class RemoveTests(unittest.TestCase):
    def test_remove_clears_one_action(self):
        store = _store()
        store.record("m1", "marked")
        store.record("m1", "downloaded")
        store.remove("m1", "marked")
        self.assertEqual(store.tags_for("m1"), {"downloaded": "recent"})

    def test_remove_last_action_drops_mail(self):
        store = _store()
        store.record("m1", "marked")
        store.remove("m1", "marked")
        self.assertEqual(store.tags_for("m1"), {})
        self.assertNotIn("m1", store._tags)

    def test_remove_unknown_action_is_noop(self):
        store = _store()
        store.record("m1", "marked")
        store.remove("m1", "bogus")
        store.remove("m1", "downloaded")  # never recorded
        self.assertEqual(store.tags_for("m1"), {"marked": "recent"})


class RecencyTests(unittest.TestCase):
    def test_old_tag_is_reported_as_old(self):
        store = _store()
        old = (datetime.now() - timedelta(days=RECENT_DAYS + 1)).strftime(_TS)
        new = datetime.now().strftime(_TS)
        store._tags = {"m1": {"downloaded": old, "links": new}}
        result = store.tags_for("m1")
        self.assertEqual(result["downloaded"], "old")
        self.assertEqual(result["links"], "recent")

    def test_marked_greys_after_recent_window(self):
        store = _store()
        old = (datetime.now() - timedelta(days=RECENT_DAYS + 1)).strftime(_TS)
        store._tags = {"m1": {"marked": old}}
        self.assertEqual(store.tags_for("m1"), {"marked": "old"})


class PersistenceTests(unittest.TestCase):
    def test_encoded_on_disk_and_reloads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tags.json"
            store = TagStore(path)
            store.record("secret-mail", "downloaded")

            data = path.read_bytes()
            self.assertTrue(data.startswith(crypto.MAGIC))
            self.assertNotIn(b"secret-mail", data)

            reloaded = TagStore(path)
            reloaded.load()
            self.assertEqual(reloaded.tags_for("secret-mail"), {"downloaded": "recent"})

    def test_load_missing_file_is_noop(self):
        store = _store()
        store.load()
        self.assertEqual(store.tags_for("x"), {})

    def test_load_corrupt_file_keeps_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tags.json"
            path.write_bytes(b"not decodable")
            store = TagStore(path)
            store.load()
            self.assertEqual(store.tags_for("x"), {})


if __name__ == "__main__":
    unittest.main()
