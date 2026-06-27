"""Tests for mailfilter.password_detect: component compilation (template +
optional value regex), the rule gauntlet, and the text scan."""

import re
import unittest

import config
from mailfilter import password_detect as pd


def _component(template, value_regex="", enabled=True):
    return {"template": template, "value_regex": value_regex, "enabled": enabled}


def _rules(**overrides):
    rules = dict(config.PASSWORD_RULE_DEFAULTS)
    rules.update(overrides)
    return rules


class BuildRegexTests(unittest.TestCase):
    def test_generic_value_when_blank(self):
        source, err = pd.build_regex("password: <{(password_value)}>", "")
        self.assertIsNone(err)
        self.assertIn("(" + config.PASSWORD_GENERIC_VALUE_REGEX + ")", source)
        self.assertTrue(re.search(source, "password: Hunter2x", re.I))

    def test_custom_value_regex(self):
        source, err = pd.build_regex("token: <{(v)}>", r"[A-Z0-9]{4,}")
        self.assertIsNone(err)
        self.assertEqual(re.search(source, "TOKEN: AB12CD", re.I).group(1), "AB12CD")

    def test_whitespace_is_flexible(self):
        # A newline in the template compiles to \s*, so the value matches whether
        # it is on the same line or the next.
        source, _ = pd.build_regex("password:\n<{(v)}>", "")
        self.assertTrue(re.search(source, "password: same", re.I))
        self.assertTrue(re.search(source, "password:\n  next", re.I))

    def test_missing_placeholder_is_error(self):
        source, err = pd.build_regex("just some text", "")
        self.assertIsNone(source)
        self.assertIn("marker", err)

    def test_duplicate_placeholder_is_error(self):
        source, err = pd.build_regex("<{(a)}> and <{(b)}>", "")
        self.assertIsNone(source)
        self.assertIn("one", err)

    def test_bad_value_regex_is_error(self):
        source, err = pd.build_regex("p: <{(v)}>", "a(")
        self.assertIsNone(source)
        self.assertIn("password pattern", err)

    def test_literal_specials_are_escaped(self):
        # "[password]" must match literally, not as a character class.
        source, _ = pd.build_regex("[password] <{(v)}>", "")
        self.assertTrue(re.search(source, "[password] Secret12", re.I))
        self.assertIsNone(re.search(source, "p Secret12", re.I))


class CompilePatternsTests(unittest.TestCase):
    def test_compiles_enabled_valid_components(self):
        compiled, errors = pd.compile_patterns(
            [_component("password: <{(v)}>"), _component("pin <{(v)}>")])
        self.assertEqual([c.number for c in compiled], [1, 2])
        self.assertEqual(errors, [])

    def test_skips_disabled_and_blank_but_keeps_numbering(self):
        # Components are numbered by their position in the FULL list, so the
        # surviving one is still #3, matching what the user sees on screen.
        patterns = [
            _component("off: <{(v)}>", enabled=False),
            _component("   "),
            _component("ok: <{(v)}>"),
        ]
        compiled, errors = pd.compile_patterns(patterns)
        self.assertEqual([c.number for c in compiled], [3])
        self.assertEqual(errors, [])

    def test_missing_placeholder_recorded_by_number(self):
        compiled, errors = pd.compile_patterns(
            [_component("no marker"), _component("ok: <{(v)}>")])
        self.assertEqual([c.number for c in compiled], [2])
        self.assertEqual([n for n, _ in errors], [1])

    def test_bad_value_regex_recorded(self):
        compiled, errors = pd.compile_patterns([_component("p: <{(v)}>", value_regex="a(")])
        self.assertEqual(compiled, [])
        self.assertEqual(errors[0][0], 1)

    def test_components_are_case_insensitive(self):
        compiled, _ = pd.compile_patterns([_component("password: <{(v)}>")])
        self.assertTrue(compiled[0].regex.search("PASSWORD: ABCdef12"))


