"""Tests for mailfilter.customer_store: coercion, assign/unassign, persistence."""

import tempfile
import unittest
from pathlib import Path

from mailfilter import crypto
from mailfilter.customer_store import CustomerStore


def _store():
    return CustomerStore(Path(tempfile.mkdtemp()) / "customers.json")


class CoerceTests(unittest.TestCase):
    def test_new_org_is_empty_by_default(self):
        org = _store().create({"name": "Acme"})
        self.assertEqual(org["name"], "Acme")
        self.assertEqual(org["category"], "")
        self.assertEqual(org["category_color"], "#6366f1")
        self.assertEqual(org["domains"], [])
        self.assertEqual(org["contacts"], [])
        self.assertTrue(org["id"])
        self.assertTrue(org["created"])

    def test_category_color_kept_invalid_falls_back_and_preserved_on_update(self):
        store = _store()
        self.assertEqual(
            store.create({"name": "A", "category_color": "#ff0000"})["category_color"],
            "#ff0000")
        self.assertEqual(
            store.create({"name": "B", "category_color": "nope"})["category_color"],
            "#6366f1")
        org = store.create({"name": "C", "category_color": "#abcdef"})
        # Omitted on update -> the stored colour is preserved, not reset.
        self.assertEqual(store.update(org["id"], {"name": "C2"})["category_color"], "#abcdef")

    def test_blank_name_defaults_to_untitled(self):
        self.assertEqual(_store().create({"name": "   "})["name"], "Untitled")

    def test_invalid_color_falls_back(self):
        self.assertEqual(_store().create({"name": "A", "color": "red"})["color"], "#3b82f6")

    def test_valid_color_kept(self):
        self.assertEqual(_store().create({"name": "A", "color": "#abcdef"})["color"], "#abcdef")

    def test_domain_role_clamped_and_lowercased(self):
        org = _store().create({"name": "A", "domains": [
            {"domain": "ACME.com", "role": "member"},
            {"domain": "rep.com", "role": "bogus"},
        ]})
        self.assertEqual(org["domains"][0], {"domain": "acme.com", "role": "member"})
        # Unknown role clamps to the first known role ("member").
        self.assertEqual(org["domains"][1], {"domain": "rep.com", "role": "member"})

    def test_blank_and_duplicate_domains_dropped(self):
        org = _store().create({"name": "A", "domains": [
            {"domain": "acme.com", "role": "member"},
            {"domain": "  ", "role": "member"},
            {"domain": "ACME.com", "role": "representative"},  # dup (case) -> dropped
        ]})
        self.assertEqual([d["domain"] for d in org["domains"]], ["acme.com"])

    def test_contacts_coerced_like_domains(self):
        org = _store().create({"name": "A", "contacts": [
            {"email": "Bob@Acme.com", "role": "representative"},
            {"email": "bob@acme.com", "role": "member"},  # dup -> dropped
        ]})
        self.assertEqual(org["contacts"], [{"email": "bob@acme.com", "role": "representative"}])

    def test_update_preserves_contacts_when_omitted(self):
        store = _store()
        org = store.create({"name": "A"})
        store.assign("rep@gmail.com", org["id"], "representative")
        # A modal save that sends only name/domains must not wipe the pins.
        updated = store.update(org["id"], {"name": "A2", "domains": [
            {"domain": "acme.com", "role": "member"}]})
        self.assertEqual(updated["name"], "A2")
        self.assertEqual(updated["contacts"], [{"email": "rep@gmail.com", "role": "representative"}])

    def test_snapshot_copies_are_independent(self):
        store = _store()
        store.create({"name": "A", "domains": [{"domain": "acme.com", "role": "member"}]})
        snap = store.snapshot()
        snap[0]["domains"].append({"domain": "evil.com", "role": "member"})
        self.assertEqual(len(store.snapshot()[0]["domains"]), 1)


