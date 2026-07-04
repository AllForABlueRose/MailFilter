"""Tests for mailfilter.vault_store: lifecycle, CRUD, index, capture, accessor."""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import config
from mailfilter import vault_crypto
from mailfilter.vault_store import VaultLocked, VaultStore


def _store():
    d = Path(tempfile.mkdtemp())
    return VaultStore(d / "vault.json", d / "index.json", d / "key.dpapi")


def _dt(days_ago=0, hours_ago=0):
    """A RECEIVED_FORMAT datetime string relative to now (keeps age-based tests
    stable regardless of when they run — see VAULT_TEMP_HIDE_AFTER_DAYS)."""
    return (datetime.now() - timedelta(days=days_ago, hours=hours_ago)).strftime(
        config.RECEIVED_FORMAT)


@unittest.skipUnless(vault_crypto.is_available(), "cryptography not installed")
class LifecycleTests(unittest.TestCase):
    def test_init_creates_unlocked_vault(self):
        v = _store()
        self.assertFalse(v.is_initialized())
        self.assertTrue(v.init("passphrase1"))
        self.assertTrue(v.is_initialized())
        self.assertTrue(v.is_unlocked())

    def test_init_rejects_short_passphrase_and_double_init(self):
        v = _store()
        self.assertFalse(v.init("short"))          # < VAULT_PASSPHRASE_MIN
        self.assertTrue(v.init("passphrase1"))
        self.assertFalse(v.init("passphrase2"))    # already exists

    def test_lock_then_unlock_with_passphrase(self):
        v = _store()
        v.init("passphrase1")
        v.lock()
        self.assertFalse(v.is_unlocked())
        self.assertFalse(v.unlock("wrong-passphrase"))
        self.assertTrue(v.unlock("passphrase1"))

    def test_persists_across_instances(self):
        d = Path(tempfile.mkdtemp())
        args = (d / "vault.json", d / "index.json", d / "key.dpapi")
        v1 = VaultStore(*args)
        v1.init("passphrase1")
        v1.add_entry("org-1", {"label": "FTP", "secret": "s3cr3t"})
        v2 = VaultStore(*args)
        self.assertTrue(v2.is_initialized())
        self.assertTrue(v2.unlock("passphrase1"))
        self.assertEqual(v2.entries_by_org()["org-1"][0]["label"], "FTP")

    def test_operations_require_unlock(self):
        v = _store()
        v.init("passphrase1")
        v.lock()
        with self.assertRaises(VaultLocked):
            v.add_entry("org-1", {"secret": "x"})


@unittest.skipUnless(vault_crypto.is_available(), "cryptography not installed")
class EntryTests(unittest.TestCase):
    def setUp(self):
        self.v = _store()
        self.v.init("passphrase1")

    def test_add_redacts_secret_but_flags_presence(self):
        pub = self.v.add_entry("org-1", {"label": "API", "secret": "k"})
        self.assertNotIn("secret", pub)
        self.assertTrue(pub["has_secret"])
        self.assertEqual(pub["kind"], "managed")

    def test_reveal_returns_secret(self):
        pub = self.v.add_entry("org-1", {"label": "API", "secret": "topsecret"})
        self.assertEqual(self.v.reveal(pub["id"]), "topsecret")

    def test_update_and_delete(self):
        pub = self.v.add_entry("org-1", {"label": "API", "secret": "a"})
        self.v.update_entry(pub["id"], {"label": "API-2", "secret": "b"})
        self.assertEqual(self.v.entries_by_org()["org-1"][0]["label"], "API-2")
        self.assertEqual(self.v.reveal(pub["id"]), "b")
        self.assertTrue(self.v.delete_entry(pub["id"]))
        self.assertNotIn("org-1", self.v.entries_by_org())

    def test_get_secret_external_accessor(self):
        self.v.add_entry("org-1", {"label": "Login", "secret": "pw1", "kind": "managed"})
        self.v.add_entry("org-1", {"label": "Other", "secret": "pw2", "kind": "managed"})
        self.assertEqual(self.v.get_secret("org-1", "Login"), "pw1")
        self.assertEqual(self.v.get_secret("org-1"), "pw1")        # first managed
        self.assertIsNone(self.v.get_secret("org-1", "missing"))
        self.assertIsNone(self.v.get_secret("nobody"))

    def test_get_secret_none_when_locked(self):
        self.v.add_entry("org-1", {"label": "Login", "secret": "pw1"})
        self.v.lock()
        self.assertIsNone(self.v.get_secret("org-1", "Login"))


