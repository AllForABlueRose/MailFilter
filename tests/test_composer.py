"""Unit tests for the pure Composer read-side (mailfilter/composer.py).

Covers the two catalogues (the 10 example mails, the 10 function blocks) and the
preview delegating to the same planner Press uses. The cache-mail picker moved to
mail_picker.py (tests/test_mail_picker.py). No Flask, no Outlook.
"""

import unittest

import config
from mailfilter import (bulk_compose, compose_template_store, composer, customers,
                        template_lang)


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
        # is_internal is decided by Customer Management, so the samples demonstrate both
        # outcomes once one of their domains belongs to a Root organization.
        domains = [s["mail"]["sender_email"].rsplit("@", 1)[-1] for s in composer.SAMPLES]
        root = [{"id": "r", "name": "Mine", "category": "Root",
                 "domains": [{"domain": domains[2], "role": "member"}], "contacts": []}]
        internal = customers.internal_domains(root)
        self.assertTrue(any(d in internal for d in domains))
        self.assertTrue(any(d not in internal for d in domains))

    def test_the_internal_sample_reads_as_internal_once_its_domain_is_yours(self):
        sample = composer.sample("sample-internal")
        domain = sample["mail"]["sender_email"].rsplit("@", 1)[-1]
        template = {"body": '{{ if(sender.is_internal, "internal", "external") }}',
                    "attachment_expr": "", "error": ""}

        # Nothing declared yet: honestly external.
        plan = composer.preview(template, sample["mail"], sample["row"], [])
        self.assertEqual(plan["body"], "external")

        # Declare the domain as your own company's, and it flips.
        root = [{"id": "r", "name": "Mine", "category": "Root",
                 "domains": [{"domain": domain, "role": "member"}], "contacts": []}]
        plan = composer.preview(template, sample["mail"], sample["row"], root,
                                internal=customers.internal_domains(root))
        self.assertEqual(plan["body"], "internal")

    BODY = ('Dear {{ default(sender.first_name, "Sir/Madam") }},\n'
            "{% if row.uses_ftp %}Link: {{ ftp_link(row.file_name) }}"
            "{% else %}Attached: {{ row.file_name }}{% endif %}")

    def test_every_sample_with_a_file_name_renders(self):
        template = {"body": self.BODY, "attachment_expr": "", "error": ""}
        for s in composer.SAMPLES:
            if not s["row"]["file_name"]:
                continue   # the deliberately-incomplete sample; asserted below
            with self.subTest(sample=s["id"]):
                plan = composer.preview(template, s["mail"], s["row"], [])
                self.assertTrue(plan["body"])
                # Reply-all: everyone on the original's To/Cc is carried over. Composer
                # previews with no drafting mailbox, so nothing extra is CC'd.
                for email in s["mail"]["cc_emails"]:
                    self.assertIn(email, plan["cc"])

    def test_the_no_file_sample_is_blocked_for_the_stated_reason(self):
        # The template PRINTS row.file_name and this sample has none, so it is refused
        # before rendering rather than shipping a draft with a hole in it.
        template = {"body": self.BODY, "attachment_expr": "", "error": ""}
        sample = composer.sample("sample-no-file")
        plan = composer.preview(template, sample["mail"], sample["row"], [])
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["warnings"], ["missing row.file_name"])
        self.assertEqual(plan["body"], "")

    def test_a_branch_only_variable_does_not_block(self):
        # row.uses_ftp is blank on the attachment samples -- blank means "no", not
        # "missing", so it must not block them.
        template = {"body": self.BODY, "attachment_expr": "", "error": ""}
        sample = composer.sample("sample-attached")
        self.assertEqual(sample["row"]["uses_ftp"], "")
        plan = composer.preview(template, sample["mail"], sample["row"], [])
        self.assertEqual(plan["status"], "ready")


