"""Tests for mailfilter.util.safe_filename."""

import unittest

from mailfilter.util import safe_filename


class SafeFilenameTests(unittest.TestCase):
    def test_keeps_spaces_dots_hyphens(self):
        self.assertEqual(safe_filename("my file-2026.06.pdf", "x"), "my file-2026.06.pdf")

    def test_replaces_path_separators(self):
        self.assertEqual(safe_filename("a/b\\c.txt", "x"), "a_b_c.txt")

    def test_trims_leading_trailing_separators(self):
        self.assertEqual(safe_filename("  .name.  ", "x"), "name")

    def test_fallback_when_nothing_usable(self):
        self.assertEqual(safe_filename("", "fb"), "fb")
        self.assertEqual(safe_filename("///", "fb"), "fb")

    def test_none_uses_fallback(self):
        self.assertEqual(safe_filename(None, "fb"), "fb")


if __name__ == "__main__":
    unittest.main()
