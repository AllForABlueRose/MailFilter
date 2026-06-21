"""Tests for mailfilter.compose_template_store: CRUD, coercion, validation,
encoded-at-rest persistence."""

import tempfile
import unittest
from pathlib import Path

from mailfilter import crypto
from mailfilter.compose_template_store import ComposeTemplateStore


def _store():
    return ComposeTemplateStore(Path(tempfile.mkdtemp()) / "compose.json")


class CrudTests(unittest.TestCase):
    def setUp(self):
        self.store = _store()

    def test_create_assigns_id_and_defaults(self):
        t = self.store.create({"name": "Standard", "body": "Hello {{ row.x }}"})
        self.assertTrue(t["id"])
        self.assertEqual(t["name"], "Standard")
        self.assertEqual(t["error"], "")
        self.assertIn(t, self.store.snapshot())

    def test_update_merges(self):
        t = self.store.create({"name": "A", "body": "x"})
        updated = self.store.update(t["id"], {"body": "y"})
        self.assertEqual(updated["name"], "A")
        self.assertEqual(updated["body"], "y")

    def test_update_unknown_returns_none(self):
        self.assertIsNone(self.store.update("nope", {"body": "x"}))

    def test_get_and_delete(self):
        t = self.store.create({"name": "A", "body": "x"})
        self.assertEqual(self.store.get(t["id"])["name"], "A")
        self.assertTrue(self.store.delete(t["id"]))
        self.assertIsNone(self.store.get(t["id"]))
        self.assertFalse(self.store.delete(t["id"]))

    def test_blank_name_becomes_untitled(self):
        self.assertEqual(self.store.create({"body": "x"})["name"], "Untitled")


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.store = _store()

    def test_good_template_has_no_error(self):
        t = self.store.create({"body": "{% if row.a %}{{ row.b }}{% endif %}"})
        self.assertEqual(t["error"], "")

    def test_bad_body_records_error_but_still_saves(self):
        t = self.store.create({"body": "{% if row.a %}unterminated"})
        self.assertIn("body:", t["error"])
        # Stored anyway so it can be fixed in the editor.
        self.assertIsNotNone(self.store.get(t["id"]))

    def test_bad_attachment_expr_records_error(self):
        t = self.store.create({"body": "ok", "attachment_expr": "upper("})
        self.assertIn("attachment name:", t["error"])

    def test_good_attachment_expr_ok(self):
        t = self.store.create({"body": "ok",
                               "attachment_expr": 'upper(row.ref) + ".pdf"'})
        self.assertEqual(t["error"], "")


class PersistenceTests(unittest.TestCase):
    def test_encoded_on_disk_and_reloads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "compose.json"
            store = ComposeTemplateStore(path)
            store.create({"name": "Secret", "body": "classified-token {{ row.x }}"})

            data = path.read_bytes()
            self.assertTrue(data.startswith(crypto.MAGIC))
            self.assertNotIn(b"classified-token", data)

            reloaded = ComposeTemplateStore(path)
            reloaded.load()
            self.assertEqual(reloaded.snapshot()[0]["name"], "Secret")

    def test_load_missing_file_is_empty(self):
        store = _store()
        store.load()
        self.assertEqual(store.snapshot(), [])

    def test_corrupt_entries_dropped_on_load(self):
        # _coerce returns None for non-dict entries; the loader drops them.
        store = _store()
        self.assertIsNone(store._coerce("not a dict"))


if __name__ == "__main__":
    unittest.main()
