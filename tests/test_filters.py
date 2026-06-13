"""Tests for mailfilter.filters: query parsing and the filtering predicate."""

import unittest
from datetime import datetime

from mailfilter.filters import MailQuery, filter_mails, parse_datetime
from mailfilter.store import MailStore
from tests.factories import make_mail


def _derived(**overrides):
    return MailStore._with_derived(make_mail(**overrides))


class ParseDatetimeTests(unittest.TestCase):
    def test_valid_iso(self):
        self.assertEqual(parse_datetime("2026-06-10T09:30"), datetime(2026, 6, 10, 9, 30))

    def test_empty_and_invalid(self):
        self.assertIsNone(parse_datetime(""))
        self.assertIsNone(parse_datetime(None))
        self.assertIsNone(parse_datetime("not-a-date"))


class FromArgsTests(unittest.TestCase):
    def test_lowercasing_and_case_preservation(self):
        q = MailQuery.from_args({
            "main": "Server, ERROR",
            "optional": "Urgent, Critical",
            "exclude": "Spam",
            "sender": "Alice",
            "recipient": "BOB",
            "resources": "1",
        })
        self.assertEqual(q.main, ["server", "error"])
        self.assertEqual(q.exclude, ["spam"])
        self.assertEqual(q.sender, "alice")
        self.assertEqual(q.recipient, "bob")
        self.assertEqual(q.optional, ["Urgent", "Critical"])  # original case
        self.assertTrue(q.resources_only)

    def test_keyword_splitting_trims_and_drops_blanks(self):
        q = MailQuery.from_args({"main": " a , , b ,"})
        self.assertEqual(q.main, ["a", "b"])

    def test_resources_flag_variants(self):
        for val in ("1", "true", "on"):
            self.assertTrue(MailQuery.from_args({"resources": val}).resources_only)
        for val in ("", "0", "off", "no"):
            self.assertFalse(MailQuery.from_args({"resources": val}).resources_only)

    def test_defaults_when_absent(self):
        q = MailQuery.from_args({})
        self.assertEqual(q.main, [])
        self.assertIsNone(q.start)
        self.assertFalse(q.resources_only)


class FilterMailsTests(unittest.TestCase):
    def setUp(self):
        self.mails = [
            _derived(id="a", subject="server error", received="2026-06-10 09:00:00"),
            _derived(
                id="b", subject="weekly newsletter", body="no links here",
                attachments=[], received="2026-06-11 09:00:00",
                sender="Carol", sender_email="carol@x.com",
            ),
        ]

    def _ids(self, query):
        return [m["id"] for m in filter_mails(self.mails, query)]

    def test_empty_query_returns_all_in_order(self):
        self.assertEqual(self._ids(MailQuery()), ["a", "b"])

    def test_date_range_inclusive(self):
        q = MailQuery(start=datetime(2026, 6, 11), end=datetime(2026, 6, 12))
        self.assertEqual(self._ids(q), ["b"])

    def test_main_keyword_any_of(self):
        self.assertEqual(self._ids(MailQuery(main=["server"])), ["a"])
        self.assertEqual(self._ids(MailQuery(main=["server", "newsletter"])), ["a", "b"])
        self.assertEqual(self._ids(MailQuery(main=["nope"])), [])

    def test_exclude_keyword(self):
        self.assertEqual(self._ids(MailQuery(exclude=["newsletter"])), ["a"])

    def test_sender_substring(self):
        self.assertEqual(self._ids(MailQuery(sender="carol")), ["b"])
        self.assertEqual(self._ids(MailQuery(sender="alice")), ["a"])

    def test_recipient_substring(self):
        self.assertEqual(self._ids(MailQuery(recipient="bob jones")), ["a", "b"])

    def test_resources_only(self):
        # 'a' has links+attachment; 'b' has neither.
        self.assertEqual(self._ids(MailQuery(resources_only=True)), ["a"])


if __name__ == "__main__":
    unittest.main()
