"""Tests for mailfilter.bulk_compose: row<->mail matching, sender classification,
template rendering, attachment/FTP resolution, and reply-all recipients."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from mailfilter import bulk_compose


def make_shared_mail(**overrides):
    mail = {
        "id": "M1",
        "subject": "Quarterly invoice",
        "sender": "Alice Smith",
        "sender_email": "alice@acme.com",
        "recipient_emails": ["shared.services@example.com", "bob@example.com"],
        "cc_emails": ["carol@third.com"],
        "received": "2026-06-20 14:30:00",
    }
    mail.update(overrides)
    return mail


TEMPLATE = {
    "body": ("Dear {{ default(sender.first_name, \"Sir/Madam\") }},\n"
             "{% if row.uses_ftp %}Link: {{ ftp_link(row.file_name) }}"
             "{% else %}Attached: {{ row.file_name }}{% endif %}"),
    "attachment_expr": "",
    "error": "",
}


class MatchingTests(unittest.TestCase):
    def test_exact_match_is_ready(self):
        row = {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:00",
               "sender": "alice@acme.com", "file_name": "inv.pdf", "uses_ftp": "Yes"}
        out = bulk_compose.plan_all([row], [make_shared_mail()], TEMPLATE, [])
        plan = out["plans"][0]
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["mail_id"], "M1")
        self.assertEqual(out["summary"], {"total": 1, "ready": 1, "blocked": 0})

    def test_subject_prefix_is_ignored_in_matching(self):
        row = {"subject": "RE: Quarterly invoice", "datetime": "2026-06-20 14:30:00",
               "sender": "", "uses_ftp": "Yes", "file_name": "x"}
        plan = bulk_compose.plan_all([row], [make_shared_mail()], TEMPLATE, [])["plans"][0]
        self.assertEqual(plan["match_count"], 1)

    def test_no_match_is_blocked(self):
        row = {"subject": "Nonexistent", "datetime": "", "sender": ""}
        plan = bulk_compose.plan_all([row], [make_shared_mail()], TEMPLATE, [])["plans"][0]
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["match_count"], 0)
        self.assertIn("no matching mail", plan["warnings"][0])

    def test_ambiguous_match_is_blocked(self):
        mails = [make_shared_mail(id="M1"), make_shared_mail(id="M2")]
        row = {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:00",
               "sender": "alice"}
        plan = bulk_compose.plan_all([row], mails, TEMPLATE, [])["plans"][0]
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["match_count"], 2)

    def test_datetime_tolerance(self):
        row = {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:30",
               "sender": "", "uses_ftp": "Yes", "file_name": "x"}
        # 30s drift, default tolerance 60s -> still matches.
        plan = bulk_compose.plan_all([row], [make_shared_mail()], TEMPLATE, [])["plans"][0]
        self.assertEqual(plan["match_count"], 1)
        # Tighten tolerance to 5s -> no match.
        plan2 = bulk_compose.plan_all([row], [make_shared_mail()], TEMPLATE, [],
                                      tolerance=5)["plans"][0]
        self.assertEqual(plan2["match_count"], 0)

    def test_sender_criterion_filters(self):
        row = {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:00",
               "sender": "someone-else@nope.com"}
        plan = bulk_compose.plan_all([row], [make_shared_mail()], TEMPLATE, [])["plans"][0]
        self.assertEqual(plan["match_count"], 0)


class RecipientTests(unittest.TestCase):
    def _plan(self):
        row = {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:00",
               "sender": "", "uses_ftp": "Yes", "file_name": "x"}
        return bulk_compose.plan_all([row], [make_shared_mail()], TEMPLATE, [])["plans"][0]

    def test_reply_all_to_is_original_sender(self):
        self.assertEqual(self._plan()["to"], ["alice@acme.com"])

    def test_cc_includes_originals_and_shared(self):
        cc = self._plan()["cc"]
        self.assertIn("bob@example.com", cc)
        self.assertIn("carol@third.com", cc)
        self.assertIn(config.SHARED_MAILBOX_ADDRESS, cc)

    def test_sender_not_duplicated_in_cc(self):
        self.assertNotIn("alice@acme.com", self._plan()["cc"])

    def test_reply_subject_prefixed(self):
        self.assertEqual(self._plan()["subject"], "RE: Quarterly invoice")


class TemplateRenderTests(unittest.TestCase):
    def _row(self, **kw):
        base = {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:00",
                "sender": "", "file_name": "inv.pdf"}
        base.update(kw)
        return base

    def test_ftp_branch_uses_link(self):
        plan = bulk_compose.plan_all([self._row(uses_ftp="Yes")],
                                     [make_shared_mail()], TEMPLATE, [])["plans"][0]
        self.assertTrue(plan["uses_ftp"])
        self.assertIn("Link: " + config.FTP_LINK_BASE + "inv.pdf", plan["body"])
        self.assertEqual(plan["ftp_link"], config.FTP_LINK_BASE + "inv.pdf")
        self.assertIsNone(plan["attachment"])

    def test_sender_first_name_in_body(self):
        plan = bulk_compose.plan_all([self._row(uses_ftp="Yes")],
                                     [make_shared_mail()], TEMPLATE, [])["plans"][0]
        self.assertIn("Dear Alice,", plan["body"])

    def test_template_error_blocks_all_rows(self):
        bad = {"body": "{% if row.a %}unterminated", "attachment_expr": "", "error": "body: oops"}
        plan = bulk_compose.plan_all([self._row(uses_ftp="Yes")],
                                     [make_shared_mail()], bad, [])["plans"][0]
        self.assertEqual(plan["status"], "blocked")
        self.assertIn("template is invalid", plan["warnings"][0])


class SenderClassificationTests(unittest.TestCase):
    def test_internal_domain_flag(self):
        ctx = bulk_compose._sender_context(make_shared_mail(sender_email="x@example.com"), [])
        self.assertTrue(ctx["is_internal"])

    def test_external_domain_flag(self):
        ctx = bulk_compose._sender_context(make_shared_mail(sender_email="x@acme.com"), [])
        self.assertFalse(ctx["is_internal"])

    def test_org_resolution_via_customers(self):
        orgs = [{"id": "o1", "name": "Acme", "category": "VIP",
                 "domains": [{"domain": "acme.com", "role": "member"}], "contacts": []}]
        ctx = bulk_compose._sender_context(make_shared_mail(sender_email="x@acme.com"), orgs)
        self.assertEqual(ctx["org"], "Acme")
        self.assertEqual(ctx["role"], "member")


class AttachmentResolutionTests(unittest.TestCase):
    def test_traversal_is_blocked(self):
        _p, exists, error = bulk_compose.resolve_attachment_path("../secret.txt")
        self.assertFalse(exists)
        self.assertIn("escapes", error)

    def test_blank_name_error(self):
        _p, _e, error = bulk_compose.resolve_attachment_path("  ")
        self.assertEqual(error, "no file name")

    def test_existing_file_marks_ready(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "inv.pdf").write_text("data")
            with mock.patch.object(config, "FILE_SERVER_DIR", d):
                row = {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:00",
                       "sender": "", "file_name": "inv.pdf"}  # no FTP -> attach
                plan = bulk_compose.plan_all([row], [make_shared_mail()], TEMPLATE, [])["plans"][0]
                self.assertEqual(plan["status"], "ready")
                self.assertEqual(plan["attachment"]["name"], "inv.pdf")
                self.assertTrue(plan["attachment"]["exists"])

    def test_missing_file_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(config, "FILE_SERVER_DIR", d):
                row = {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:00",
                       "sender": "", "file_name": "ghost.pdf"}
                plan = bulk_compose.plan_all([row], [make_shared_mail()], TEMPLATE, [])["plans"][0]
                self.assertEqual(plan["status"], "blocked")
                self.assertTrue(any("file not found" in w for w in plan["warnings"]))

    def test_attachment_expr_builds_name(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "INV-2026.pdf").write_text("x")
            tmpl = {"body": "ok", "attachment_expr": 'upper(row.ref) + ".pdf"', "error": ""}
            with mock.patch.object(config, "FILE_SERVER_DIR", d):
                row = {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:00",
                       "sender": "", "ref": "inv-2026"}
                plan = bulk_compose.plan_all([row], [make_shared_mail()], tmpl, [])["plans"][0]
                self.assertEqual(plan["attachment"]["name"], "INV-2026.pdf")
                self.assertEqual(plan["status"], "ready")


class PlanForMailTests(unittest.TestCase):
    """The render half, split out of plan_row so Composer can preview against a mail
    the user picked (nothing to match). Press and Composer must agree, so what
    plan_row produces after matching has to equal plan_for_mail on the same mail."""

    ROW = {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:00",
           "sender": "alice@acme.com", "file_name": "inv.pdf", "uses_ftp": "Yes"}

    def test_matches_what_plan_row_produces_for_the_same_mail(self):
        mail = make_shared_mail()
        via_row = bulk_compose.plan_row(0, self.ROW, [mail], TEMPLATE, [], 60)
        direct = bulk_compose.plan_for_mail(0, self.ROW, mail, TEMPLATE, [])
        self.assertEqual(via_row, direct)
        self.assertEqual(direct["status"], "ready")

    def test_skips_matching_entirely(self):
        # A mail the row's subject/datetime/sender would never match is still planned:
        # the caller already chose it.
        mail = make_shared_mail(subject="Something else entirely",
                                received="2020-01-01 00:00:00",
                                sender_email="stranger@nowhere.test")
        plan = bulk_compose.plan_for_mail(0, self.ROW, mail, TEMPLATE, [])
        self.assertEqual(plan["match_count"], 1)
        self.assertEqual(plan["mail_id"], "M1")
        self.assertEqual(plan["subject"], "RE: Something else entirely")

    def test_reply_all_recipients_always_cc_the_shared_mailbox(self):
        plan = bulk_compose.plan_for_mail(0, self.ROW, make_shared_mail(), TEMPLATE, [])
        self.assertEqual(plan["to"], ["alice@acme.com"])
        self.assertIn(config.SHARED_MAILBOX_ADDRESS, plan["cc"])
        self.assertNotIn("alice@acme.com", plan["cc"])

    def test_a_template_error_blocks_the_row(self):
        broken = {"body": "{{ upper( }}", "attachment_expr": "", "error": ""}
        plan = bulk_compose.plan_for_mail(0, self.ROW, make_shared_mail(), broken, [])
        self.assertEqual(plan["status"], "blocked")
        self.assertTrue(any("template error" in w for w in plan["warnings"]))


class InvalidTemplatePlanTests(unittest.TestCase):
    def test_a_stored_error_blocks_every_row_unrendered(self):
        rows = [{"subject": "a", "uses_ftp": "Yes"}, {"subject": "b"}]
        broken = {"body": "ok", "attachment_expr": "", "error": "body: bad"}
        out = bulk_compose.plan_all(rows, [make_shared_mail()], broken, [])
        self.assertEqual(out["summary"], {"total": 2, "ready": 0, "blocked": 2})
        for plan in out["plans"]:
            self.assertEqual(plan["body"], "")
            self.assertIn("template is invalid", plan["warnings"][0])


if __name__ == "__main__":
    unittest.main()
