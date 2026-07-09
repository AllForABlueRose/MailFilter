"""Tests for mailfilter.safelinks: detecting Outlook Safe Links that merely wrap a
plain URL also present in the same mail."""

import unittest

from mailfilter import safelinks

SAFE = ("https://nam06.safelinks.protection.outlook.com/"
        "?url=https%3A%2F%2Fexample.com%2Fpath&data=abc123")


class SafeLinkTests(unittest.TestCase):
    def test_is_safe_link(self):
        self.assertTrue(safelinks.is_safe_link(SAFE))
        self.assertFalse(safelinks.is_safe_link("https://example.com/path"))

    def test_target_is_decoded(self):
        self.assertEqual(safelinks.safe_link_target(SAFE), "https://example.com/path")
        self.assertIsNone(safelinks.safe_link_target("https://example.com/path"))

    def test_hidden_when_plain_twin_present(self):
        self.assertEqual(
            safelinks.hidden_safe_links([SAFE, "https://example.com/path"]), {SAFE})

    def test_kept_when_no_twin(self):
        self.assertEqual(safelinks.hidden_safe_links([SAFE]), set())

    def test_trailing_slash_and_case_normalized(self):
        self.assertEqual(
            safelinks.hidden_safe_links([SAFE, "https://EXAMPLE.com/path/"]), {SAFE})

    def test_plain_urls_are_never_hidden(self):
        self.assertEqual(
            safelinks.hidden_safe_links(["https://a.com/x", "https://b.com/y"]), set())

    def test_different_target_not_hidden(self):
        self.assertEqual(
            safelinks.hidden_safe_links([SAFE, "https://example.com/other"]), set())


if __name__ == "__main__":
    unittest.main()
