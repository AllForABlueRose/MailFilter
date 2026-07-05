"""Tests for mailfilter.customers: contact aggregation and org resolution.

The directory is derived from the in-memory mail snapshot, so inputs are built
with make_mail + MailStore._with_derived (which supplies _received_dt), matching
how the store hands mails to the read side.
"""

import unittest

from mailfilter import customers
from mailfilter.store import MailStore
from tests.factories import make_mail


def _mails(*overrides):
    return [MailStore._with_derived(make_mail(**o)) for o in overrides]


def _by_email(directory):
    return {c["email"]: c for c in directory}


class AggregateTests(unittest.TestCase):
    def test_dedup_across_mails_and_lowercases_email(self):
        contacts = customers.aggregate(_mails(
            {"id": "A", "sender_email": "Carol@Acme.com"},
            {"id": "B", "sender_email": "carol@acme.com"},
        ))
        emails = [c["email"] for c in contacts]
        self.assertIn("carol@acme.com", emails)
        self.assertEqual(emails.count("carol@acme.com"), 1)

    def test_count_is_distinct_mails(self):
        contacts = _by_email(customers.aggregate(_mails(
            {"id": "A", "sender": "Carol", "sender_email": "carol@acme.com"},
            {"id": "B", "sender": "Carol", "sender_email": "carol@acme.com"},
        )))
        self.assertEqual(contacts["carol@acme.com"]["count"], 2)

    def test_cc_and_to_both_harvested(self):
        contacts = _by_email(customers.aggregate(_mails(
            {"id": "A",
             "recipient_names": ["Bob"], "recipient_emails": ["bob@acme.com"],
             "cc_names": ["Dan"], "cc_emails": ["dan@acme.com"]},
        )))
        self.assertIn("bob@acme.com", contacts)
        self.assertIn("dan@acme.com", contacts)

    def test_same_contact_on_one_mail_counts_once(self):
        # Appearing as both To and CC on the same mail is one mail, not two.
        contacts = _by_email(customers.aggregate(_mails(
            {"id": "A",
             "recipient_names": ["Bob"], "recipient_emails": ["bob@acme.com"],
             "cc_names": ["Bob"], "cc_emails": ["bob@acme.com"]},
        )))
        self.assertEqual(contacts["bob@acme.com"]["count"], 1)

    def test_name_is_most_recent_display_name(self):
        contacts = _by_email(customers.aggregate(_mails(
            {"id": "A", "sender": "Carol Old", "sender_email": "carol@acme.com",
             "received": "2026-01-01 09:00:00"},
            {"id": "B", "sender": "Carol New", "sender_email": "carol@acme.com",
             "received": "2026-06-01 09:00:00"},
        )))
        self.assertEqual(contacts["carol@acme.com"]["name"], "Carol New")

    def test_last_received_is_newest(self):
        contacts = _by_email(customers.aggregate(_mails(
            {"id": "A", "sender_email": "carol@acme.com", "received": "2026-01-01 09:00:00"},
            {"id": "B", "sender_email": "carol@acme.com", "received": "2026-06-01 09:00:00"},
        )))
        self.assertEqual(contacts["carol@acme.com"]["last_dt"].year, 2026)
        self.assertEqual(contacts["carol@acme.com"]["last_dt"].month, 6)

    def test_blank_email_skipped(self):
        contacts = customers.aggregate(_mails(
            {"id": "A", "sender": "Nameless", "sender_email": "",
             "recipient_names": ["Bob"], "recipient_emails": ["bob@acme.com"]},
        ))
        self.assertEqual([c["email"] for c in contacts], ["bob@acme.com"])

    def test_exchange_dn_skipped(self):
        contacts = customers.aggregate(_mails(
            {"id": "A", "sender_email": "/O=EXCHANGELABS/OU=x/CN=foo",
             "recipient_emails": ["bob@acme.com"], "recipient_names": ["Bob"]},
        ))
        self.assertTrue(all(not c["email"].startswith("/") for c in contacts))
        self.assertEqual(len(contacts), 1)

    def test_unequal_name_email_lists_pad(self):
        # Two emails, one name: the second pairs with "" rather than truncating.
        contacts = _by_email(customers.aggregate(_mails(
            {"id": "A", "recipient_names": ["Bob"],
             "recipient_emails": ["bob@acme.com", "extra@acme.com"]},
        )))
        self.assertIn("extra@acme.com", contacts)
        self.assertEqual(contacts["extra@acme.com"]["name"], "")

    def test_domain_extracted(self):
        contacts = _by_email(customers.aggregate(_mails(
            {"id": "A", "sender_email": "carol@acme.co.jp"},
        )))
        self.assertEqual(contacts["carol@acme.co.jp"]["domain"], "acme.co.jp")


