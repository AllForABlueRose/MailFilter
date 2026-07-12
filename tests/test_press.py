"""Unit tests for the pure Press read-side (mailfilter/press.py).

The worklist's three states (empty / failed / ok), the row a cache mail starts with,
the Excel form's columns, and binding a filled-in form back to mail items. No Flask,
no Outlook.
"""

import unittest

from mailfilter import bulk_compose, press, template_lang
from tests.factories import make_mail

# A template that PRINTS row.ref (so it is required) and branches on row.uses_ftp
# (so it is a column but blank is a valid answer).
INVOICE = {
    "id": "T1",
    "name": "Invoice",
    "body": ("Dear {{ sender.first_name }},\n"
             "{% if row.uses_ftp %}Link: {{ ftp_link(row.file_name) }}"
             "{% else %}Attached: {{ row.file_name }}{% endif %}\n"
             "Ref {{ upper(row.ref) }}."),
    "attachment_expr": "",
    "error": "",
}
DRAWINGS = {
    "id": "T2", "name": "Drawings",
    "body": "Due {{ row.due }} — {{ row.file_name }}",
    "attachment_expr": "", "error": "",
}


class VariableTests(unittest.TestCase):
    def test_columns_include_a_branch_only_variable(self):
        self.assertEqual(press.template_variables(INVOICE),
                         ["uses_ftp", "file_name", "ref"])

    def test_only_printed_variables_are_required(self):
        # A blank row.uses_ftp means "no", not "missing" -- it must not block an item.
        self.assertEqual(bulk_compose.required_variables(INVOICE), ["file_name", "ref"])

    def test_union_across_templates_is_first_seen_order(self):
        self.assertEqual(press.union_variables([INVOICE, DRAWINGS]),
                         ["uses_ftp", "file_name", "ref", "due"])

    def test_union_of_no_templates_is_empty(self):
        self.assertEqual(press.union_variables([]), [])


class RowDefaultTests(unittest.TestCase):
    def test_the_mail_answers_what_it_can_and_the_rest_starts_blank(self):
        mail = make_mail(subject="Invoice", sender="Alice Smith",
                         received="2026-06-10 09:30:00",
                         attachments=[{"filename": "report.pdf"}])
        row = press.row_defaults(mail, press.template_variables(INVOICE))
        self.assertEqual(row["file_name"], "report.pdf")   # from the mail's attachment
        self.assertEqual(row["subject"], "Invoice")
        self.assertEqual(row["ref"], "")                   # nothing on the mail says this
        self.assertEqual(row["uses_ftp"], "")

    def test_a_mail_with_no_attachment_has_a_blank_file_name(self):
        row = press.row_defaults(make_mail(attachments=[]), [])
        self.assertEqual(row["file_name"], "")


