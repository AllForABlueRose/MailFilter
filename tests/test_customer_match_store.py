"""Tests for mailfilter.customer_match_store: the Suspected Customers List.

The list is now keyword->organization mappings (``{"keyword", "org_id"}``); legacy
bare-name caches migrate to unmapped keywords.
"""

import tempfile
import unittest
from pathlib import Path

import config
from mailfilter import crypto
from mailfilter.customer_match_store import CustomerMatchStore, coerce


def _store():
    return CustomerMatchStore(Path(tempfile.mkdtemp()) / "customer_match.json")


def _m(keyword, org_id=""):
    return {"keyword": keyword, "org_id": org_id}


class CoerceTests(unittest.TestCase):
    def test_accepts_new_mapping_dicts(self):
        self.assertEqual(
            coerce([{"keyword": "zen", "org_id": "o1"}, {"keyword": "glo", "org_id": "o2"}]),
            [_m("zen", "o1"), _m("glo", "o2")])

    def test_accepts_customers_envelope(self):
        self.assertEqual(coerce({"customers": [{"keyword": "A", "org_id": "o1"}]}),
                         [_m("A", "o1")])

    def test_migrates_legacy_bare_names_to_unmapped_keywords(self):
        self.assertEqual(coerce(["Acme", "Globex"]), [_m("Acme"), _m("Globex")])
        self.assertEqual(coerce({"customers": ["Acme"]}), [_m("Acme")])

    def test_trims_and_drops_blank_keywords(self):
        self.assertEqual(
            coerce([{"keyword": "  Acme  ", "org_id": "  o1 "}, {"keyword": "", "org_id": "o2"},
                    {"keyword": "   ", "org_id": ""}, "Globex"]),
            [_m("Acme", "o1"), _m("Globex")])

    def test_dedupes_keyword_case_insensitively_first_wins(self):
        self.assertEqual(
            coerce([{"keyword": "Acme", "org_id": "o1"}, {"keyword": "acme", "org_id": "o2"}]),
            [_m("Acme", "o1")])

    def test_caps_keyword_length(self):
        long = "x" * (config.CUSTOMER_MATCH_NAME_MAX * 2)
        self.assertEqual(len(coerce([_m(long)])[0]["keyword"]), config.CUSTOMER_MATCH_NAME_MAX)

    def test_caps_count(self):
        many = [_m(f"name{i}") for i in range(config.CUSTOMER_MATCH_MAX_NAMES + 50)]
        self.assertEqual(len(coerce(many)), config.CUSTOMER_MATCH_MAX_NAMES)

    def test_non_sequence_returns_empty(self):
        self.assertEqual(coerce(12345), [])
        self.assertEqual(coerce("A\nB"), [])  # bare string is no longer a list source


class StoreTests(unittest.TestCase):
    def test_defaults_empty(self):
        self.assertEqual(_store().snapshot(), {"customers": []})

    def test_update_then_snapshot_and_mappings(self):
        store = _store()
        store.update({"customers": [{"keyword": "Acme", "org_id": "o1"},
                                    {"keyword": "acme", "org_id": "o9"},
                                    {"keyword": "Globex", "org_id": "o2"}]})
        self.assertEqual(store.snapshot(), {"customers": [_m("Acme", "o1"), _m("Globex", "o2")]})
        self.assertEqual(store.mappings(), [_m("Acme", "o1"), _m("Globex", "o2")])

    def test_mappings_is_a_deep_copy(self):
        store = _store()
        store.update([{"keyword": "Acme", "org_id": "o1"}])
        got = store.mappings()
        got.append(_m("Mutated"))
        got[0]["org_id"] = "hacked"
        self.assertEqual(store.mappings(), [_m("Acme", "o1")])


class PersistenceTests(unittest.TestCase):
    def test_encoded_on_disk_and_reloads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "customer_match.json"
            store = CustomerMatchStore(path)
            store.update([{"keyword": "Acme Corp", "org_id": "o1"}])

            data = path.read_bytes()
            self.assertTrue(data.startswith(crypto.MAGIC))  # encoded at rest

            reloaded = CustomerMatchStore(path)
            reloaded.load()
            self.assertEqual(reloaded.mappings(), [_m("Acme Corp", "o1")])

    def test_legacy_bare_name_cache_migrates_on_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "customer_match.json"
            # Simulate an old cache that stored bare name strings.
            legacy = CustomerMatchStore(path)
            legacy._mappings = ["Acme Corp"]  # pre-migration shape on disk
            legacy._save()

            reloaded = CustomerMatchStore(path)
            reloaded.load()
            self.assertEqual(reloaded.mappings(), [_m("Acme Corp")])

    def test_load_missing_file_stays_empty(self):
        store = _store()
        store.load()
        self.assertEqual(store.snapshot(), {"customers": []})


if __name__ == "__main__":
    unittest.main()
