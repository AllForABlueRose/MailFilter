"""Tests for mailfilter.experimental_store: defaults, sanitizing merge, persistence."""

import tempfile
import unittest
from pathlib import Path

from mailfilter import crypto
from mailfilter.experimental_store import DEFAULTS, ExperimentalStore, coerce


def _store():
    return ExperimentalStore(Path(tempfile.mkdtemp()) / "experimental.json")


class DefaultsTests(unittest.TestCase):
    def test_snapshot_defaults_all_off(self):
        snap = _store().snapshot()
        self.assertEqual(snap, DEFAULTS)
        self.assertTrue(all(v is False for v in snap.values()))

    def test_snapshot_is_a_copy(self):
        store = _store()
        snap = store.snapshot()
        snap["passwords"] = True
        self.assertFalse(store.snapshot()["passwords"])


class CoerceTests(unittest.TestCase):
    def test_unknown_keys_dropped(self):
        out = coerce({"passwords": True, "bogus": True})
        self.assertNotIn("bogus", out)
        self.assertTrue(out["passwords"])

    def test_values_coerced_to_bool(self):
        out = coerce({"normalize_width": "yes", "passwords": 0})
        self.assertIs(out["normalize_width"], True)
        self.assertIs(out["passwords"], False)

    def test_non_dict_returns_base_defaults(self):
        self.assertEqual(coerce("nonsense"), DEFAULTS)


class UpdateTests(unittest.TestCase):
    def test_partial_update_merges(self):
        store = _store()
        store.update({"passwords": True})
        store.update({"normalize_width": True})  # passwords must be retained
        snap = store.snapshot()
        self.assertTrue(snap["passwords"])
        self.assertTrue(snap["normalize_width"])


class PersistenceTests(unittest.TestCase):
    def test_encoded_on_disk_and_reloads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "experimental.json"
            store = ExperimentalStore(path)
            store.update({"normalize_width": True})

            data = path.read_bytes()
            self.assertTrue(data.startswith(crypto.MAGIC))  # encoded at rest

            reloaded = ExperimentalStore(path)
            reloaded.load()
            self.assertTrue(reloaded.snapshot()["normalize_width"])

    def test_load_missing_file_keeps_defaults(self):
        store = _store()
        store.load()
        self.assertEqual(store.snapshot(), DEFAULTS)


if __name__ == "__main__":
    unittest.main()
