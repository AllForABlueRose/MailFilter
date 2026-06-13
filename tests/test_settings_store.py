"""Tests for mailfilter.settings_store: defaults, sanitizing merge, persistence."""

import tempfile
import unittest
from pathlib import Path

from mailfilter import crypto
from mailfilter.settings_store import DEFAULTS, MAX_LEN, SettingsStore


def _store():
    return SettingsStore(Path(tempfile.mkdtemp()) / "settings.json")


class DefaultsTests(unittest.TestCase):
    def test_snapshot_defaults_before_any_save(self):
        self.assertEqual(_store().snapshot(), DEFAULTS)

    def test_snapshot_is_a_copy(self):
        store = _store()
        snap = store.snapshot()
        snap["main"] = "mutated"
        self.assertEqual(store.snapshot()["main"], "")


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.store = _store()

    def test_update_persists_known_fields(self):
        out = self.store.update({"main": "server,error", "resources": True})
        self.assertEqual(out["main"], "server,error")
        self.assertTrue(out["resources"])
        self.assertEqual(self.store.snapshot()["main"], "server,error")

    def test_unknown_keys_are_ignored(self):
        out = self.store.update({"bogus": "x", "main": "ok"})
        self.assertNotIn("bogus", out)
        self.assertEqual(out["main"], "ok")

    def test_resources_coerced_to_bool(self):
        self.assertIs(self.store.update({"resources": "yes"})["resources"], True)
        self.assertIs(self.store.update({"resources": ""})["resources"], False)

    def test_partial_update_merges_with_existing(self):
        self.store.update({"main": "a", "sender": "bob"})
        self.store.update({"main": "b"})  # sender must be retained
        snap = self.store.snapshot()
        self.assertEqual(snap["main"], "b")
        self.assertEqual(snap["sender"], "bob")

    def test_string_length_is_capped(self):
        out = self.store.update({"main": "x" * (MAX_LEN * 4)})
        self.assertEqual(len(out["main"]), MAX_LEN)


class PersistenceTests(unittest.TestCase):
    def test_encoded_on_disk_and_reloads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "settings.json"
            store = SettingsStore(path)
            store.update({"main": "secret-keyword", "resources": True})

            data = path.read_bytes()
            # Encoded at rest (not bare plaintext) like the mail cache.
            self.assertTrue(data.startswith(crypto.MAGIC))
            self.assertNotIn(b"secret-keyword", data)

            reloaded = SettingsStore(path)
            reloaded.load()
            self.assertEqual(reloaded.snapshot()["main"], "secret-keyword")
            self.assertTrue(reloaded.snapshot()["resources"])

    def test_load_missing_file_keeps_defaults(self):
        store = _store()
        store.load()
        self.assertEqual(store.snapshot(), DEFAULTS)

    def test_load_corrupt_file_keeps_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "settings.json"
            path.write_bytes(b"not decodable json")
            store = SettingsStore(path)
            store.load()
            self.assertEqual(store.snapshot(), DEFAULTS)


if __name__ == "__main__":
    unittest.main()
