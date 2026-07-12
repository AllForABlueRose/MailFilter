"""Tests for CategoryStore: the selectable organization categories.

Seeded on first run, grown by typing. Case-insensitively deduplicated, because the
"Partner" category is not merely cosmetic -- it decides whose domains count as internal
in a reply template (customers.internal_domains).
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import config
from mailfilter.category_store import CategoryStore, coerce


class CoerceTests(unittest.TestCase):
    def test_trims_and_drops_blanks(self):
        self.assertEqual(coerce(["  Partner  ", "", "   ", "Vendor"]),
                         ["Partner", "Vendor"])

    def test_deduplicates_case_insensitively_first_spelling_wins(self):
        self.assertEqual(coerce(["Partner", "partner", "PARTNER"]), ["Partner"])

    def test_order_is_preserved(self):
        self.assertEqual(coerce(["Vendor", "Root", "Customer"]),
                         ["Vendor", "Root", "Customer"])

    def test_non_strings_are_dropped(self):
        self.assertEqual(coerce(["Partner", 7, None, {"x": 1}]), ["Partner"])

    def test_a_name_is_capped(self):
        self.assertEqual(len(coerce(["x" * (config.ORG_CATEGORY_MAX + 40)])[0]),
                         config.ORG_CATEGORY_MAX)

    def test_the_list_is_capped(self):
        self.assertEqual(len(coerce([f"c{i}" for i in range(config.ORG_CATEGORIES_MAX + 20)])),
                         config.ORG_CATEGORIES_MAX)

    def test_a_corrupt_cache_yields_an_empty_list(self):
        self.assertEqual(coerce("not a list"), [])
        self.assertEqual(coerce(None), [])


class CategoryStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.path = Path(self._tmp) / "categories.json"
        self.store = CategoryStore(self.path)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_starts_empty(self):
        self.assertEqual(self.store.snapshot(), [])

    def test_seed_fills_an_empty_store(self):
        self.store.seed(config.ORG_DEFAULT_CATEGORIES)
        self.assertEqual(self.store.snapshot(), list(config.ORG_DEFAULT_CATEGORIES))

    def test_seed_does_not_overwrite_an_existing_list(self):
        self.store.add("Reseller")
        self.store.seed(config.ORG_DEFAULT_CATEGORIES)
        self.assertEqual(self.store.snapshot(), ["Reseller"])

    def test_add_creates_a_new_category(self):
        self.assertTrue(self.store.add("Reseller"))
        self.assertIn("Reseller", self.store.snapshot())

    def test_add_is_case_insensitively_idempotent(self):
        self.store.add("Partner")
        self.assertFalse(self.store.add("partner"))
        self.assertEqual(self.store.snapshot(), ["Partner"])   # first spelling wins

    def test_add_ignores_a_blank(self):
        self.assertFalse(self.store.add("   "))
        self.assertEqual(self.store.snapshot(), [])

    def test_add_refuses_once_full(self):
        for i in range(config.ORG_CATEGORIES_MAX):
            self.store.add(f"c{i}")
        self.assertFalse(self.store.add("one too many"))
        self.assertEqual(len(self.store.snapshot()), config.ORG_CATEGORIES_MAX)

    def test_update_replaces_the_whole_list(self):
        self.store.seed(config.ORG_DEFAULT_CATEGORIES)
        self.assertEqual(self.store.update(["Only"]), ["Only"])

    def test_persists_encoded_and_reloads(self):
        self.store.seed(config.ORG_DEFAULT_CATEGORIES)
        self.store.add("Reseller")

        raw = self.path.read_bytes()
        self.assertNotIn(b"Reseller", raw)   # encoded at rest, not plaintext

        again = CategoryStore(self.path)
        again.load()
        self.assertEqual(again.snapshot(),
                         list(config.ORG_DEFAULT_CATEGORIES) + ["Reseller"])

    def test_load_of_a_missing_file_is_empty(self):
        store = CategoryStore(Path(self._tmp) / "nope.json")
        store.load()
        self.assertEqual(store.snapshot(), [])

    def test_the_partner_default_exists(self):
        # customers.internal_domains keys off it, so it must be one of the seeds.
        self.assertIn(config.ORG_PARTNER_CATEGORY, config.ORG_DEFAULT_CATEGORIES)


if __name__ == "__main__":
    unittest.main()