class BlockTests(unittest.TestCase):
    def test_ten_blocks_with_unique_ids(self):
        self.assertEqual(len(composer.BLOCKS), 10)
        self.assertEqual(len({b["id"] for b in composer.BLOCKS}), 10)

    def test_every_demo_case_renders_through_the_real_dsl(self):
        # The palette's advertised output is produced by template_lang, not
        # hardcoded -- so it cannot drift from what the DSL actually does.
        for block in composer.render_blocks():
            for i, demo in enumerate(block["demos"]):
                with self.subTest(block=block["id"], case=i):
                    self.assertNotIn("(error:", demo["output"])
                    self.assertTrue(demo["output"].strip())

    def test_every_block_has_more_than_one_case_to_cycle_through(self):
        # The whole point of the palette is watching the inputs drive the output.
        for block in composer.render_blocks():
            with self.subTest(block=block["id"]):
                self.assertGreaterEqual(len(block["demos"]), 2)

    def test_a_case_output_matches_a_direct_render_of_its_own_context(self):
        for block in composer.render_blocks():
            for i, overrides in enumerate(block["cases"]):
                with self.subTest(block=block["id"], case=i):
                    context = composer._merge(composer.DEMO_CONTEXT, overrides)
                    self.assertEqual(
                        block["demos"][i]["output"],
                        template_lang.render(block["snippet"], context))

    def test_the_inputs_shown_are_the_ones_the_snippet_actually_reads(self):
        # Not a hand-written list: asked of the parser, so the inputs beside the result
        # are exactly what fed it.
        for block in composer.render_blocks():
            expected = []
            for ns in composer.NAMESPACES:
                expected += [f"{ns}.{n}"
                             for n in template_lang.variables(block["snippet"], ns)]
            for i, demo in enumerate(block["demos"]):
                with self.subTest(block=block["id"], case=i):
                    self.assertEqual([x["name"] for x in demo["inputs"]], expected)

    def test_cases_actually_differ_so_the_cycle_shows_something(self):
        for block in composer.render_blocks():
            with self.subTest(block=block["id"]):
                shown = [tuple(x["value"] for x in d["inputs"]) for d in block["demos"]]
                self.assertEqual(len(set(shown)), len(shown))

    def test_demo_output_is_the_first_case(self):
        for block in composer.render_blocks():
            with self.subTest(block=block["id"]):
                self.assertEqual(block["demo_output"], block["demos"][0]["output"])

    def test_named_functions_all_exist_in_the_registry(self):
        for block in composer.BLOCKS:
            if block["name"].startswith("{%"):
                continue  # the control block is a tag, not a function
            with self.subTest(block=block["id"]):
                self.assertIn(block["name"], template_lang.FUNCTIONS)


class StarterTemplateTests(unittest.TestCase):
    """The template seeded on first run: a REAL one, not placeholder text."""

    def test_it_is_a_valid_template(self):
        self.assertEqual(
            compose_template_store.validate(composer.STARTER_TEMPLATE["body"],
                                            composer.STARTER_TEMPLATE["attachment_expr"]),
            "")

    def test_it_renders_against_every_sample_that_has_a_file_name(self):
        # It renders for all of them. Whether the item is *ready* additionally depends
        # on the file being on the file server — "sample-missing-file" exists precisely
        # to show that failure, so it renders but stays blocked.
        template = {**composer.STARTER_TEMPLATE, "error": ""}
        for s in composer.SAMPLES:
            if not s["row"]["file_name"]:
                continue
            with self.subTest(sample=s["id"]):
                plan = composer.preview(template, s["mail"], s["row"], [])
                self.assertIn("Thank you for your message.", plan["body"])
                expected = "blocked" if s["id"] == "sample-missing-file" else "ready"
                self.assertEqual(plan["status"], expected)

    def test_it_demonstrates_both_branches(self):
        template = {**composer.STARTER_TEMPLATE, "error": ""}
        ftp = composer.sample("sample-ftp")
        attached = composer.sample("sample-attached")
        self.assertIn("download",
                      composer.preview(template, ftp["mail"], ftp["row"], [])["body"])
        self.assertIn("attached",
                      composer.preview(template, attached["mail"], attached["row"],
                                       [])["body"])

    def test_it_greets_a_sender_with_no_name(self):
        template = {**composer.STARTER_TEMPLATE, "error": ""}
        s = composer.sample("sample-no-name")
        plan = composer.preview(template, s["mail"], s["row"], [])
        self.assertIn("Dear Sir/Madam,", plan["body"])


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