class AssignTests(unittest.TestCase):
    def test_assign_pins_contact(self):
        store = _store()
        org = store.create({"name": "A"})
        result = store.assign("rep@gmail.com", org["id"], "representative")
        self.assertEqual(result["contacts"], [{"email": "rep@gmail.com", "role": "representative"}])

    def test_assign_unknown_org_returns_none(self):
        self.assertIsNone(_store().assign("x@y.com", "nope", "member"))

    def test_assign_blank_email_returns_none(self):
        store = _store()
        org = store.create({"name": "A"})
        self.assertIsNone(store.assign("  ", org["id"], "member"))

    def test_assign_moves_contact_between_orgs(self):
        store = _store()
        a = store.create({"name": "A"})
        b = store.create({"name": "B"})
        store.assign("rep@gmail.com", a["id"], "member")
        store.assign("rep@gmail.com", b["id"], "representative")
        by_id = {o["id"]: o for o in store.snapshot()}
        self.assertEqual(by_id[a["id"]]["contacts"], [])  # removed from A
        self.assertEqual(by_id[b["id"]]["contacts"],
                         [{"email": "rep@gmail.com", "role": "representative"}])

    def test_assign_role_clamped(self):
        store = _store()
        org = store.create({"name": "A"})
        result = store.assign("x@y.com", org["id"], "bogus")
        self.assertEqual(result["contacts"][0]["role"], "member")

    def test_unassign_removes_pin(self):
        store = _store()
        org = store.create({"name": "A"})
        store.assign("rep@gmail.com", org["id"], "member")
        self.assertTrue(store.unassign("rep@gmail.com"))
        self.assertEqual(store.snapshot()[0]["contacts"], [])

    def test_unassign_unknown_is_false(self):
        self.assertFalse(_store().unassign("ghost@nowhere.com"))


class SetDomainTests(unittest.TestCase):
    def test_set_domain_adds_member(self):
        store = _store()
        org = store.create({"name": "A"})
        result = store.set_domain(org["id"], "ACME.com", "member")
        self.assertEqual(result["domains"], [{"domain": "acme.com", "role": "member"}])

    def test_set_domain_role_clamped(self):
        store = _store()
        org = store.create({"name": "A"})
        result = store.set_domain(org["id"], "acme.com", "bogus")
        self.assertEqual(result["domains"][0]["role"], "member")

    def test_set_domain_moves_between_orgs(self):
        store = _store()
        a = store.create({"name": "A"})
        b = store.create({"name": "B"})
        store.set_domain(a["id"], "acme.com", "member")
        store.set_domain(b["id"], "acme.com", "member")
        by_id = {o["id"]: o for o in store.snapshot()}
        self.assertEqual(by_id[a["id"]]["domains"], [])       # moved off A
        self.assertEqual(by_id[b["id"]]["domains"], [{"domain": "acme.com", "role": "member"}])

    def test_set_domain_unknown_org_returns_none(self):
        self.assertIsNone(_store().set_domain("nope", "acme.com", "member"))

    def test_set_domain_blank_returns_none(self):
        store = _store()
        org = store.create({"name": "A"})
        self.assertIsNone(store.set_domain(org["id"], "  ", "member"))


class HasMemberBaseTests(unittest.TestCase):
    def test_true_via_member_domain(self):
        store = _store()
        org = store.create({"name": "A"})
        store.set_domain(org["id"], "acme.com", "member")
        self.assertTrue(store.has_member_base("carol@acme.com"))

    def test_true_via_member_contact_pin(self):
        store = _store()
        org = store.create({"name": "A"})
        store.assign("bob@x.com", org["id"], "member")
        self.assertTrue(store.has_member_base("bob@x.com"))

    def test_false_when_only_representative_domain(self):
        # A representative-domain mapping is not a base membership.
        store = _store()
        org = store.create({"name": "A"})
        store.set_domain(org["id"], "repfirm.com", "representative")
        self.assertFalse(store.has_member_base("rep@repfirm.com"))

    def test_false_for_unknown_or_blank(self):
        store = _store()
        store.create({"name": "A"})
        self.assertFalse(store.has_member_base("nobody@nowhere.com"))
        self.assertFalse(store.has_member_base("  "))


class PersistenceTests(unittest.TestCase):
    def test_encoded_on_disk_and_reloads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "customers.json"
            store = CustomerStore(path)
            store.create({"name": "SecretCorp",
                          "domains": [{"domain": "secret.example", "role": "member"}]})

            data = path.read_bytes()
            self.assertTrue(data.startswith(crypto.MAGIC))
            self.assertNotIn(b"SecretCorp", data)

            reloaded = CustomerStore(path)
            reloaded.load()
            names = [o["name"] for o in reloaded.snapshot()]
            self.assertEqual(names, ["SecretCorp"])

    def test_load_missing_file_is_noop(self):
        store = _store()
        store.load()
        self.assertEqual(store.snapshot(), [])

    def test_load_corrupt_file_keeps_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "customers.json"
            path.write_bytes(b"not decodable")
            store = CustomerStore(path)
            store.load()
            self.assertEqual(store.snapshot(), [])

    def test_non_dict_entry_dropped_on_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "customers.json"
            CustomerStore(path)  # establish the path
            from mailfilter import persistence
            persistence.save_encoded(path, [{"name": "Good"}, "garbage", 42])
            store = CustomerStore(path)
            store.load()
            self.assertEqual([o["name"] for o in store.snapshot()], ["Good"])


if __name__ == "__main__":
    unittest.main()
