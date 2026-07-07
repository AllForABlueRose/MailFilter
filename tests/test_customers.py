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


class OrgLabelTests(unittest.TestCase):
    """`customers.org_label` — the single-org pill (display name + colour)."""

    def test_uses_display_name_and_color(self):
        org = {"name": "Acme Corporation", "display_name": "Acme", "color": "#ff3366"}
        self.assertEqual(customers.org_label(org), {"name": "Acme", "color": "#ff3366"})

    def test_display_name_falls_back_to_real_name(self):
        org = {"name": "Acme Corporation", "display_name": "", "color": "#111111"}
        self.assertEqual(customers.org_label(org)["name"], "Acme Corporation")


class MailOrgResolverTests(unittest.TestCase):
    """The single shared mail->org resolver: Brute Force keyword > representative >
    sender member, feeding pill + download + CSV."""

    def _org(self, oid, **over):
        org = {"id": oid, "name": oid.title(), "display_name": "", "color": "#222222",
               "domains": [], "contacts": [], "created": "2026-01-01 00:00:00"}
        org.update(over)
        return org

    def setUp(self):
        # base-of sender's member org, plus a rep-of org on the same domain.
        self.member = self._org("base", name="Base Inc",
                                domains=[{"domain": "acme.com", "role": "member"}])
        self.rep = self._org("acme", name="Acme Corp", display_name="Acme",
                             domains=[{"domain": "acme.com", "role": "representative"}])
        self.keyword_org = self._org("glo", name="Globex Inc")
        self.orgs = [self.member, self.rep, self.keyword_org]
        self.mappings = [{"keyword": "Globex Industries", "org_id": "glo"}]

    def _mail(self, **over):
        return make_mail(**over)

    def test_representative_beats_member(self):
        # No keyword; sender on acme.com is both a member (Base) and represented (Acme).
        org = customers.resolve_mail_org(
            self._mail(sender_email="bob@acme.com", body="hi"), self.orgs)
        self.assertEqual(org["id"], "acme")   # representative wins

    def test_member_when_no_representative(self):
        org = customers.resolve_mail_org(
            self._mail(sender_email="bob@acme.com"), [self.member])
        self.assertEqual(org["id"], "base")

    def test_brute_force_beats_sender(self):
        # Keyword in body maps to Globex, overriding the sender's rep/member org.
        org = customers.resolve_mail_org(
            self._mail(sender_email="bob@acme.com", body="re: Globex Industries"),
            self.orgs, self.mappings)
        self.assertEqual(org["id"], "glo")

    def test_brute_force_disabled_falls_back_to_sender(self):
        # mappings=None disables the keyword tier -> sender resolution (rep) wins.
        org = customers.resolve_mail_org(
            self._mail(sender_email="bob@acme.com", body="re: Globex Industries"),
            self.orgs, None)
        self.assertEqual(org["id"], "acme")

    def test_keyword_matches_subject_or_body_case_insensitively(self):
        r = customers.mail_org_resolver(self.orgs, self.mappings)
        self.assertEqual(r(self._mail(sender_email="x@nobody.com",
                                      subject="GLOBEX INDUSTRIES ticket", body=""))["id"], "glo")
        self.assertEqual(r(self._mail(sender_email="x@nobody.com",
                                      subject="t", body="see globex industries"))["id"], "glo")

    def test_first_keyword_in_list_order_wins(self):
        orgs = [self._org("a", name="Acme Org"), self._org("g", name="Globex Org")]
        mappings = [{"keyword": "Globex", "org_id": "g"}, {"keyword": "Acme", "org_id": "a"}]
        org = customers.resolve_mail_org(
            self._mail(sender_email="x@nobody.com", body="Acme and Globex both appear"),
            orgs, mappings)
        self.assertEqual(org["id"], "g")

    def test_keyword_mapped_to_missing_org_falls_through(self):
        # Matched keyword -> deleted org: no brute-force hit, fall to sender (Acme rep).
        org = customers.resolve_mail_org(
            self._mail(sender_email="bob@acme.com", body="re: Globex Industries"),
            self.orgs, [{"keyword": "Globex Industries", "org_id": "deleted"}])
        self.assertEqual(org["id"], "acme")

    def test_unresolved_returns_none(self):
        self.assertIsNone(customers.resolve_mail_org(
            self._mail(sender_email="nobody@nowhere.com", body="x"), self.orgs))
        self.assertIsNone(customers.resolve_mail_org(
            self._mail(sender_email="/O=EX/CN=foo", body="x"), self.orgs))

    def test_display_vs_real_name(self):
        org = customers.resolve_mail_org(self._mail(sender_email="bob@acme.com"), self.orgs)
        self.assertEqual(customers.org_label(org), {"name": "Acme", "color": "#222222"})  # pill
        self.assertEqual(org["name"], "Acme Corp")  # download/CSV use the real name

    def test_keyword_and_operator_requires_both_terms(self):
        # ';' is AND: the mapping matches only when both terms appear in content.
        mappings = [{"keyword": "globex; invoice", "org_id": "glo"}]
        r = customers.mail_org_resolver(self.orgs, mappings)
        self.assertEqual(r(self._mail(sender_email="x@nobody.com",
                                      subject="Globex invoice #3", body=""))["id"], "glo")
        self.assertIsNone(r(self._mail(sender_email="x@nobody.com",
                                       subject="Globex ticket", body="")))

    def test_keyword_or_and_grouping(self):
        # 'a; [[b, c]]' == a AND (b OR c).
        mappings = [{"keyword": "globex; [[invoice, receipt]]", "org_id": "glo"}]
        r = customers.mail_org_resolver(self.orgs, mappings)
        self.assertEqual(r(self._mail(sender_email="x@nobody.com",
                                      body="globex receipt attached"))["id"], "glo")
        self.assertIsNone(r(self._mail(sender_email="x@nobody.com",
                                       body="globex statement attached")))

    def test_keyword_regex_term(self):
        mappings = [{"keyword": "<{(ticket-\\d+)}>", "org_id": "glo"}]
        r = customers.mail_org_resolver(self.orgs, mappings)
        self.assertEqual(r(self._mail(sender_email="x@nobody.com",
                                      subject="ref ticket-4821", body=""))["id"], "glo")
        self.assertIsNone(r(self._mail(sender_email="x@nobody.com",
                                       subject="ref ticket-none", body="")))

    def test_unparseable_keyword_is_skipped(self):
        # A bad regex must not raise; the mapping is dropped and the sender resolves.
        mappings = [{"keyword": "<{( ( )}>", "org_id": "glo"}]
        org = customers.resolve_mail_org(
            self._mail(sender_email="bob@acme.com", body="x"), self.orgs, mappings)
        self.assertEqual(org["id"], "acme")  # fell through to sender rep


if __name__ == "__main__":
    unittest.main()
