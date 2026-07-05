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

    def test_display_name_kept_when_given(self):
        org = _store().create({"name": "Acme Corporation K.K.", "display_name": "Acme"})
        self.assertEqual(org["name"], "Acme Corporation K.K.")
        self.assertEqual(org["display_name"], "Acme")

    def test_blank_display_name_stays_empty_not_untitled(self):
        # Empty is meaningful (the view falls back to the real name), so unlike
        # `name` it must NOT default to "Untitled".
        self.assertEqual(_store().create({"name": "Acme"})["display_name"], "")
        self.assertEqual(_store().create({"name": "Acme", "display_name": "  "})["display_name"], "")

    def test_display_name_preserved_on_update_when_omitted(self):
        store = _store()
        org = store.create({"name": "Acme Corporation K.K.", "display_name": "Acme"})
        # A save that sends only the name must not wipe the nickname.
        self.assertEqual(store.update(org["id"], {"name": "Acme Corp K.K."})["display_name"], "Acme")

    def test_card_style_pattern_default_to_first(self):
        org = _store().create({"name": "A"})
        self.assertEqual(org["card_style"], "outline")
        self.assertEqual(org["card_pattern"], "none")

    def test_card_style_pattern_kept_and_clamped(self):
        store = _store()
        ok = store.create({"name": "A", "card_style": "filled", "card_pattern": "dots"})
        self.assertEqual((ok["card_style"], ok["card_pattern"]), ("filled", "dots"))
        # Unknown values clamp to the first entry of each tuple.
        bad = store.create({"name": "B", "card_style": "neon", "card_pattern": "swirl"})
        self.assertEqual((bad["card_style"], bad["card_pattern"]), ("outline", "none"))

    def test_card_style_pattern_preserved_on_update_when_omitted(self):
        store = _store()
        org = store.create({"name": "A", "card_style": "filled", "card_pattern": "grid"})
        updated = store.update(org["id"], {"name": "A2"})
        self.assertEqual((updated["card_style"], updated["card_pattern"]), ("filled", "grid"))

    def test_card_decoration_fields_default_to_first(self):
        org = _store().create({"name": "A"})
        self.assertEqual(
            (org["card_ink"], org["card_corner"], org["card_corner_pos"],
             org["card_banner"], org["card_scene"]),
            ("white", "none", "top-right", "none", "none"))

    def test_card_decoration_fields_kept_and_clamped(self):
        store = _store()
        ok = store.create({"name": "A", "card_ink": "black", "card_corner": "star",
                           "card_corner_pos": "bottom-right", "card_banner": "both",
                           "card_scene": "wave", "card_pattern": "hatch"})
        self.assertEqual(
            (ok["card_ink"], ok["card_corner"], ok["card_corner_pos"],
             ok["card_banner"], ok["card_scene"], ok["card_pattern"]),
            ("black", "star", "bottom-right", "both", "wave", "hatch"))
        # Unknown values clamp to the first entry of each tuple.
        bad = store.create({"name": "B", "card_ink": "gold", "card_corner": "flag",
                            "card_corner_pos": "left", "card_banner": "top",
                            "card_scene": "rain"})
        self.assertEqual(
            (bad["card_ink"], bad["card_corner"], bad["card_corner_pos"],
             bad["card_banner"], bad["card_scene"]),
            ("white", "none", "top-right", "none", "none"))

    def test_card_decoration_fields_preserved_on_update_when_omitted(self):
        store = _store()
        org = store.create({"name": "A", "card_ink": "black", "card_scene": "cloud"})
        updated = store.update(org["id"], {"name": "A2"})
        self.assertEqual((updated["card_ink"], updated["card_scene"]), ("black", "cloud"))

    def test_notes_default_empty_kept_and_capped(self):
        import config
        self.assertEqual(_store().create({"name": "A"})["notes"], "")
        kept = _store().create({"name": "A", "notes": "  CC procurement\nJPY only  "})
        self.assertEqual(kept["notes"], "CC procurement\nJPY only")   # trimmed, newline kept
        capped = _store().create({"name": "A", "notes": "x" * (config.ORG_NOTES_MAX + 50)})
        self.assertEqual(len(capped["notes"]), config.ORG_NOTES_MAX)

    def test_notes_preserved_on_update_when_omitted(self):
        store = _store()
        org = store.create({"name": "A", "notes": "be mindful"})
        self.assertEqual(store.update(org["id"], {"name": "A2"})["notes"], "be mindful")

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


class KeyAssignmentTests(unittest.TestCase):
    """The Unlock Station's recorded key-assignment patterns on an org."""

    def test_new_org_has_no_key_assignments(self):
        self.assertEqual(_store().create({"name": "Acme"})["key_assignments"], [])

    def test_record_and_latest_wins_per_file_kind(self):
        store = _store()
        org = store.create({"name": "Acme"})
        store.record_key_assignment(org["id"], "zip", "managed")
        updated = store.record_key_assignment(org["id"], "zip", "recent_temporary")
        # One pattern per file kind, latest wins.
        zips = [k for k in updated["key_assignments"] if k["file_kind"] == "zip"]
        self.assertEqual(len(zips), 1)
        self.assertEqual(zips[0]["selector"], "recent_temporary")

    def test_record_rejects_invalid_kind_or_selector(self):
        store = _store()
        org = store.create({"name": "Acme"})
        self.assertIsNone(store.record_key_assignment(org["id"], "pdf", "managed"))
        self.assertIsNone(store.record_key_assignment(org["id"], "zip", "nonsense"))
        self.assertIsNone(store.record_key_assignment("no-such-org", "zip", "managed"))

    def test_coerce_drops_invalid_and_dedupes_and_preserved_on_update(self):
        store = _store()
        org = store.create({"name": "Acme", "key_assignments": [
            {"file_kind": "zip", "selector": "managed"},
            {"file_kind": "zip", "selector": "recent_temporary"},   # dup kind dropped
            {"file_kind": "pdf", "selector": "managed"},            # invalid kind dropped
            {"file_kind": "excel", "selector": "bogus"},            # invalid selector dropped
        ]})
        self.assertEqual(org["key_assignments"],
                         [{"file_kind": "zip", "selector": "managed",
                           "recorded": org["key_assignments"][0]["recorded"]}])
        # An update that omits the field preserves it.
        again = store.update(org["id"], {"name": "Acme Renamed"})
        self.assertEqual(again["key_assignments"], org["key_assignments"])


if __name__ == "__main__":
    unittest.main()
