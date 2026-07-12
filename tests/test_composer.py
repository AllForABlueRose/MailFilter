"""Unit tests for the pure Composer read-side (mailfilter/composer.py).

Covers the three catalogues (samples, function blocks, picker filters), the row
synthesized for a cache mail, and the preview delegating to the same planner Press
uses. No Flask, no Outlook.
"""

import unittest

import config
from mailfilter import bulk_compose, composer, template_lang

from tests.factories import make_mail


class SampleTests(unittest.TestCase):
    def test_ten_samples_with_unique_ids(self):
        self.assertEqual(len(composer.SAMPLES), 10)
        ids = [s["id"] for s in composer.SAMPLES]
        self.assertEqual(len(set(ids)), 10)

    def test_every_sample_is_well_formed(self):
        for s in composer.SAMPLES:
            with self.subTest(sample=s["id"]):
                for key in ("id", "emoji", "label", "note", "mail", "row"):
                    self.assertIn(key, s)
                for key in ("id", "subject", "sender", "sender_email", "received",
                            "recipient_emails", "cc_emails", "body"):
                    self.assertIn(key, s["mail"])
                # The row carries the canonical BULK_COLUMNS aliases the DSL expects.
                for key in ("subject", "datetime", "sender", "file_name", "uses_ftp"):
                    self.assertIn(key, s["row"])

    def test_sample_received_parses_as_a_cache_datetime(self):
        for s in composer.SAMPLES:
            with self.subTest(sample=s["id"]):
                self.assertIsNotNone(bulk_compose._parse_dt(s["mail"]["received"]))

    def test_lookup_by_id(self):
        self.assertEqual(composer.sample("sample-ftp")["id"], "sample-ftp")
        self.assertIsNone(composer.sample("nope"))

    def test_samples_cover_both_the_ftp_and_the_attachment_branch(self):
        ftp = [s for s in composer.SAMPLES if template_lang.truthy(s["row"]["uses_ftp"])]
        attached = [s for s in composer.SAMPLES
                    if not template_lang.truthy(s["row"]["uses_ftp"])]
        self.assertTrue(ftp, "no sample takes the FTP branch")
        self.assertTrue(attached, "no sample takes the attachment branch")

    def test_an_internal_and_an_external_sender_are_both_represented(self):
        domains = [s["mail"]["sender_email"].rsplit("@", 1)[-1] for s in composer.SAMPLES]
        self.assertTrue(any(d in config.INTERNAL_DOMAINS for d in domains))
        self.assertTrue(any(d not in config.INTERNAL_DOMAINS for d in domains))

    def test_every_sample_renders_through_a_real_template(self):
        body = ('Dear {{ default(sender.first_name, "Sir/Madam") }},\n'
                "{% if row.uses_ftp %}Link: {{ ftp_link(row.file_name) }}"
                "{% else %}Attached: {{ row.file_name }}{% endif %}")
        template = {"body": body, "attachment_expr": "", "error": ""}
        for s in composer.SAMPLES:
            with self.subTest(sample=s["id"]):
                plan = composer.preview(template, s["mail"], s["row"], [])
                self.assertTrue(plan["body"])
                # The shared mailbox is always CC'd, exactly as Press would draft it.
                self.assertIn(config.SHARED_MAILBOX_ADDRESS, plan["cc"])


class BlockTests(unittest.TestCase):
    def test_ten_blocks_with_unique_ids(self):
        self.assertEqual(len(composer.BLOCKS), 10)
        self.assertEqual(len({b["id"] for b in composer.BLOCKS}), 10)

    def test_every_block_demo_renders_through_the_real_dsl(self):
        # The palette's advertised output is produced by template_lang, not
        # hardcoded -- so it cannot drift from what the DSL actually does.
        for block in composer.render_blocks():
            with self.subTest(block=block["id"]):
                self.assertNotIn("(error:", block["demo_output"])
                self.assertTrue(block["demo_output"].strip())

    def test_block_demo_output_matches_a_direct_render(self):
        for block in composer.render_blocks():
            with self.subTest(block=block["id"]):
                self.assertEqual(
                    block["demo_output"],
                    template_lang.render(block["snippet"], composer.DEMO_CONTEXT))

    def test_named_functions_all_exist_in_the_registry(self):
        for block in composer.BLOCKS:
            if block["name"].startswith("{%"):
                continue  # the control block is a tag, not a function
            with self.subTest(block=block["id"]):
                self.assertIn(block["name"], template_lang.FUNCTIONS)


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.org = {"id": "O1", "name": "Acme"}
        self.no_org = lambda mail: None
        self.no_tags = lambda mail_id: {}

    def test_filter_ids_are_unique(self):
        self.assertEqual(len(set(composer.FILTER_IDS)), len(composer.FILTER_IDS))
        self.assertIn("all", composer.FILTER_IDS)

    def test_all_matches_everything(self):
        mail = {"id": "M1"}
        self.assertTrue(composer.matches(mail, "all", self.no_org, self.no_tags))

    def test_unknown_filter_falls_back_to_showing_everything(self):
        self.assertTrue(composer.matches({"id": "M1"}, "bogus", self.no_org, self.no_tags))

    def test_derived_field_filters(self):
        mail = {"id": "M1", "_has_attachments": True, "_has_links": False,
                "_has_password": True}
        self.assertTrue(composer.matches(mail, "attachments", self.no_org, self.no_tags))
        self.assertFalse(composer.matches(mail, "links", self.no_org, self.no_tags))
        self.assertTrue(composer.matches(mail, "password", self.no_org, self.no_tags))

    def test_org_filter_uses_the_supplied_resolver(self):
        mail = {"id": "M1"}
        self.assertFalse(composer.matches(mail, "org", self.no_org, self.no_tags))
        self.assertTrue(composer.matches(mail, "org", lambda m: self.org, self.no_tags))

    def test_tag_filter_uses_the_supplied_tag_lookup(self):
        mail = {"id": "M1"}
        self.assertFalse(composer.matches(mail, "tag", self.no_org, self.no_tags))
        self.assertTrue(composer.matches(
            mail, "tag", self.no_org, lambda mid: {"downloaded": "recent"}))


