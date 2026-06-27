"""Tests for mailfilter.workspace_ops: the batch-attachment naming, including the
experimental "Append Customer Name To Downloads" toggle.

Outlook is mocked (the bytes fetch) and WORKSPACE_DIR is redirected to a temp
folder, so nothing touches Outlook or the real workspace.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from mailfilter import workspace_ops
from mailfilter.store import MailStore
from tests.factories import make_mail


class AppendStemTests(unittest.TestCase):
    def test_inserts_before_extension(self):
        self.assertEqual(workspace_ops.append_stem("report.pdf", "Acme Corp"),
                         "report_Acme Corp.pdf")

    def test_no_extension(self):
        self.assertEqual(workspace_ops.append_stem("README", "Acme"), "README_Acme")

    def test_only_last_extension_is_kept_outside(self):
        self.assertEqual(workspace_ops.append_stem("archive.tar.gz", "Acme"),
                         "archive.tar_Acme.gz")


class _FakeStore:
    def __init__(self, mails):
        self._mails = [MailStore._with_derived(m) for m in mails]

    def snapshot(self):
        return list(self._mails)


class SenderOrgNamesTests(unittest.TestCase):
    def setUp(self):
        self.store = _FakeStore([
            make_mail(id="m1", sender_email="bob@acme.com"),
            make_mail(id="m2", sender_email="eve@nobody.com"),
            make_mail(id="m3", sender_email="sue@acme.com"),
        ])
        self.items = [{"id": "m1", "index": 0}, {"id": "m2", "index": 0},
                      {"id": "m3", "index": 0}]

    def test_representative_preferred_over_member(self):
        # acme.com is a member of Base Inc but represents Acme Corp — rep wins.
        orgs = [
            {"id": "1", "name": "Base Inc",
             "domains": [{"domain": "acme.com", "role": "member"}], "contacts": []},
            {"id": "2", "name": "Acme Corp",
             "domains": [{"domain": "acme.com", "role": "representative"}], "contacts": []},
        ]
        names = workspace_ops._sender_org_names(self.store, self.items, orgs)
        self.assertEqual(names.get("m1"), "Acme Corp")
        self.assertEqual(names.get("m3"), "Acme Corp")

    def test_member_used_when_no_representative(self):
        orgs = [{"id": "1", "name": "Acme Corp",
                 "domains": [{"domain": "acme.com", "role": "member"}], "contacts": []}]
        self.assertEqual(workspace_ops._sender_org_names(self.store, self.items, orgs).get("m1"),
                         "Acme Corp")

    def test_unresolved_sender_absent(self):
        orgs = [{"id": "1", "name": "Acme Corp",
                 "domains": [{"domain": "acme.com", "role": "member"}], "contacts": []}]
        names = workspace_ops._sender_org_names(self.store, self.items, orgs)
        self.assertNotIn("m2", names)  # eve@nobody.com belongs to no org


class SaveAttachmentsAppendTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = Path(self._tmp) / "workspace"
        self.store = _FakeStore([
            make_mail(id="m1", sender_email="bob@acme.com",
                      attachments=[{"filename": "report.pdf"}]),
            make_mail(id="m2", sender_email="eve@nobody.com",
                      attachments=[{"filename": "report.pdf"}]),
        ])
        self.orgs = [{"id": "1", "name": "Acme Corp",
                      "domains": [{"domain": "acme.com", "role": "representative"}],
                      "contacts": []}]

    def tearDown(self):
        config.WORKSPACE_DIR = self._orig

    def _save(self, append):
        items = [{"id": "m1", "index": 0}, {"id": "m2", "index": 0}]
        with mock.patch("mailfilter.outlook.fetch_attachment",
                        side_effect=lambda mid, idx: ("report.pdf", b"bytes")):
            return workspace_ops.save_attachments(
                self.store, items, append_org_name=append, orgs=self.orgs)

    def test_off_leaves_names_unchanged(self):
        _folder, saved, _errors = self._save(append=False)
        # Both are "report.pdf"; the second collides and is deduped, neither gets _org.
        self.assertEqual(sorted(s["name"] for s in saved), ["report.pdf", "report_1.pdf"])

    def test_on_appends_org_for_resolved_sender_only(self):
        _folder, saved, _errors = self._save(append=True)
        by_id = {s["id"]: s["name"] for s in saved}
        self.assertEqual(by_id["m1"], "report_Acme Corp.pdf")  # sender in an org
        self.assertEqual(by_id["m2"], "report.pdf")            # sender in no org


class CustomerNameMatchesTests(unittest.TestCase):
    """The Suspected Customers List content matcher."""

    def setUp(self):
        self.store = _FakeStore([
            make_mail(id="m1", subject="Re: Globex Industries account", body="hi"),
            make_mail(id="m2", subject="ticket", body="mentions globex industries here"),
            make_mail(id="m3", subject="ticket", body="nothing relevant"),
        ])
        self.items = [{"id": "m1", "index": 0}, {"id": "m2", "index": 0},
                      {"id": "m3", "index": 0}]

    def test_matches_in_subject_or_body_case_insensitively(self):
        out = workspace_ops._customer_name_matches(
            self.store, self.items, ["Globex Industries"])
        self.assertEqual(out.get("m1"), "Globex Industries")  # in subject
        self.assertEqual(out.get("m2"), "Globex Industries")  # in body, lowercased
        self.assertNotIn("m3", out)                            # no match

    def test_first_name_in_list_order_wins(self):
        m = _FakeStore([make_mail(id="x", body="Acme and Globex both appear")])
        out = workspace_ops._customer_name_matches(
            m, [{"id": "x", "index": 0}], ["Globex", "Acme"])
        self.assertEqual(out["x"], "Globex")  # earlier in the list, not first in text

    def test_skip_ids_are_excluded(self):
        out = workspace_ops._customer_name_matches(
            self.store, self.items, ["Globex Industries"], skip={"m1"})
        self.assertNotIn("m1", out)
        self.assertIn("m2", out)

    def test_empty_list_matches_nothing(self):
        self.assertEqual(
            workspace_ops._customer_name_matches(self.store, self.items, []), {})


class SaveAttachmentsResolveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = Path(self._tmp) / "workspace"
        self.store = _FakeStore([
            # m1: keyword in content, sender in no org -> keyword applies.
            make_mail(id="m1", sender_email="x@nobody.com",
                      body="about Globex Industries", attachments=[{"filename": "a.pdf"}]),
            # m2: keyword in content AND sender in an org -> org wins, keyword skipped.
            make_mail(id="m2", sender_email="bob@acme.com",
                      body="about Globex Industries", attachments=[{"filename": "a.pdf"}]),
        ])
        self.orgs = [{"id": "1", "name": "Acme Corp",
                      "domains": [{"domain": "acme.com", "role": "representative"}],
                      "contacts": []}]

    def tearDown(self):
        config.WORKSPACE_DIR = self._orig

    def _save(self, **kw):
        items = [{"id": "m1", "index": 0}, {"id": "m2", "index": 0}]
        with mock.patch("mailfilter.outlook.fetch_attachment",
                        side_effect=lambda mid, idx: ("a.pdf", b"bytes")):
            return workspace_ops.save_attachments(self.store, items, **kw)

    def test_resolve_works_on_its_own(self):
        _folder, saved, _errors = self._save(
            resolve_customer=True, customer_names=["Globex Industries"])
        # Both mails match the keyword; the identical names collide and are deduped.
        self.assertEqual(sorted(s["name"] for s in saved),
                         ["a_Globex Industries.pdf", "a_Globex Industries_1.pdf"])

    def test_org_takes_priority_and_skips_keyword(self):
        _folder, saved, _errors = self._save(
            append_org_name=True, orgs=self.orgs,
            resolve_customer=True, customer_names=["Globex Industries"])
        by_id = {s["id"]: s["name"] for s in saved}
        self.assertEqual(by_id["m1"], "a_Globex Industries.pdf")  # no org -> keyword
        self.assertEqual(by_id["m2"], "a_Acme Corp.pdf")          # org wins


if __name__ == "__main__":
    unittest.main()
