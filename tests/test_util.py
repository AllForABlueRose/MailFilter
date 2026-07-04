"""Tests for mailfilter.util: safe_filename and the atomic-write retry."""

import os
import tempfile
import unittest
from pathlib import Path

import config
from mailfilter import util
from mailfilter.util import safe_filename


class AtomicWriteRetryTests(unittest.TestCase):
    """os.replace retries transient Windows PermissionError then re-raises."""

    def setUp(self):
        self._real_replace = os.replace
        self._orig = (config.FILE_REPLACE_RETRIES, config.FILE_REPLACE_DELAY_SECONDS)
        config.FILE_REPLACE_RETRIES = 4
        config.FILE_REPLACE_DELAY_SECONDS = 0  # no sleeping in tests
        self.path = Path(tempfile.mkdtemp()) / "cache.bin"

    def tearDown(self):
        os.replace = self._real_replace
        config.FILE_REPLACE_RETRIES, config.FILE_REPLACE_DELAY_SECONDS = self._orig

    def test_write_survives_transient_permission_error(self):
        calls = {"n": 0}
        real = self._real_replace

        def flaky(a, b):
            calls["n"] += 1
            if calls["n"] < config.FILE_REPLACE_RETRIES:   # fail N-1 times
                raise PermissionError(5, "Access is denied")
            return real(a, b)

        os.replace = flaky
        util.atomic_write_bytes(self.path, b"payload")
        self.assertEqual(self.path.read_bytes(), b"payload")
        self.assertEqual(calls["n"], config.FILE_REPLACE_RETRIES)

    def test_reraises_after_exhausting_retries(self):
        calls = {"n": 0}

        def always_fail(a, b):
            calls["n"] += 1
            raise PermissionError(5, "Access is denied")

        os.replace = always_fail
        with self.assertRaises(PermissionError):
            util.atomic_write_bytes(self.path, b"x")
        self.assertEqual(calls["n"], config.FILE_REPLACE_RETRIES)


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
