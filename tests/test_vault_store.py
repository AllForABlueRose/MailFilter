"""Tests for mailfilter.vault_store: lifecycle, CRUD, index, capture, accessor."""

import tempfile
import unittest
from pathlib import Path

from mailfilter import vault_crypto
from mailfilter.vault_store import VaultLocked, VaultStore


def _store():
    d = Path(tempfile.mkdtemp())
    return VaultStore(d / "vault.json", d / "index.json", d / "key.dpapi")


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
        pub = self.v.capture_scan("org-1", "leaked", "From mail", "2026-06-28 10:00:00")
        self.assertEqual(pub["kind"], "temporary")
        self.assertEqual(pub["scan_dt"], "2026-06-28 10:00:00")

    def test_capture_dedupes_same_secret(self):
        a = self.v.capture_scan("org-1", "leaked", scan_dt="2026-06-28 10:00:00")
        b = self.v.capture_scan("org-1", "leaked", scan_dt="2026-06-29 10:00:00")
        self.assertEqual(a["id"], b["id"])               # same entry, not a duplicate
        self.assertEqual(len(self.v.entries_by_org()["org-1"]), 1)

    def test_capture_stores_source_email(self):
        pub = self.v.capture_scan("org-1", "leaked", source_email="bob@acme.com")
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
        self.v.capture_scan("org-1", "leaked", scan_dt="2026-06-28 10:00:00", source_email="a@b.com")
        self.assertIn("org-1", self.v.search("2026-06-28", {}))
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


if __name__ == "__main__":
    unittest.main()