@unittest.skipUnless(vault_crypto.is_available(), "cryptography not installed")
class CaptureAndIndexTests(unittest.TestCase):
    def setUp(self):
        self.v = _store()
        self.v.init("passphrase1")

    def test_capture_adds_temporary_key(self):
        pub, created = self.v.capture_scan("org-1", "leaked", "From mail", _dt(days_ago=1))
        self.assertTrue(created)                          # brand-new key
        self.assertEqual(pub["kind"], "temporary")
        self.assertEqual(pub["scan_dt"], _dt(days_ago=1))

    def test_capture_dedupes_same_secret(self):
        a, a_created = self.v.capture_scan("org-1", "leaked", scan_dt=_dt(days_ago=3))
        b, b_created = self.v.capture_scan("org-1", "leaked", scan_dt=_dt(days_ago=2))
        self.assertTrue(a_created)                        # first is new
        self.assertFalse(b_created)                       # re-capture is not new
        self.assertEqual(a["id"], b["id"])               # same entry, not a duplicate
        self.assertEqual(len(self.v.entries_by_org()["org-1"]), 1)

    def test_capture_stores_source_email(self):
        pub, created = self.v.capture_scan("org-1", "leaked", source_email="bob@acme.com")
        self.assertTrue(created)
        self.assertEqual(pub["source_email"], "bob@acme.com")

    def test_rehome_unassigned_moves_to_resolved_org(self):
        import config
        self.v.capture_scan(config.VAULT_UNASSIGNED_ORG_ID, "leaked",
                            source_email="bob@acme.com")
        moved = self.v.rehome_unassigned(
            lambda email: "org-9" if email == "bob@acme.com" else None)
        self.assertEqual(moved, 1)
        entries = self.v.entries_by_org()
        self.assertNotIn(config.VAULT_UNASSIGNED_ORG_ID, entries)
        self.assertEqual(entries["org-9"][0]["source_email"], "bob@acme.com")

    def test_rehome_absorbs_duplicate_secret(self):
        import config
        self.v.add_entry("org-9", {"label": "M", "secret": "leaked", "kind": "managed"})
        self.v.capture_scan(config.VAULT_UNASSIGNED_ORG_ID, "leaked",
                            source_email="bob@acme.com")
        moved = self.v.rehome_unassigned(lambda email: "org-9")
        self.assertEqual(moved, 1)
        # The org keeps its single key (the parked duplicate was absorbed).
        self.assertEqual(len(self.v.entries_by_org()["org-9"]), 1)

    def test_rehome_leaves_still_unresolved(self):
        import config
        self.v.capture_scan(config.VAULT_UNASSIGNED_ORG_ID, "leaked",
                            source_email="bob@nowhere.test")
        self.assertEqual(self.v.rehome_unassigned(lambda email: None), 0)
        self.assertIn(config.VAULT_UNASSIGNED_ORG_ID, self.v.entries_by_org())

    def test_reveal_all_returns_every_secret(self):
        self.v.add_entry("org-1", {"label": "A", "secret": "alpha", "kind": "managed"})
        self.v.capture_scan("org-2", "beta", source_email="x@y.com")
        self.assertEqual(set(self.v.reveal_all().values()), {"alpha", "beta"})

    def test_reveal_all_requires_unlock(self):
        self.v.add_entry("org-1", {"label": "A", "secret": "alpha"})
        self.v.lock()
        with self.assertRaises(VaultLocked):
            self.v.reveal_all()

    def test_search_matches_value_and_org_name_redacted(self):
        self.v.add_entry("org-1", {"label": "Portal", "secret": "s3cr3t-pw", "kind": "managed"})
        self.v.add_entry("org-2", {"label": "Other", "secret": "nope", "kind": "managed"})
        names = {"org-1": "Acme", "org-2": "Beta"}
        by_value = self.v.search("s3cr3t", names)
        self.assertIn("org-1", by_value)
        self.assertNotIn("org-2", by_value)
        self.assertNotIn("secret", by_value["org-1"][0])     # still redacted
        self.assertIn("org-2", self.v.search("beta", names))  # by org display name

    def test_search_matches_datetime_and_blank_returns_all(self):
        day = _dt(days_ago=2)
        self.v.capture_scan("org-1", "leaked", scan_dt=day, source_email="a@b.com")
        self.assertIn("org-1", self.v.search(day[:10], {}))   # date portion
        self.assertIn("org-1", self.v.search("", {}))

    def test_index_reflects_kinds_and_is_readable_while_locked(self):
        self.v.add_entry("org-1", {"label": "M", "secret": "x", "kind": "managed"})
        self.v.capture_scan("org-1", "leaked", scan_dt="2026-06-28 10:00:00")
        idx = self.v.index()
        self.assertEqual(idx["org-1"]["count"], 2)
        self.assertTrue(idx["org-1"]["has_managed"])
        self.assertTrue(idx["org-1"]["has_temporary"])
        self.assertEqual(idx["org-1"]["last_scan_dt"], "2026-06-28 10:00:00")
        # The index is the non-secret summary the card reads without unlocking.
        self.v.lock()
        locked_idx = self.v.index()
        self.assertEqual(locked_idx["org-1"]["count"], 2)
        self.assertNotIn("secret", str(locked_idx))


