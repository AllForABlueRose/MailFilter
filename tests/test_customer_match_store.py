"""Tests for mailfilter.customer_match_store: the Suspected Customers List."""

import tempfile
import unittest
from pathlib import Path

import config
from mailfilter import crypto
from mailfilter.customer_match_store import CustomerMatchStore, coerce


def _store():
    return CustomerMatchStore(Path(tempfile.mkdtemp()) / "customer_match.json")


class CoerceTests(unittest.TestCase):
    def test_accepts_list_dict_and_string(self):
        self.assertEqual(coerce(["A", "B"]), ["A", "B"])
        self.assertEqual(coerce({"customers": ["A", "B"]}), ["A", "B"])
        self.assertEqual(coerce("A\nB"), ["A", "B"])

    def test_trims_and_drops_blanks(self):
        self.assertEqual(coerce(["  Acme  ", "", "   ", "Globex"]), ["Acme", "Globex"])

    def test_dedupes_case_insensitively_first_wins(self):
        self.assertEqual(coerce(["Acme", "acme", "ACME"]), ["Acme"])

    def test_caps_name_length(self):
        long = "x" * (config.CUSTOMER_MATCH_NAME_MAX * 2)
        self.assertEqual(len(coerce([long])[0]), config.CUSTOMER_MATCH_NAME_MAX)

    def test_caps_count(self):
        many = [f"name{i}" for i in range(config.CUSTOMER_MATCH_MAX_NAMES + 50)]
        self.assertEqual(len(coerce(many)), config.CUSTOMER_MATCH_MAX_NAMES)

    def test_non_sequence_returns_empty(self):
        self.assertEqual(coerce(12345), [])


class StoreTests(unittest.TestCase):
    def test_defaults_empty(self):
        self.assertEqual(_store().snapshot(), {"customers": []})

    def test_update_then_snapshot_and_names(self):
        store = _store()
        store.update({"customers": ["Acme", "acme", "Globex"]})
        self.assertEqual(store.snapshot(), {"customers": ["Acme", "Globex"]})
        self.assertEqual(store.names(), ["Acme", "Globex"])

    def test_names_is_a_copy(self):
        store = _store()
        store.update(["Acme"])
        store.names().append("Mutated")
        self.assertEqual(store.names(), ["Acme"])


class PersistenceTests(unittest.TestCase):
    def test_encoded_on_disk_and_reloads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "customer_match.json"
            store = CustomerMatchStore(path)
            store.update(["Acme Corp"])

            data = path.read_bytes()
            self.assertTrue(data.startswith(crypto.MAGIC))  # encoded at rest

            reloaded = CustomerMatchStore(path)
            reloaded.load()
            self.assertEqual(reloaded.names(), ["Acme Corp"])

    def test_load_missing_file_stays_empty(self):
        store = _store()
        store.load()
        self.assertEqual(store.snapshot(), {"customers": []})


if __name__ == "__main__":
    unittest.main()
