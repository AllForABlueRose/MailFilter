"""Tests for mailfilter.expr: the boolean keyword-expression language."""

import unittest

from mailfilter import expr
from mailfilter.expr import ExprError


def matches(expression, text):
    """Parse ``expression`` and evaluate it against lowercased ``text``."""
    node = expr.parse(expression)
    return node is not None and expr.evaluate(node, text.lower())


class ParseBlankTests(unittest.TestCase):
    def test_blank_is_none(self):
        self.assertIsNone(expr.parse(""))
        self.assertIsNone(expr.parse("   "))
        self.assertIsNone(expr.parse(None))


class LiteralTests(unittest.TestCase):
    def test_single_literal_substring_case_insensitive(self):
        self.assertTrue(matches("Server", "the SERVER log"))
        self.assertFalse(matches("server", "no match here"))

    def test_literal_keeps_internal_spaces(self):
        self.assertTrue(matches("out of office", "i am out of office today"))
        self.assertFalse(matches("out of office", "out office"))


class BooleanTests(unittest.TestCase):
    def test_comma_is_or(self):
        self.assertTrue(matches("server, error", "only server here"))
        self.assertTrue(matches("server, error", "only error here"))
        self.assertFalse(matches("server, error", "neither term"))

    def test_semicolon_is_and(self):
        self.assertTrue(matches("server; error", "server had an error"))
        self.assertFalse(matches("server; error", "server only"))

    def test_left_to_right_no_precedence(self):
        # a, b; c  ==  (a OR b) AND c
        self.assertTrue(matches("a, b; c", "b c"))      # (a|b) -> b true, AND c true
        self.assertFalse(matches("a, b; c", "a b"))     # c missing
        # a; b, c  ==  (a AND b) OR c
        self.assertTrue(matches("a; b, c", "c"))        # right OR rescues it
        self.assertFalse(matches("a; b, c", "a"))       # a AND b false, c missing

    def test_grouping_overrides_left_to_right(self):
        # a; [b, c]  ==  a AND (b OR c)
        self.assertTrue(matches("a; [b, c]", "a c"))
        self.assertFalse(matches("a; [b, c]", "b c"))   # a missing
        self.assertFalse(matches("a; [b, c]", "a d"))   # neither b nor c

    def test_nested_groups(self):
        self.assertTrue(matches("[a; [b, c]]", "a b"))
        self.assertFalse(matches("[a; [b, c]]", "b c"))


class RegexTests(unittest.TestCase):
    def test_regex_operand(self):
        self.assertTrue(matches("<{(gr(a|e)y)}>", "the grey cat"))
        self.assertTrue(matches("<{(gr(a|e)y)}>", "the GRAY cat"))
        self.assertFalse(matches("<{(gr(a|e)y)}>", "the green cat"))

    def test_comma_inside_regex_is_not_an_operator(self):
        node = expr.parse("<{(a{1,3})}>")
        self.assertEqual(len(expr.operands(node)), 1)  # one term, not split on the comma
        self.assertTrue(expr.evaluate(node, "aaa"))
        self.assertFalse(expr.evaluate(node, "bbbb"))

    def test_brackets_inside_regex_are_not_grouping(self):
        self.assertTrue(matches("<{(gr[ae]y)}>", "grey"))
        self.assertTrue(matches("<{(gr[ae]y)}>", "gray"))

    def test_regex_mixed_with_boolean_operators(self):
        self.assertTrue(matches("server; <{(50[24])}>", "server returned 502"))
        self.assertFalse(matches("server; <{(50[24])}>", "server returned 500"))
        self.assertFalse(matches("server; <{(50[24])}>", "502 but no s-word"))


class OperandsTests(unittest.TestCase):
    def test_collects_all_leaves(self):
        node = expr.parse("a; [b, <{(c)}>]")
        leaves = expr.operands(node)
        self.assertEqual(len(leaves), 3)
        kinds = sorted(leaf[0] for leaf in leaves)
        self.assertEqual(kinds, ["lit", "lit", "re"])

    def test_operands_of_none(self):
        self.assertEqual(expr.operands(None), [])


class ErrorTests(unittest.TestCase):
    def test_unmatched_close_bracket(self):
        with self.assertRaises(ExprError):
            expr.parse("a]")

    def test_unterminated_group(self):
        with self.assertRaises(ExprError):
            expr.parse("[a, b")

    def test_empty_group(self):
        with self.assertRaises(ExprError):
            expr.parse("[]")

    def test_leading_operator(self):
        with self.assertRaises(ExprError):
            expr.parse(", a")

    def test_trailing_operator(self):
        with self.assertRaises(ExprError):
            expr.parse("a;")

    def test_missing_operator_between_terms(self):
        with self.assertRaises(ExprError):
            expr.parse("[a, b] [c, d]")

    def test_unterminated_regex(self):
        with self.assertRaises(ExprError):
            expr.parse("<{(unclosed")

    def test_invalid_regex(self):
        with self.assertRaises(ExprError):
            expr.parse("<{((unbalanced)}>")


if __name__ == "__main__":
    unittest.main()