class ComputeTests(unittest.TestCase):
    def setUp(self):
        self.mail = make_mail(id="M1", subject="Invoice", sender="Alice Smith",
                              sender_email="alice@acme.com",
                              attachments=[{"filename": "report.pdf"}])
        self.mails = {"M1": self.mail}
        self.templates = {"T1": INVOICE, "T2": DRAWINGS}

    def _compute(self, template_id, row):
        return press.compute([{"mail_id": "M1", "template_id": template_id, "row": row}],
                             self.mails, self.templates, [], "shared@example.com")[0]

    def test_no_template_is_empty(self):
        r = self._compute(None, {})
        self.assertEqual(r["status"], press.STATUS_EMPTY)
        self.assertIsNone(r["plan"])
        self.assertEqual(r["reasons"], [])

    def test_a_blank_required_cell_fails_with_the_reason(self):
        r = self._compute("T1", {"file_name": "report.pdf", "uses_ftp": ""})
        self.assertEqual(r["status"], press.STATUS_FAILED)
        self.assertEqual(r["reasons"], ["missing row.ref"])

    def test_filling_the_cell_makes_it_ok(self):
        r = self._compute("T1", {"file_name": "Invoice_ACME_2026Q2.pdf",
                                 "uses_ftp": "yes", "ref": "acme-1042"})
        self.assertEqual(r["status"], press.STATUS_OK)
        self.assertIn("Ref ACME-1042.", r["plan"]["body"])
        self.assertEqual(r["reasons"], [])

    def test_an_ok_item_carries_the_plan_draft_ops_would_create(self):
        r = self._compute("T1", {"file_name": "Invoice_ACME_2026Q2.pdf",
                                 "uses_ftp": "yes", "ref": "a"})
        self.assertEqual(r["plan"]["mail_id"], "M1")
        self.assertEqual(r["plan"]["to"], ["alice@acme.com"])
        self.assertIn("shared@example.com", r["plan"]["cc"])

    def test_the_cc_address_is_omitted_when_the_toggle_is_off(self):
        results = press.compute(
            [{"mail_id": "M1", "template_id": "T1",
              "row": {"file_name": "x", "uses_ftp": "yes", "ref": "a"}}],
            self.mails, self.templates, [], "")   # cc_address = "" (toggle off)
        self.assertNotIn("shared@example.com", results[0]["plan"]["cc"])

    def test_a_broken_template_fails_rather_than_raising(self):
        broken = {"id": "T3", "body": "{{ upper( }}", "attachment_expr": "",
                  "error": "body: unexpected end of expression"}
        results = press.compute([{"mail_id": "M1", "template_id": "T3", "row": {}}],
                                self.mails, {"T3": broken}, [], "")
        self.assertEqual(results[0]["status"], press.STATUS_FAILED)
        self.assertIn("template is invalid", results[0]["reasons"][0])

    def test_each_item_reports_the_variables_its_own_template_reads(self):
        r = self._compute("T2", {"file_name": "x", "due": "2026-07-31"})
        self.assertEqual(r["variables"], ["due", "file_name"])

    def test_an_item_naming_a_mail_that_left_the_cache_is_dropped(self):
        results = press.compute([{"mail_id": "GONE", "template_id": "T1", "row": {}}],
                                self.mails, self.templates, [], "")
        self.assertEqual(results, [])

    def test_an_item_naming_an_unknown_template_is_empty_not_an_error(self):
        r = self._compute("NOSUCH", {})
        self.assertEqual(r["status"], press.STATUS_EMPTY)


class FormTests(unittest.TestCase):
    def setUp(self):
        self.mail = make_mail(id="M1", subject="Invoice", sender_email="a@acme.com",
                              received="2026-06-10 09:30:00",
                              recipient_emails=["me@example.com"],
                              attachments=[{"filename": "report.pdf"}])

    def test_columns_are_entry_id_then_report_then_the_templates_variables(self):
        columns = press.form_columns(INVOICE)
        self.assertEqual(columns[0], press.ENTRY_ID_COLUMN)
        self.assertEqual(columns[1:6], press.REPORT_COLUMNS)
        self.assertEqual(columns[6:], ["uses_ftp", "file_name", "ref"])

    def test_entry_id_is_omitted_when_no_mail_is_loaded(self):
        # The user could not supply an Outlook EntryID by hand, so it is left out and
        # the upload falls back to a best-effort match.
        columns = press.form_columns(INVOICE, with_entry_id=False)
        self.assertNotIn(press.ENTRY_ID_COLUMN, columns)

    def test_no_template_still_yields_the_report_columns(self):
        self.assertEqual(press.form_columns(None, with_entry_id=False),
                         press.REPORT_COLUMNS)

    def test_rows_are_prefilled_from_the_mail(self):
        columns = press.form_columns(INVOICE)
        rows = press.form_rows([self.mail], INVOICE, columns)
        row = dict(zip(columns, rows[0]))
        self.assertEqual(row[press.ENTRY_ID_COLUMN], "M1")
        self.assertEqual(row["datetime"], "2026-06-10 09:30:00")
        self.assertEqual(row["subject"], "Invoice")
        self.assertEqual(row["file_name"], "report.pdf")
        self.assertEqual(row["ref"], "")           # nothing to prefill it with

    def test_rows_round_trip_what_the_user_already_typed(self):
        columns = press.form_columns(INVOICE)
        rows = press.form_rows([self.mail], INVOICE, columns,
                               rows_by_id={"M1": {"ref": "acme-1042"}})
        row = dict(zip(columns, rows[0]))
        self.assertEqual(row["ref"], "acme-1042")