@unittest.skipUnless(vault_crypto.is_available(), "cryptography not installed")
class VisibilityOrderingTests(unittest.TestCase):
    """Temporary-key age hiding, re-record refresh, and managed-first ordering."""

    def setUp(self):
        self.v = _store()
        self.v.init("passphrase1")

    def test_capture_refreshes_scan_dt_on_newer_rerecord(self):
        self.v.capture_scan("org-1", "leaked", scan_dt=_dt(days_ago=3))
        newer = _dt(days_ago=1)
        _entry, created = self.v.capture_scan("org-1", "leaked", scan_dt=newer)
        self.assertFalse(created)                         # a refresh is not a new key
        self.assertEqual(self.v.entries_by_org()["org-1"][0]["scan_dt"], newer)

    def test_older_rerecord_does_not_roll_back(self):
        newer = _dt(days_ago=1)
        self.v.capture_scan("org-1", "leaked", scan_dt=newer)
        self.v.capture_scan("org-1", "leaked", scan_dt=_dt(days_ago=5))
        self.assertEqual(self.v.entries_by_org()["org-1"][0]["scan_dt"], newer)

    def test_aged_temporary_hidden_from_list_and_search_but_recorded(self):
        aged = _dt(days_ago=config.VAULT_TEMP_HIDE_AFTER_DAYS + 2)
        self.v.capture_scan("org-1", "leaked", scan_dt=aged, source_email="a@b.com")
        self.assertNotIn("org-1", self.v.entries_by_org())      # hidden from the list
        self.assertNotIn("org-1", self.v.search("leaked", {}))  # and from search
        self.assertEqual(self.v.index()["org-1"]["count"], 1)   # still on record

    def test_rerecord_unhides_an_aged_key(self):
        self.v.capture_scan("org-1", "leaked",
                            scan_dt=_dt(days_ago=config.VAULT_TEMP_HIDE_AFTER_DAYS + 2))
        self.assertNotIn("org-1", self.v.entries_by_org())
        self.v.capture_scan("org-1", "leaked", scan_dt=_dt(days_ago=0))
        self.assertIn("org-1", self.v.entries_by_org())          # refreshed -> visible

    def test_managed_key_never_hidden(self):
        self.v.add_entry("org-1", {"label": "M", "secret": "x", "kind": "managed"})
        self.assertIn("org-1", self.v.entries_by_org())

    def test_ordering_managed_first_then_temporary_newest(self):
        self.v.capture_scan("org-1", "t-old", scan_dt=_dt(days_ago=2))
        self.v.capture_scan("org-1", "t-new", scan_dt=_dt(hours_ago=1))
        self.v.add_entry("org-1", {"label": "M1", "secret": "m1", "kind": "managed"})
        rows = self.v.entries_by_org()["org-1"]
        self.assertEqual([r["kind"] for r in rows], ["managed", "temporary", "temporary"])
        temps = [r for r in rows if r["kind"] == "temporary"]
        self.assertGreater(temps[0]["scan_dt"], temps[1]["scan_dt"])  # newest first


if __name__ == "__main__":
    unittest.main()
