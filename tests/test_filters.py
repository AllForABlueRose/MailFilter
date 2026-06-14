"""Tests for mailfilter.filters: query building and the filtering predicate.

The expression grammar itself is covered in test_expr.py; here we test that
each field is wired to the right derived text and combined correctly.
"""

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
    def test_parses_fields_into_nodes(self):
        q = MailQuery.from_args({"main": "a, b", "sender": "alice"})
        self.assertIsNotNone(q.main)
        self.assertIsNotNone(q.sender)
        self.assertIsNone(q.optional)   # absent -> None
        self.assertEqual(q.errors, ())

    def test_collects_parse_errors(self):
        q = MailQuery.from_args({"main": "a;"})  # trailing operator
        self.assertTrue(q.errors)
        self.assertIn("main", q.errors[0])
        self.assertIsNone(q.main)

    def test_parses_blacklist_fields(self):
        q = MailQuery.from_args({"attachment_blacklist": ".exe", "links_blacklist": "track"})
        self.assertIsNotNone(q.attachment_blacklist)
        self.assertIsNotNone(q.links_blacklist)
        self.assertEqual(q.errors, ())

    def test_resources_flag_variants(self):
        for val in ("1", "true", "on"):
            self.assertTrue(MailQuery.from_args({"resources": val}).resources_only)
        for val in ("", "0", "off"):
            self.assertFalse(MailQuery.from_args({"resources": val}).resources_only)

    def test_defaults_when_absent(self):
        q = MailQuery.from_args({})
        self.assertIsNone(q.main)
        self.assertIsNone(q.start)
        self.assertFalse(q.resources_only)
        self.assertEqual(q.errors, ())


class FilterMailsTests(unittest.TestCase):
    def setUp(self):
        # Disjoint vocabularies so each assertion is unambiguous.
        self.mails = [
            _derived(
                id="a", subject="server", body="alpha beta",
                sender="Alice", sender_email="alice@x.com",
                recipient_names=["Bob"], recipient_emails=["bob@x.com"],
                attachments=[{"filename": "f.pdf"}],
                received="2026-06-10 09:00:00",
            ),
            _derived(
                id="b", subject="newsletter", body="gamma",
                sender="Carol", sender_email="carol@x.com",
                recipient_names=["Dave"], recipient_emails=["dave@x.com"],
                attachments=[],
                received="2026-06-11 09:00:00",
            ),
        ]

    def _ids(self, args):
        q = MailQuery.from_args(args)
        self.assertEqual(q.errors, ())
        return [m["id"] for m in filter_mails(self.mails, q)]

    def test_empty_query_returns_all_in_order(self):
        self.assertEqual(self._ids({}), ["a", "b"])

    def test_main_or(self):
        self.assertEqual(self._ids({"main": "server, gamma"}), ["a", "b"])
        self.assertEqual(self._ids({"main": "server"}), ["a"])

    def test_main_and(self):
        self.assertEqual(self._ids({"main": "server; alpha"}), ["a"])
        self.assertEqual(self._ids({"main": "server; gamma"}), [])

    def test_main_grouping(self):
        # (server OR newsletter) AND alpha  -> only 'a'
        self.assertEqual(self._ids({"main": "[[server, newsletter]]; alpha"}), ["a"])

    def test_main_ignores_sender_and_recipient(self):
        # 'alice' is a's sender and 'bob' a's recipient — keyword search sees neither.
        self.assertEqual(self._ids({"main": "alice"}), [])
        self.assertEqual(self._ids({"main": "bob"}), [])

    def test_exclude_ignores_sender_and_recipient(self):
        self.assertEqual(self._ids({"exclude": "alice"}), ["a", "b"])
        self.assertEqual(self._ids({"exclude": "bob"}), ["a", "b"])

    def test_main_ignores_attachment_names(self):
        # A keyword present only in an attachment name must not filter the mail.
        m = _derived(id="z", subject="hi", body="plain text",
                     attachments=[{"filename": "secret_report.pdf"}])
        q = MailQuery.from_args({"main": "secret"})
        self.assertEqual([x["id"] for x in filter_mails([m], q)], [])

    def test_main_regex(self):
        self.assertEqual(self._ids({"main": "<{(serv\\w+)}>"}), ["a"])

    def test_exclude_expression(self):
        self.assertEqual(self._ids({"exclude": "newsletter"}), ["a"])
        # exclude when BOTH present -> drops 'a', keeps 'b'
        self.assertEqual(self._ids({"exclude": "alpha; server"}), ["b"])

    def test_sender_expression(self):
        self.assertEqual(self._ids({"sender": "alice, carol"}), ["a", "b"])
        self.assertEqual(self._ids({"sender": "alice"}), ["a"])

    def test_recipient_expression(self):
        self.assertEqual(self._ids({"recipient": "bob"}), ["a"])

    def test_exclude_sender(self):
        self.assertEqual(self._ids({"exclude_sender": "alice"}), ["b"])

    def test_exclude_recipient(self):
        self.assertEqual(self._ids({"exclude_recipient": "bob"}), ["b"])

    def test_recipient_matches_cc(self):
        m = _derived(id="c", cc_names=["Eve"], cc_emails=["eve@x.com"])
        q = MailQuery.from_args({"recipient": "eve"})
        self.assertEqual([x["id"] for x in filter_mails([m], q)], ["c"])

    def test_resources_only(self):
        self.assertEqual(self._ids({"resources": "1"}), ["a"])

    def test_date_range_inclusive(self):
        self.assertEqual(self._ids({"start": "2026-06-11T00:00"}), ["b"])


if __name__ == "__main__":
    unittest.main()
