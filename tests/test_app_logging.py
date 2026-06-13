"""Tests for the request-log filter in app.py that mutes the /api/mail poll."""

import logging
import tempfile
import unittest
from pathlib import Path

import config


class PollingLogFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Point the stores at temp files before importing app (which builds the
        # app at import time), so the real caches are never read or written.
        cls._orig = (config.CACHE_FILE, config.SETTINGS_FILE, config.TAGS_FILE)
        config.CACHE_FILE = Path(tempfile.mkdtemp()) / "cache.json"
        config.SETTINGS_FILE = Path(tempfile.mkdtemp()) / "settings.json"
        config.TAGS_FILE = Path(tempfile.mkdtemp()) / "tags.json"
        import app
        cls.filt = app._MutePollingAccessLog()

    @classmethod
    def tearDownClass(cls):
        config.CACHE_FILE, config.SETTINGS_FILE, config.TAGS_FILE = cls._orig

    def _record(self, requestline):
        # Mirrors werkzeug's access-log record shape.
        return logging.LogRecord(
            "werkzeug", logging.INFO, __file__, 0,
            '%s - - [t] "%s" %s %s', ("127.0.0.1", requestline, 200, "-"), None,
        )

    def test_drops_api_mail_poll(self):
        self.assertFalse(self.filt.filter(self._record("GET /api/mail?main=x HTTP/1.1")))

    def test_keeps_other_requests(self):
        for line in (
            "POST /refresh HTTP/1.1",
            "GET /api/settings HTTP/1.1",
            "GET /api/thread?id=a HTTP/1.1",
            "GET /attachments/a/0 HTTP/1.1",
            "GET / HTTP/1.1",
        ):
            self.assertTrue(self.filt.filter(self._record(line)), line)


if __name__ == "__main__":
    unittest.main()