class CandidateRuleTests(unittest.TestCase):
    def test_length_band_inclusive(self):
        rules = _rules(min_length=8, max_length=12)
        self.assertFalse(pd.candidate_ok("short", rules))
        self.assertTrue(pd.candidate_ok("eightchr", rules))
        self.assertTrue(pd.candidate_ok("twelvecharss", rules))
        self.assertFalse(pd.candidate_ok("thirteencharss", rules))

    def test_no_japanese(self):
        rules = _rules(min_length=1)
        self.assertFalse(pd.candidate_ok("パスワード", rules))
        self.assertFalse(pd.candidate_ok("pass日本", rules))
        self.assertTrue(pd.candidate_ok("plainpass", rules))

    def test_japanese_rule_can_be_disabled(self):
        self.assertTrue(pd.candidate_ok("パスワード", _rules(min_length=1, no_japanese=False)))

    def test_no_link(self):
        rules = _rules(min_length=1)
        for link in ("http://x.com/a", "https://a.b/c", "www.example.com", "ftp://h/f",
                     "example.com/path"):
            self.assertFalse(pd.candidate_ok(link, rules), link)
        self.assertTrue(pd.candidate_ok("Pass.word", rules))

    def test_no_repeating(self):
        rules = _rules(min_length=1)
        # Fixed-period repeats (alphanumeric or symbol).
        self.assertFalse(pd.candidate_ok("aaaaaa", rules))
        self.assertFalse(pd.candidate_ok("abababab", rules))
        self.assertFalse(pd.candidate_ok("======", rules))
        self.assertFalse(pd.candidate_ok("=-=-=-", rules))
        # Symbol styling the fixed-period check misses (longer period / 3 distinct).
        self.assertFalse(pd.candidate_ok("==----==----", rules))
        self.assertFalse(pd.candidate_ok("=-~=-~", rules))
        self.assertFalse(pd.candidate_ok("________________", rules))
        # Real passwords (have a letter/digit) and varied all-symbol strings stay.
        self.assertTrue(pd.candidate_ok("abcabc", rules))
        self.assertTrue(pd.candidate_ok("Hunter2x", rules))
        self.assertTrue(pd.candidate_ok("Xy7$kPlm9!", rules))
        self.assertTrue(pd.candidate_ok("!@#$%^&*", rules))

    def test_no_file(self):
        rules = _rules(min_length=1)
        self.assertFalse(pd.candidate_ok("report.pdf", rules))
        self.assertFalse(pd.candidate_ok("ARCHIVE.ZIP", rules))
        self.assertTrue(pd.candidate_ok("secret.code", rules))

    def test_empty_candidate_never_ok(self):
        self.assertFalse(pd.candidate_ok("", _rules(min_length=1)))


class ScanTextTests(unittest.TestCase):
    def setUp(self):
        self.compiled, _ = pd.compile_patterns(config.PASSWORD_DEFAULT_PATTERNS)
        self.rules = _rules()

    def test_captures_value_after_marker(self):
        self.assertEqual(pd.scan_text("Password: Hunter2xZ here", self.compiled, self.rules),
                         ["Hunter2xZ"])

    def test_password_on_following_line(self):
        found = pd.scan_text("Your password:\n  Sup3rSecret!\nThanks", self.compiled, self.rules)
        self.assertEqual(found, ["Sup3rSecret!"])

    def test_rules_reject_candidates(self):
        text = ("password: report.pdf\n"
                "password: ab\n"
                "password: 日本語パスワード\n"
                "password: GoodPass12")
        self.assertEqual(pd.scan_text(text, self.compiled, self.rules), ["GoodPass12"])

    def test_custom_value_regex_constrains(self):
        # A digits-only value regex only captures the all-digit token. (The whole
        # pattern compiles IGNORECASE, so use a class that case can't widen.)
        compiled, _ = pd.compile_patterns([_component("code: <{(v)}>", value_regex=r"\d+")])
        text = "code: 12345678\ncode: lettersonly"
        self.assertEqual(pd.scan_text(text, compiled, self.rules), ["12345678"])

    def test_dedup_first_seen_order(self):
        compiled, _ = pd.compile_patterns([_component("password: <{(v)}>")])
        text = "password: Alpha9xyz\npassword: Beta99xyz\npassword: Alpha9xyz"
        self.assertEqual(pd.scan_text(text, compiled, self.rules), ["Alpha9xyz", "Beta99xyz"])

    def test_cap_limits_results(self):
        compiled, _ = pd.compile_patterns([_component("password: <{(v)}>")])
        text = "\n".join(f"password: Secret{i}aaa" for i in range(10))
        self.assertEqual(len(pd.scan_text(text, compiled, self.rules, cap=3)), 3)

    def test_no_patterns_or_text(self):
        self.assertEqual(pd.scan_text("", self.compiled, self.rules), [])
        self.assertEqual(pd.scan_text("password: GoodPass12", [], self.rules), [])


if __name__ == "__main__":
    unittest.main()