class BindUploadTests(unittest.TestCase):
    def setUp(self):
        self.m1 = make_mail(id="M1", subject="Invoice", sender="Alice Smith",
                            sender_email="alice@acme.com", received="2026-06-10 09:30:00")
        self.m2 = make_mail(id="M2", subject="Drawings", sender="Bob Lee",
                            sender_email="bob@orion.com", received="2026-06-11 10:00:00")
        self.mails = [self.m1, self.m2]

    def test_entry_id_binds_exactly(self):
        rows = [{press.ENTRY_ID_COLUMN: "M2", "ref": "x"}]
        bound, unbound = press.bind_upload(rows, self.mails)
        self.assertEqual(list(bound), ["M2"])
        self.assertEqual(unbound, [])

    def test_an_entry_id_that_is_not_loaded_is_reported(self):
        bound, unbound = press.bind_upload([{press.ENTRY_ID_COLUMN: "GONE"}], self.mails)
        self.assertEqual(bound, {})
        self.assertIn("not loaded", unbound[0]["reason"])

    def test_without_an_entry_id_it_matches_best_effort(self):
        # The form was downloaded before any mail was loaded, so the user filled in a
        # sheet with no EntryID column.
        rows = [{"subject": "Invoice", "datetime": "2026-06-10 09:30:00",
                 "sender": "alice@acme.com", "ref": "acme-1"}]
        bound, unbound = press.bind_upload(rows, self.mails)
        self.assertEqual(list(bound), ["M1"])
        self.assertEqual(bound["M1"]["ref"], "acme-1")
        self.assertEqual(unbound, [])

    def test_an_unmatchable_row_is_reported_not_guessed(self):
        rows = [{"subject": "Nothing like this", "datetime": "", "sender": ""}]
        bound, unbound = press.bind_upload(rows, self.mails)
        self.assertEqual(bound, {})
        self.assertIn("no loaded mail matches", unbound[0]["reason"])

    def test_an_ambiguous_row_is_reported_not_guessed(self):
        twin = make_mail(id="M3", subject="Invoice", sender="Alice Smith",
                         sender_email="alice@acme.com", received="2026-06-10 09:30:00")
        rows = [{"subject": "Invoice", "datetime": "2026-06-10 09:30:00", "sender": ""}]
        bound, unbound = press.bind_upload(rows, [self.m1, twin])
        self.assertEqual(bound, {})
        self.assertIn("2 loaded mails match", unbound[0]["reason"])

    def test_the_row_index_is_reported_so_the_user_can_find_it(self):
        rows = [{"subject": "Invoice", "datetime": "2026-06-10 09:30:00",
                 "sender": "alice@acme.com"},
                {"subject": "Nope", "datetime": "", "sender": ""}]
        _bound, unbound = press.bind_upload(rows, self.mails)
        self.assertEqual(unbound[0]["row_index"], 1)


class TemplateLangVariableTests(unittest.TestCase):
    """The introspection the whole feature hangs on."""

    def test_names_are_read_from_the_parsed_tree_not_the_source(self):
        # A "row.fake" inside a string literal is not a variable read.
        self.assertEqual(template_lang.variables('{{ "row.fake" }}', "row"), [])

    def test_a_bare_namespace_is_not_a_variable(self):
        self.assertEqual(template_lang.variables("{{ row }}", "row"), [])

    def test_an_unparseable_template_yields_no_names_rather_than_raising(self):
        self.assertEqual(template_lang.variables("{{ upper( }}", "row"), [])

    def test_conditions_can_be_excluded(self):
        body = "{% if row.flag %}{{ row.value }}{% endif %}"
        self.assertEqual(template_lang.variables(body, "row"), ["flag", "value"])
        self.assertEqual(template_lang.variables(body, "row", conditions=False), ["value"])

    def test_other_namespaces_are_not_reported(self):
        body = "{{ sender.first_name }} {{ row.ref }}"
        self.assertEqual(template_lang.variables(body, "row"), ["ref"])
        self.assertEqual(template_lang.variables(body, "sender"), ["first_name"])


if __name__ == "__main__":
    unittest.main()