class PageTests(unittest.TestCase):
    def setUp(self):
        self.mails = [{"id": f"M{i}", "_has_attachments": i % 2 == 0} for i in range(25)]
        self.no_org = lambda mail: None
        self.no_tags = lambda mail_id: {}

    def _page(self, offset, limit, filter_id="all"):
        return composer.page(self.mails, filter_id, offset, limit, self.no_org, self.no_tags)

    def test_pages_do_not_overlap_and_cover_everything(self):
        first = self._page(0, 10)
        second = self._page(10, 10)
        third = self._page(20, 10)
        self.assertEqual([m["id"] for m in first["mails"]], [f"M{i}" for i in range(10)])
        self.assertEqual([m["id"] for m in second["mails"]], [f"M{i}" for i in range(10, 20)])
        self.assertEqual(len(third["mails"]), 5)
        self.assertTrue(first["has_more"])
        self.assertFalse(third["has_more"])

    def test_total_counts_the_filtered_set_not_the_page(self):
        result = self._page(0, 10, "attachments")
        self.assertEqual(result["total"], 13)
        self.assertEqual(len(result["mails"]), 10)
        self.assertTrue(result["has_more"])

    def test_offset_past_the_end_is_an_empty_last_page(self):
        result = self._page(99, 10)
        self.assertEqual(result["mails"], [])
        self.assertFalse(result["has_more"])


class RowForMailTests(unittest.TestCase):
    def test_row_is_synthesized_from_the_mail_itself(self):
        mail = make_mail(subject="Invoice", sender="Alice Smith",
                         received="2026-06-10 09:30:00",
                         attachments=[{"filename": "report.pdf"}])
        row = composer.row_for_mail(mail)
        self.assertEqual(row["subject"], "Invoice")
        self.assertEqual(row["datetime"], "2026-06-10 09:30:00")
        self.assertEqual(row["sender"], "Alice Smith")
        self.assertEqual(row["file_name"], "report.pdf")
        self.assertEqual(row["uses_ftp"], "")

    def test_a_mail_with_no_attachment_has_a_blank_file_name(self):
        self.assertEqual(composer.row_for_mail(make_mail(attachments=[]))["file_name"], "")

    def test_a_missing_row_key_resolves_to_empty_in_the_dsl(self):
        # A cache mail has no sheet, so row.ref is absent -- the DSL's missing-key
        # rule ("") must apply rather than raising.
        row = composer.row_for_mail(make_mail())
        self.assertEqual(template_lang.render("[{{ row.ref }}]", {"row": row}), "[]")


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.sample = composer.sample("sample-ftp")

    def test_preview_delegates_to_the_same_planner_press_uses(self):
        template = {"body": "Link: {{ ftp_link(row.file_name) }}",
                    "attachment_expr": "", "error": ""}
        plan = composer.preview(template, self.sample["mail"], self.sample["row"], [])
        direct = bulk_compose.plan_for_mail(0, self.sample["row"], self.sample["mail"],
                                            template, [])
        self.assertEqual(plan, direct)
        self.assertEqual(plan["status"], "ready")

    def test_a_template_carrying_a_stored_error_blocks_without_rendering(self):
        template = {"body": "Dear {{ sender.first_name }}", "attachment_expr": "",
                    "error": "body: unexpected end of expression"}
        plan = composer.preview(template, self.sample["mail"], self.sample["row"], [])
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["body"], "")
        self.assertIn("template is invalid", plan["warnings"][0])

    def test_a_missing_file_blocks_the_plan(self):
        sample = composer.sample("sample-missing-file")
        template = {"body": "Attached.", "attachment_expr": "", "error": ""}
        plan = composer.preview(template, sample["mail"], sample["row"], [])
        self.assertEqual(plan["status"], "blocked")
        self.assertTrue(any("file not found" in w for w in plan["warnings"]))


if __name__ == "__main__":
    unittest.main()
