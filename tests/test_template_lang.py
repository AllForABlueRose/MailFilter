"""Tests for mailfilter.template_lang: the sandboxed reply-template DSL.

Covers expression evaluation, the {% if/elif/else %} block grammar, the function
registry, and -- importantly -- that the sandbox cannot reach Python internals.
"""

import unittest

import config
from mailfilter import template_lang
from mailfilter.template_lang import TemplateError, eval_expr, render


def ev(expr, ctx=None):
    return eval_expr(expr, ctx or {})


class ExpressionTests(unittest.TestCase):
    def test_literals(self):
        self.assertEqual(ev('"hello"'), "hello")
        self.assertEqual(ev("42"), 42)
        self.assertEqual(ev("3.5"), 3.5)
        self.assertIs(ev("true"), True)
        self.assertIs(ev("false"), False)

    def test_variable_lookup_dotted(self):
        ctx = {"row": {"file_name": "report.pdf"}, "sender": {"is_internal": True}}
        self.assertEqual(ev("row.file_name", ctx), "report.pdf")
        self.assertIs(ev("sender.is_internal", ctx), True)

    def test_missing_variable_is_empty_string(self):
        self.assertEqual(ev("row.nope", {"row": {}}), "")
        self.assertEqual(ev("nothing.here", {}), "")

    def test_string_concat_with_plus(self):
        ctx = {"row": {"ref": "AB", "n": "12"}}
        self.assertEqual(ev('upper(row.ref) + "_" + row.n', ctx), "AB_12")

    def test_numeric_arithmetic(self):
        self.assertEqual(ev("2 + 3 * 4"), 14)
        self.assertEqual(ev("(2 + 3) * 4"), 20)
        self.assertEqual(ev("10 / 4"), 2.5)
        self.assertEqual(ev("-5 + 8"), 3)

    def test_comparison_and_boolean(self):
        ctx = {"row": {"ftp": "Yes"}}
        self.assertIs(ev('row.ftp == "Yes"', ctx), True)
        self.assertIs(ev('row.ftp != "No"', ctx), True)
        self.assertIs(ev("1 < 2 and 2 < 3"), True)
        self.assertIs(ev("1 > 2 or 3 > 2"), True)
        self.assertIs(ev("not false"), True)

    def test_division_by_zero_raises(self):
        with self.assertRaises(TemplateError):
            ev("1 / 0")


class FunctionTests(unittest.TestCase):
    def test_if_and_default(self):
        self.assertEqual(ev('if(true, "a", "b")'), "a")
        self.assertEqual(ev('if(false, "a", "b")'), "b")
        self.assertEqual(ev('default("", "fallback")'), "fallback")
        self.assertEqual(ev('default("x", "fallback")'), "x")

    def test_string_functions(self):
        self.assertEqual(ev('upper("abc")'), "ABC")
        self.assertEqual(ev('lower("ABC")'), "abc")
        self.assertEqual(ev('title("hello world")'), "Hello World")
        self.assertEqual(ev('strip("  x  ")'), "x")
        self.assertEqual(ev('replace("a-b-c", "-", "/")'), "a/b/c")
        self.assertIs(ev('contains("Hello", "ell")'), True)
        self.assertEqual(ev('concat("a", "b", "c")'), "abc")

    def test_ftp_link_uses_config_base(self):
        self.assertEqual(ev('ftp_link("x.pdf")'), config.FTP_LINK_BASE + "x.pdf")

    def test_date_reformat(self):
        self.assertEqual(ev('date("2026-06-20 14:30:00", "%Y%m%d")'), "20260620")

    def test_unknown_function_raises_at_parse(self):
        with self.assertRaises(TemplateError):
            ev('frobnicate("x")')


