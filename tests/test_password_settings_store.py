"""Tests for mailfilter.password_settings_store: seeded defaults, coercion and
clamping of patterns/rules, and encoded-at-rest persistence."""

import tempfile
import unittest
from pathlib import Path

import config
from mailfilter import crypto
from mailfilter.password_settings_store import (
    PasswordSettingsStore,
    coerce,
    default_settings,
)


def _store():
    return PasswordSettingsStore(Path(tempfile.mkdtemp()) / "pwd.json")


class DefaultsTests(unittest.TestCase):
    def test_seeded_with_config_defaults(self):
        snap = _store().snapshot()
        self.assertEqual(len(snap["patterns"]), len(config.PASSWORD_DEFAULT_PATTERNS))
        self.assertTrue(all(p["enabled"] for p in snap["patterns"]))
        self.assertEqual(snap["rules"], config.PASSWORD_RULE_DEFAULTS)

    def test_snapshot_is_a_deep_copy(self):
        store = _store()
        snap = store.snapshot()
        snap["patterns"].append({"name": "x", "regex": "y", "enabled": True})
        snap["rules"]["min_length"] = 1
        self.assertEqual(len(store.snapshot()["patterns"]), len(config.PASSWORD_DEFAULT_PATTERNS))
        self.assertEqual(store.snapshot()["rules"]["min_length"],
                         config.PASSWORD_RULE_DEFAULTS["min_length"])

    def test_default_settings_independent(self):
        a, b = default_settings(), default_settings()
        a["patterns"][0]["template"] = "mutated"
        self.assertNotEqual(b["patterns"][0]["template"], "mutated")


class CoerceTests(unittest.TestCase):
    def test_patterns_replaced_wholesale_and_defaulted(self):
        out = coerce({"patterns": [{"template": "pw: <{(v)}>"}]})
        self.assertEqual(out["patterns"],
                         [{"template": "pw: <{(v)}>", "value_regex": "", "enabled": True}])

    def test_name_is_dropped(self):
        # Components carry no name (identified by position); a sent name is ignored.
        out = coerce({"patterns": [{"name": "ignored", "template": "pw: <{(v)}>"}]})
        self.assertNotIn("name", out["patterns"][0])

    def test_drops_blank_and_non_dict_patterns(self):
        out = coerce({"patterns": [{"template": "  "}, "nope",
                                   {"template": "y <{(v)}>"}]})
        self.assertEqual([p["template"] for p in out["patterns"]], ["y <{(v)}>"])

    def test_template_keeps_internal_newlines(self):
        out = coerce({"patterns": [{"template": "password:\n<{(v)}>"}]})
        self.assertEqual(out["patterns"][0]["template"], "password:\n<{(v)}>")

    def test_pattern_count_capped(self):
        many = [{"template": f"r{i} <{{(v)}}>"} for i in range(config.PASSWORD_MAX_PATTERNS + 10)]
        self.assertEqual(len(coerce({"patterns": many})["patterns"]),
                         config.PASSWORD_MAX_PATTERNS)

    def test_template_and_value_length_capped(self):
        out = coerce({"patterns": [{"template": "t" * 999, "value_regex": "v" * 999}]})
        p = out["patterns"][0]
        self.assertEqual(len(p["template"]), config.PASSWORD_TEMPLATE_MAX)
        self.assertEqual(len(p["value_regex"]), config.PASSWORD_VALUE_REGEX_MAX)

    def test_rule_bools_coerced(self):
        out = coerce({"rules": {"no_japanese": "", "no_link": "yes"}})
        self.assertFalse(out["rules"]["no_japanese"])
        self.assertTrue(out["rules"]["no_link"])

    def test_length_bounds_clamped(self):
        out = coerce({"rules": {"min_length": -5, "max_length": 9999}})
        self.assertEqual(out["rules"]["min_length"], config.PASSWORD_LENGTH_FLOOR)
        self.assertEqual(out["rules"]["max_length"], config.PASSWORD_LENGTH_CEIL)

    def test_swapped_bounds_repaired(self):
        out = coerce({"rules": {"min_length": 40, "max_length": 10}})
        self.assertLessEqual(out["rules"]["min_length"], out["rules"]["max_length"])
        self.assertEqual((out["rules"]["min_length"], out["rules"]["max_length"]), (10, 40))

    def test_unknown_keys_dropped(self):
        out = coerce({"bogus": 1, "rules": {"junk": 2, "min_length": 5}})
        self.assertNotIn("bogus", out)
        self.assertNotIn("junk", out["rules"])
        self.assertEqual(out["rules"]["min_length"], 5)

    def test_non_dict_returns_base(self):
        self.assertEqual(coerce("nope"), default_settings())


class UpdateAndPersistenceTests(unittest.TestCase):
    def test_update_merges_rules_and_persists(self):
        store = _store()
        store.update({"rules": {"min_length": 6}})
        store.update({"rules": {"no_file": False}})   # min_length must be retained
        rules = store.snapshot()["rules"]
        self.assertEqual(rules["min_length"], 6)
        self.assertFalse(rules["no_file"])

    def test_encoded_on_disk_and_reloads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "pwd.json"
            store = PasswordSettingsStore(path)
            store.update({"patterns": [{"template": "pw: <{(v)}>",
                                        "value_regex": r"topsecret\d+"}]})

            data = path.read_bytes()
            self.assertTrue(data.startswith(crypto.MAGIC))
            self.assertNotIn(b"topsecret", data)   # encoded at rest, not plaintext

            reloaded = PasswordSettingsStore(path)
            reloaded.load()
            self.assertEqual(reloaded.snapshot()["patterns"][0]["value_regex"], r"topsecret\d+")

    def test_load_missing_file_keeps_defaults(self):
        store = _store()
        store.load()
        self.assertEqual(store.snapshot(), default_settings())

    def test_load_corrupt_file_keeps_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "pwd.json"
            path.write_bytes(b"not decodable")
            store = PasswordSettingsStore(path)
            store.load()
            self.assertEqual(store.snapshot(), default_settings())


if __name__ == "__main__":
    unittest.main()
