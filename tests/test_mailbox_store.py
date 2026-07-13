"""Tests for MailboxStore: the mailboxes Press may draft from, and their proof.

The store records a verdict; the proving itself is COM (mailfilter/outlook.py) and is
tested against stubs in test_press_routes.py. What matters here is that no path other
than an actual verification can produce a `verified` mailbox.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import config
from mailfilter import persistence
from mailfilter.mailbox_store import MailboxStore


class MailboxStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.path = Path(self._tmp) / "mailbox.json"
        self.store = MailboxStore(self.path)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_starts_unset_and_not_ready(self):
        snap = self.store.snapshot()
        self.assertEqual(snap["personal"]["status"], "unset")
        self.assertEqual(snap["shared"]["status"], "unset")
        self.assertEqual(snap["selected"], "personal")
        self.assertTrue(snap["cc_enabled"])
        self.assertFalse(self.store.is_ready())

    def test_a_verified_mailbox_makes_it_ready(self):
        self.store.set_address("personal", "me@example.com", "verified")
        self.assertTrue(self.store.is_ready())
        self.assertEqual(self.store.selected_address(), "me@example.com")
        self.assertTrue(self.store.get("personal")["verified_at"])

    def test_a_pending_mailbox_is_not_ready_and_yields_no_address(self):
        # Named while Outlook was down: remembered, but it unlocks nothing.
        self.store.set_address("personal", "me@example.com", "pending", "Outlook is down")
        self.assertFalse(self.store.is_ready())
        self.assertEqual(self.store.selected_address(), "")
        self.assertEqual(self.store.pending_kinds(), ["personal"])

    def test_a_rejected_mailbox_drops_the_address(self):
        # A wrong address must not linger -- the user is asked again.
        self.store.set_address("personal", "wrong@example.com", "unset", "not your mailbox")
        box = self.store.get("personal")
        self.assertEqual(box["address"], "")
        self.assertEqual(box["status"], "unset")
        self.assertEqual(box["error"], "not your mailbox")

    def test_readiness_follows_the_selected_mailbox(self):
        self.store.set_address("personal", "me@example.com", "verified")
        self.store.update({"selected": "shared"})     # shared is still unset
        self.assertFalse(self.store.is_ready())
        self.assertEqual(self.store.selected_address(), "")
        self.store.set_address("shared", "team@example.com", "verified")
        self.assertTrue(self.store.is_ready())
        self.assertEqual(self.store.selected_address(), "team@example.com")

    def test_update_cannot_set_an_address_or_a_status(self):
        # Only a real verification may do that.
        self.store.update({"personal": {"address": "sneaky@example.com",
                                        "status": "verified"},
                           "selected": "shared", "cc_enabled": False})
        snap = self.store.snapshot()
        self.assertEqual(snap["personal"]["address"], "")
        self.assertEqual(snap["personal"]["status"], "unset")
        self.assertEqual(snap["selected"], "shared")   # these two DO apply
        self.assertFalse(snap["cc_enabled"])

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.set_address("nonsense", "x@example.com", "verified")

    def test_unknown_selected_value_is_ignored(self):
        self.store.update({"selected": "nonsense"})
        self.assertEqual(self.store.snapshot()["selected"], "personal")

    def test_persists_encoded_and_reloads(self):
        self.store.set_address("shared", "team@example.com", "verified")
        self.store.update({"selected": "shared", "cc_enabled": False})

        raw = self.path.read_bytes()
        self.assertNotIn(b"team@example.com", raw)   # encoded at rest, not plaintext

        again = MailboxStore(self.path)
        again.load()
        snap = again.snapshot()
        self.assertEqual(snap["shared"]["address"], "team@example.com")
        self.assertEqual(snap["shared"]["status"], "verified")
        self.assertEqual(snap["selected"], "shared")
        self.assertFalse(snap["cc_enabled"])
        self.assertTrue(again.is_ready())

    def test_a_corrupt_cache_cannot_grant_verified(self):
        # A hand-edited cache claiming "verified" with no address must not unlock
        # draft creation.
        persistence.save_encoded(self.path, {
            "personal": {"address": "", "status": "verified"},
            "selected": "personal",
        })
        store = MailboxStore(self.path)
        store.load()
        self.assertEqual(store.snapshot()["personal"]["status"], "unset")
        self.assertFalse(store.is_ready())

    def test_an_address_is_capped(self):
        self.store.set_address("personal", "x" * (config.MAILBOX_ADDRESS_MAX + 50),
                               "verified")
        self.assertEqual(len(self.store.get("personal")["address"]),
                         config.MAILBOX_ADDRESS_MAX)

    def test_load_of_a_missing_file_leaves_defaults(self):
        store = MailboxStore(Path(self._tmp) / "nope.json")
        store.load()
        self.assertEqual(store.snapshot()["personal"]["status"], "unset")


class OwnAddressTests(unittest.TestCase):
    """The saved identity the app's internal domain comes from.

    Sticky by design: Press's mailbox may be unset, pending or rejected at any moment,
    and none of that may change how the rest of the app classifies a sender.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.path = Path(self._tmp) / "mailbox.json"
        self.store = MailboxStore(self.path)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_starts_blank(self):
        self.assertEqual(self.store.own_address(), "")

    def test_the_first_detection_sets_it(self):
        self.store.remember_own_address("me@mycorp.com")
        self.assertEqual(self.store.own_address(), "me@mycorp.com")

    def test_the_same_domain_does_not_overwrite_it(self):
        self.store.remember_own_address("me@mycorp.com")
        self.store.remember_own_address("someone.else@mycorp.com")
        self.assertEqual(self.store.own_address(), "me@mycorp.com")

    def test_a_different_domain_overwrites_it(self):
        self.store.remember_own_address("me@mycorp.com")
        self.store.remember_own_address("me@newcorp.co.jp")
        self.assertEqual(self.store.own_address(), "me@newcorp.co.jp")

    def test_a_blank_address_never_clears_it(self):
        self.store.remember_own_address("me@mycorp.com")
        self.store.remember_own_address("")
        self.store.remember_own_address(None)
        self.assertEqual(self.store.own_address(), "me@mycorp.com")

    def test_it_survives_the_mailbox_being_rejected_or_unset(self):
        self.store.remember_own_address("me@mycorp.com")
        self.store.set_address("personal", "me@mycorp.com", "verified")
        self.store.set_address("personal", "", "unset", "wrong mailbox")
        self.assertEqual(self.store.get("personal")["status"], "unset")
        self.assertEqual(self.store.own_address(), "me@mycorp.com")   # still known

    def test_it_survives_a_deferred_check(self):
        self.store.remember_own_address("me@mycorp.com")
        self.store.set_address("personal", "me@mycorp.com", "pending", "no Outlook")
        self.assertEqual(self.store.own_address(), "me@mycorp.com")

    def test_it_is_capped(self):
        self.store.remember_own_address("x" * (config.MAILBOX_ADDRESS_MAX + 50))
        self.assertEqual(len(self.store.own_address()), config.MAILBOX_ADDRESS_MAX)

    def test_it_persists_encoded_and_reloads(self):
        self.store.remember_own_address("me@mycorp.com")
        reloaded = MailboxStore(self.path)
        reloaded.load()
        self.assertEqual(reloaded.own_address(), "me@mycorp.com")

    def test_update_cannot_set_it(self):
        self.store.update({"own_address": "attacker@evil.com", "cc_enabled": False})
        self.assertEqual(self.store.own_address(), "")


if __name__ == "__main__":
    unittest.main()
