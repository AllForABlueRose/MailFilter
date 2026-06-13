"""Tests for mailfilter.outlook's graceful behaviour without pywin32.

The COM-touching paths can't be exercised off Windows, so these tests cover
the fallback contract: a refresh on a machine without Outlook must not crash —
it records a failure status and keeps serving the cache.
"""

import tempfile
import unittest
from pathlib import Path

from mailfilter import outlook
from mailfilter.store import MailStore

try:
    import win32com  # noqa: F401
    HAVE_PYWIN32 = True
except ImportError:
    HAVE_PYWIN32 = False


@unittest.skipIf(HAVE_PYWIN32, "pywin32 present; the unavailable-path tests don't apply")
class WithoutPywin32Tests(unittest.TestCase):
    def test_import_raises_outlook_unavailable(self):
        with self.assertRaises(outlook.OutlookUnavailableError):
            outlook._import_pywin32()

    def test_refresh_records_failure_without_crashing(self):
        store = MailStore(Path(tempfile.mkdtemp()) / "cache.json")
        self.assertTrue(outlook.refresh(store))  # ran (didn't skip)
        status = store.status_snapshot()
        self.assertEqual(status["fetch_status"], "Failed")
        self.assertTrue(status["fetch_error"])

    def test_fetch_attachment_raises_outlook_unavailable(self):
        with self.assertRaises(outlook.OutlookUnavailableError):
            outlook.fetch_attachment("any-id", 0)


class FetchLockTests(unittest.TestCase):
    def test_refresh_skips_when_a_fetch_is_already_running(self):
        store = MailStore(Path(tempfile.mkdtemp()) / "cache.json")
        acquired = outlook._fetch_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            self.assertIs(outlook.refresh(store), False)
            # Status untouched because the call returned before set_fetching().
            self.assertEqual(store.status_snapshot()["fetch_status"], "Not started")
        finally:
            outlook._fetch_lock.release()


if __name__ == "__main__":
    unittest.main()
