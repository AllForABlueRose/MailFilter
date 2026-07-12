"""Tests for mailfilter.bulk_compose: the shared planner behind Composer and Press.

``plan_for_mail`` is the single path to a draft (the mail is always chosen by the
user, so nothing is matched). ``match_row_to_mails`` survives only to bind an
uploaded spreadsheet row that carries no Entry ID back to a loaded mail item.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from mailfilter import bulk_compose

CC = "shared.services@example.com"   # the mailbox Press drafts from, passed in


def make_mail(**overrides):
    mail = {
        "id": "M1",
        "subject": "Quarterly invoice",
        "sender": "Alice Smith",
        "sender_email": "alice@acme.com",
        "recipient_emails": [CC, "bob@example.com"],
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


def _row(**kw):
    base = {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:00",
            "sender": "", "file_name": "inv.pdf", "uses_ftp": "Yes"}
    base.update(kw)
    return base


class PlanForMailTests(unittest.TestCase):
    """The render half: the caller already chose the mail, so nothing is matched."""

    def test_plans_the_mail_it_is_given(self):
        plan = bulk_compose.plan_for_mail(0, _row(), make_mail(), TEMPLATE, [], CC)
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["mail_id"], "M1")
        self.assertEqual(plan["match_count"], 1)

    def test_a_mail_the_row_would_never_match_is_still_planned(self):
        # Press picks the mail; the row is just data hung on it.
        mail = make_mail(subject="Something else", received="2020-01-01 00:00:00",
                         sender_email="stranger@nowhere.test")
        plan = bulk_compose.plan_for_mail(0, _row(), mail, TEMPLATE, [], CC)
        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["subject"], "RE: Something else")

    def test_a_template_that_fails_to_render_blocks_the_row(self):
        broken = {"body": "{{ upper( }}", "attachment_expr": "", "error": ""}
        plan = bulk_compose.plan_for_mail(0, _row(), make_mail(), broken, [], CC)
        self.assertEqual(plan["status"], "blocked")
        self.assertTrue(any("template error" in w for w in plan["warnings"]))

    def test_a_stored_template_error_blocks_unrendered(self):
        plan = bulk_compose.invalid_template_plan(0, _row(), "body: oops")
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["body"], "")
        self.assertIn("template is invalid", plan["warnings"][0])


class MissingVariableTests(unittest.TestCase):
    """A blank cell for a variable the template reads is a hole in the draft."""

    NEEDS_REF = {"body": "Ref {{ upper(row.ref) }}", "attachment_expr": "", "error": ""}

    def test_template_variables_lists_what_the_template_reads(self):
        tmpl = {"body": "{{ row.a }} {{ row.b }}", "attachment_expr": "row.c + \".pdf\"",
                "error": ""}
        self.assertEqual(bulk_compose.template_variables(tmpl), ["a", "b", "c"])

    def test_a_blank_needed_cell_blocks_the_row_before_rendering(self):
        plan = bulk_compose.plan_for_mail(0, _row(), make_mail(), self.NEEDS_REF, [], CC)
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["warnings"], ["missing row.ref"])
        self.assertEqual(plan["body"], "")   # never rendered with a hole in it

    def test_filling_the_cell_lets_it_render(self):
        plan = bulk_compose.plan_for_mail(0, _row(ref="acme-1"), make_mail(),
                                          self.NEEDS_REF, [], CC)
        self.assertEqual(plan["body"], "Ref ACME-1")

    def test_whitespace_only_counts_as_missing(self):
        self.assertEqual(bulk_compose.missing_variables(self.NEEDS_REF, {"ref": "   "}),
                         ["ref"])

    def test_a_variable_inside_a_string_literal_is_not_needed(self):
        tmpl = {"body": '{{ "row.fake" }}', "attachment_expr": "", "error": ""}
        self.assertEqual(bulk_compose.template_variables(tmpl), [])


class RecipientTests(unittest.TestCase):
    def _plan(self, cc_address=CC):
        return bulk_compose.plan_for_mail(0, _row(), make_mail(), TEMPLATE, [], cc_address)

    def test_reply_all_to_is_original_sender(self):
        self.assertEqual(self._plan()["to"], ["alice@acme.com"])

    def test_cc_includes_originals_and_the_drafting_mailbox(self):
        cc = self._plan()["cc"]
        self.assertIn("bob@example.com", cc)
        self.assertIn("carol@third.com", cc)
        self.assertIn(CC, cc)

    def test_sender_not_duplicated_in_cc(self):
        self.assertNotIn("alice@acme.com", self._plan()["cc"])

    def test_a_blank_cc_address_ccs_no_one_extra(self):
        # The user turned the Cc toggle off: only the original recipients remain.
        cc = self._plan(cc_address="")["cc"]
        self.assertIn("bob@example.com", cc)
        self.assertNotIn("nobody@nowhere.test", cc)

    def test_the_drafting_mailbox_is_not_duplicated(self):
        # It is already on the original's To line in make_mail().
        self.assertEqual(self._plan()["cc"].count(CC), 1)

    def test_reply_subject_prefixed(self):
        self.assertEqual(self._plan()["subject"], "RE: Quarterly invoice")

    def test_an_already_replied_subject_is_left_alone(self):
        plan = bulk_compose.plan_for_mail(0, _row(), make_mail(subject="RE: Invoice"),
                                          TEMPLATE, [], CC)
        self.assertEqual(plan["subject"], "RE: Invoice")


class TemplateRenderTests(unittest.TestCase):
    def test_ftp_branch_uses_link(self):
        plan = bulk_compose.plan_for_mail(0, _row(uses_ftp="Yes"), make_mail(),
                                          TEMPLATE, [], CC)
        self.assertTrue(plan["uses_ftp"])
        self.assertIn("Link: " + config.FTP_LINK_BASE + "inv.pdf", plan["body"])
        self.assertEqual(plan["ftp_link"], config.FTP_LINK_BASE + "inv.pdf")
        self.assertIsNone(plan["attachment"])

    def test_sender_first_name_in_body(self):
        plan = bulk_compose.plan_for_mail(0, _row(), make_mail(), TEMPLATE, [], CC)
        self.assertIn("Dear Alice,", plan["body"])


class RowForMailTests(unittest.TestCase):
    """The row a cache mail would have had -- it never came from a spreadsheet."""

    def test_synthesized_from_the_mail_itself(self):
        mail = make_mail(attachments=[{"filename": "report.pdf"}])
        row = bulk_compose.row_for_mail(mail)
        self.assertEqual(row["subject"], "Quarterly invoice")
        self.assertEqual(row["datetime"], "2026-06-20 14:30:00")
        self.assertEqual(row["sender"], "Alice Smith")
        self.assertEqual(row["file_name"], "report.pdf")
        self.assertEqual(row["uses_ftp"], "")

    def test_no_attachment_means_a_blank_file_name(self):
        self.assertEqual(bulk_compose.row_for_mail(make_mail())["file_name"], "")


class SenderClassificationTests(unittest.TestCase):
    def test_internal_domain_flag(self):
        # The internal-domain set is computed by customers.internal_domains and passed
        # in; this module never decides it for itself.
        ctx = bulk_compose._sender_context(make_mail(sender_email="x@mycorp.com"), [],
                                           internal=frozenset({"mycorp.com"}))
        self.assertTrue(ctx["is_internal"])

    def test_external_domain_flag(self):
        ctx = bulk_compose._sender_context(make_mail(sender_email="x@acme.com"), [],
                                           internal=frozenset({"mycorp.com"}))
        self.assertFalse(ctx["is_internal"])

    def test_nobody_is_internal_without_an_internal_set(self):
        # No verified mailbox and no Root/Partner orgs: everyone reads as external,
        # which is the honest answer rather than a guess.
        ctx = bulk_compose._sender_context(make_mail(sender_email="x@mycorp.com"), [])
        self.assertFalse(ctx["is_internal"])

    def test_a_blank_sender_is_never_internal(self):
        ctx = bulk_compose._sender_context(make_mail(sender_email=""), [],
                                           internal=frozenset({"mycorp.com"}))
        self.assertFalse(ctx["is_internal"])

    def test_org_resolution_via_customers(self):
        orgs = [{"id": "o1", "name": "Acme", "category": "VIP",
                 "domains": [{"domain": "acme.com", "role": "member"}], "contacts": []}]
        ctx = bulk_compose._sender_context(make_mail(sender_email="x@acme.com"), orgs)
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
                plan = bulk_compose.plan_for_mail(0, _row(uses_ftp=""), make_mail(),
                                                  TEMPLATE, [], CC)
                self.assertEqual(plan["status"], "ready")
                self.assertEqual(plan["attachment"]["name"], "inv.pdf")
                self.assertTrue(plan["attachment"]["exists"])

    def test_missing_file_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(config, "FILE_SERVER_DIR", d):
                plan = bulk_compose.plan_for_mail(0, _row(uses_ftp="", file_name="ghost.pdf"),
                                                  make_mail(), TEMPLATE, [], CC)
                self.assertEqual(plan["status"], "blocked")
                self.assertTrue(any("file not found" in w for w in plan["warnings"]))

    def test_attachment_expr_builds_name(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "INV-2026.pdf").write_text("x")
            tmpl = {"body": "ok", "attachment_expr": 'upper(row.ref) + ".pdf"', "error": ""}
            with mock.patch.object(config, "FILE_SERVER_DIR", d):
                plan = bulk_compose.plan_for_mail(0, _row(uses_ftp="", ref="inv-2026"),
                                                  make_mail(), tmpl, [], CC)
                self.assertEqual(plan["attachment"]["name"], "INV-2026.pdf")
                self.assertEqual(plan["status"], "ready")


class MatchRowToMailsTests(unittest.TestCase):
    """The matcher survives for ONE job: binding an uploaded row that has no Entry ID."""

    def test_exact_match(self):
        matched = bulk_compose.match_row_to_mails(
            {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:00",
             "sender": "alice@acme.com"}, [make_mail()])
        self.assertEqual([m["id"] for m in matched], ["M1"])

    def test_subject_prefix_is_ignored(self):
        matched = bulk_compose.match_row_to_mails(
            {"subject": "RE: Quarterly invoice", "datetime": "2026-06-20 14:30:00",
             "sender": ""}, [make_mail()])
        self.assertEqual(len(matched), 1)

    def test_no_match(self):
        matched = bulk_compose.match_row_to_mails(
            {"subject": "Nonexistent", "datetime": "", "sender": ""}, [make_mail()])
        self.assertEqual(matched, [])

    def test_ambiguous_match_returns_them_all(self):
        mails = [make_mail(id="M1"), make_mail(id="M2")]
        matched = bulk_compose.match_row_to_mails(
            {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:00",
             "sender": "alice"}, mails)
        self.assertEqual(len(matched), 2)   # the caller reports it unbound, never guesses

    def test_datetime_tolerance(self):
        row = {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:30",
               "sender": ""}
        self.assertEqual(len(bulk_compose.match_row_to_mails(row, [make_mail()])), 1)
        self.assertEqual(
            len(bulk_compose.match_row_to_mails(row, [make_mail()], tolerance=5)), 0)

    def test_sender_criterion_filters(self):
        matched = bulk_compose.match_row_to_mails(
            {"subject": "Quarterly invoice", "datetime": "2026-06-20 14:30:00",
             "sender": "someone-else@nope.com"}, [make_mail()])
        self.assertEqual(matched, [])


if __name__ == "__main__":
    unittest.main()