class SandboxTests(unittest.TestCase):
    def test_dunder_access_yields_empty_not_type(self):
        # The classic escape: reaching a real object's __class__. Here every hop
        # is a dict lookup, so this resolves to "" rather than a Python type.
        self.assertEqual(ev('row.__class__', {"row": {}}), "")

    def test_attribute_access_on_string_value_is_blocked(self):
        # row.name is a string; a further hop must not reach str methods/attrs.
        self.assertEqual(ev("row.name.upper", {"row": {"name": "alice"}}), "")

    def test_no_indexing_syntax(self):
        with self.assertRaises(TemplateError):
            ev('row["file_name"]', {"row": {"file_name": "x"}})


class RenderTests(unittest.TestCase):
    def test_plain_text_passthrough(self):
        self.assertEqual(render("Hello world", {}), "Hello world")

    def test_inline_expression(self):
        ctx = {"sender": {"first_name": "Alice"}}
        self.assertEqual(render("Dear {{ sender.first_name }},", ctx),
                         "Dear Alice,")

    def test_if_else_block(self):
        tmpl = "{% if row.uses_ftp %}LINK{% else %}ATTACHED{% endif %}"
        self.assertEqual(render(tmpl, {"row": {"uses_ftp": "Yes"}}), "LINK")
        self.assertEqual(render(tmpl, {"row": {"uses_ftp": "No"}}), "ATTACHED")

    def test_elif_chain(self):
        tmpl = ("{% if sender.role == \"member\" %}internal"
                "{% elif sender.role == \"representative\" %}rep"
                "{% else %}external{% endif %}")
        self.assertEqual(render(tmpl, {"sender": {"role": "member"}}), "internal")
        self.assertEqual(render(tmpl, {"sender": {"role": "representative"}}), "rep")
        self.assertEqual(render(tmpl, {"sender": {"role": ""}}), "external")

    def test_nested_if(self):
        tmpl = ("{% if row.a %}{% if row.b %}AB{% else %}A{% endif %}"
                "{% else %}none{% endif %}")
        self.assertEqual(render(tmpl, {"row": {"a": "1", "b": "1"}}), "AB")
        self.assertEqual(render(tmpl, {"row": {"a": "1", "b": ""}}), "A")
        self.assertEqual(render(tmpl, {"row": {"a": "", "b": "1"}}), "none")

    def test_realistic_template(self):
        tmpl = (
            "Dear {{ default(sender.first_name, \"Sir/Madam\") }},\n"
            "{% if row.uses_ftp %}"
            "Retrieve it here: {{ ftp_link(row.file_name) }}"
            "{% else %}"
            "Please find {{ row.file_name }} attached."
            "{% endif %}\n"
            "{{ if(sender.is_internal, \"Best regards,\", \"Yours faithfully,\") }}"
        )
        ftp = render(tmpl, {"sender": {"first_name": "Bob", "is_internal": True},
                            "row": {"uses_ftp": "Y", "file_name": "f.pdf"}})
        self.assertIn("Retrieve it here: " + config.FTP_LINK_BASE + "f.pdf", ftp)
        self.assertIn("Best regards,", ftp)

        att = render(tmpl, {"sender": {"first_name": "", "is_internal": False},
                            "row": {"uses_ftp": "", "file_name": "f.pdf"}})
        self.assertIn("Dear Sir/Madam,", att)
        self.assertIn("Please find f.pdf attached.", att)
        self.assertIn("Yours faithfully,", att)


class TemplateErrorTests(unittest.TestCase):
    def test_unclosed_if(self):
        with self.assertRaises(TemplateError):
            render("{% if row.a %}x", {"row": {"a": "1"}})

    def test_dangling_endif(self):
        with self.assertRaises(TemplateError):
            render("x{% endif %}", {})

    def test_if_without_condition(self):
        with self.assertRaises(TemplateError):
            render("{% if %}x{% endif %}", {})

    def test_unknown_tag(self):
        with self.assertRaises(TemplateError):
            render("{% loop %}x{% endloop %}", {})

    def test_validate_accepts_good_template(self):
        template_lang.validate("{% if row.a %}{{ row.b }}{% endif %}")  # no raise

    def test_validate_rejects_bad_template(self):
        with self.assertRaises(TemplateError):
            template_lang.validate("{% if row.a %}")


if __name__ == "__main__":
    unittest.main()