class ResolveTests(unittest.TestCase):
    def _org(self, oid, created, **over):
        org = {"id": oid, "name": oid.title(), "color": "#111111", "category": "",
               "domains": [], "contacts": [], "created": created}
        org.update(over)
        return org

    def test_unassigned_when_no_org_matches(self):
        d = _by_email(customers.build_directory(
            _mails({"id": "A", "sender_email": "carol@acme.com"}), []))
        self.assertIsNone(d["carol@acme.com"]["member_org_id"])
        self.assertIsNone(d["carol@acme.com"]["rep_org_id"])

    def test_domain_mapping_resolves_member(self):
        orgs = [self._org("acme", "2026-01-01 00:00:00",
                          domains=[{"domain": "acme.com", "role": "member"}])]
        d = _by_email(customers.build_directory(
            _mails({"id": "A", "sender_email": "carol@acme.com"}), orgs))
        self.assertEqual(d["carol@acme.com"]["member_org_id"], "acme")
        self.assertIsNone(d["carol@acme.com"]["rep_org_id"])

    def test_representative_domain_role(self):
        # A whole domain mapped as representative makes its contacts reps (rep via
        # domain, so rep_pinned is False) with no base membership.
        orgs = [self._org("acme", "2026-01-01 00:00:00",
                          domains=[{"domain": "repfirm.com", "role": "representative"}])]
        d = _by_email(customers.build_directory(
            _mails({"id": "A", "sender_email": "rep@repfirm.com"}), orgs))
        self.assertEqual(d["rep@repfirm.com"]["rep_org_id"], "acme")
        self.assertIsNone(d["rep@repfirm.com"]["member_org_id"])
        self.assertFalse(d["rep@repfirm.com"]["rep_pinned"])

    def test_multi_domain_org(self):
        orgs = [self._org("acme", "2026-01-01 00:00:00", domains=[
            {"domain": "acme.com", "role": "member"},
            {"domain": "acme.co.jp", "role": "member"},
        ])]
        d = _by_email(customers.build_directory(_mails(
            {"id": "A", "sender_email": "a@acme.com"},
            {"id": "B", "sender_email": "b@acme.co.jp"},
        ), orgs))
        self.assertEqual(d["a@acme.com"]["member_org_id"], "acme")
        self.assertEqual(d["b@acme.co.jp"]["member_org_id"], "acme")

    def test_member_and_representative_coexist(self):
        # Bob is a member of X (his domain) AND a pinned representative of Acme —
        # the two axes resolve independently.
        orgs = [
            self._org("x", "2026-01-01 00:00:00",
                      domains=[{"domain": "partner.com", "role": "member"}]),
            self._org("acme", "2026-02-01 00:00:00",
                      contacts=[{"email": "bob@partner.com", "role": "representative"}]),
        ]
        d = _by_email(customers.build_directory(
            _mails({"id": "A", "sender_email": "bob@partner.com"}), orgs))
        self.assertEqual(d["bob@partner.com"]["member_org_id"], "x")
        self.assertEqual(d["bob@partner.com"]["rep_org_id"], "acme")
        self.assertTrue(d["bob@partner.com"]["rep_pinned"])

    def test_member_domain_collision_first_org_wins(self):
        orgs = [
            self._org("first", "2026-01-01 00:00:00",
                      domains=[{"domain": "shared.com", "role": "member"}]),
            self._org("second", "2026-02-01 00:00:00",
                      domains=[{"domain": "shared.com", "role": "member"}]),
        ]
        d = _by_email(customers.build_directory(
            _mails({"id": "A", "sender_email": "x@shared.com"}), orgs))
        self.assertEqual(d["x@shared.com"]["member_org_id"], "first")

    def test_representative_pin_collision_first_org_wins(self):
        orgs = [
            self._org("first", "2026-01-01 00:00:00",
                      contacts=[{"email": "x@gmail.com", "role": "representative"}]),
            self._org("second", "2026-02-01 00:00:00",
                      contacts=[{"email": "x@gmail.com", "role": "representative"}]),
        ]
        d = _by_email(customers.build_directory(
            _mails({"id": "A", "sender_email": "x@gmail.com"}), orgs))
        self.assertEqual(d["x@gmail.com"]["rep_org_id"], "first")

    def test_last_received_formatted_as_string(self):
        d = _by_email(customers.build_directory(
            _mails({"id": "A", "sender_email": "carol@acme.com",
                    "received": "2026-06-01 09:30:00"}), []))
        self.assertEqual(d["carol@acme.com"]["last_received"], "2026-06-01 09:30:00")


class LabelResolverTests(unittest.TestCase):
    def _org(self, oid, **over):
        org = {"id": oid, "name": oid.title(), "display_name": "", "color": "#111111",
               "domains": [], "contacts": [], "created": "2026-01-01 00:00:00"}
        org.update(over)
        return org

    def test_member_label_uses_display_name_and_color(self):
        orgs = [self._org("acme", name="Acme Corporation", display_name="Acme",
                          color="#ff3366", domains=[{"domain": "acme.com", "role": "member"}])]
        labels = customers.label_resolver(orgs)("carol@acme.com")
        self.assertEqual(labels, [{"name": "Acme", "color": "#ff3366"}])

    def test_display_name_falls_back_to_real_name(self):
        orgs = [self._org("acme", name="Acme Corporation", display_name="",
                          domains=[{"domain": "acme.com", "role": "member"}])]
        self.assertEqual(customers.label_resolver(orgs)("carol@acme.com")[0]["name"],
                         "Acme Corporation")

    def test_member_then_representative_both_labelled(self):
        orgs = [
            self._org("x", name="Xco", domains=[{"domain": "acme.com", "role": "member"}]),
            self._org("acme", name="Acme", contacts=[{"email": "rep@acme.com",
                                                       "role": "representative"}]),
        ]
        labels = customers.label_resolver(orgs)("rep@acme.com")
        self.assertEqual([l["name"] for l in labels], ["Xco", "Acme"])

    def test_same_org_on_both_axes_deduped(self):
        orgs = [self._org("acme", name="Acme",
                          domains=[{"domain": "acme.com", "role": "member"}],
                          contacts=[{"email": "bob@acme.com", "role": "representative"}])]
        self.assertEqual(customers.label_resolver(orgs)("bob@acme.com"),
                         [{"name": "Acme", "color": "#111111"}])

    def test_unresolved_and_non_smtp_yield_no_labels(self):
        orgs = [self._org("acme", domains=[{"domain": "acme.com", "role": "member"}])]
        resolve = customers.label_resolver(orgs)
        self.assertEqual(resolve("nobody@nowhere.com"), [])
        self.assertEqual(resolve(""), [])
        self.assertEqual(resolve("/O=EX/CN=foo"), [])


if __name__ == "__main__":
    unittest.main()
