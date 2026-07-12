"""Tests for the request-log filter in app.py that mutes the /api/mail poll."""

import logging
import shutil
import tempfile
import unittest
from pathlib import Path

import config

# `import app` builds the app AT IMPORT TIME, so every path create_app() touches must
# be redirected first -- especially the ones it *seeds* on a first run (the categories
# and the starter reply template), which would otherwise be written into the user's
# real caches. Any new store added to the factory belongs in this list.
_ISOLATED = (
    "CACHE_FILE", "SETTINGS_FILE", "TAGS_FILE", "TEMPLATES_DIR",
    "AUTOMATIONS_FILE", "CUSTOMERS_FILE", "CATEGORIES_FILE",
    "COMPOSE_TEMPLATES_FILE", "MAILBOX_FILE", "PASSWORD_SETTINGS_FILE",
    "EXPERIMENTAL_FILE", "CUSTOMER_MATCH_FILE", "CALENDAR_PINS_FILE",
    "VAULT_FILE", "VAULT_INDEX_FILE", "VAULT_KEY_DPAPI_FILE",
)


class PollingLogFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        cls._orig = {name: getattr(config, name) for name in _ISOLATED}
        for name in _ISOLATED:
            setattr(config, name, Path(cls._tmp) / name.lower())
        import app
        cls.filt = app._MutePollingAccessLog()

    @classmethod
    def tearDownClass(cls):
        for name, value in cls._orig.items():
            setattr(config, name, value)
        shutil.rmtree(cls._tmp, ignore_errors=True)

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
