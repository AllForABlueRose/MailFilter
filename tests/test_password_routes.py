"""HTTP-layer tests for Smart Password Detection: the settings endpoints, the
on-demand scan, and the `passwords` sidebar filter — all via the test client
against throwaway caches, so the real caches and Outlook are never touched."""

import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import config
from mailfilter import create_app
from tests.factories import make_mail

_ISOLATED = (
    "CACHE_FILE", "SETTINGS_FILE", "TAGS_FILE", "TEMPLATES_DIR",
    "AUTOMATIONS_FILE", "CUSTOMERS_FILE", "COMPOSE_TEMPLATES_FILE",
    "PASSWORD_SETTINGS_FILE", "EXPERIMENTAL_FILE",
    # The scan route now touches the vault store (auto-capture); isolate its files
    # so a locked-vault scan can never read or write the real vault on disk.
    "VAULT_FILE", "VAULT_INDEX_FILE", "VAULT_KEY_DPAPI_FILE",
)


def _recent(days_ago=0):
    return (datetime.now() - timedelta(days=days_ago)).strftime(config.RECEIVED_FORMAT)


class PasswordRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig = {name: getattr(config, name) for name in _ISOLATED}
        for name in _ISOLATED:
            setattr(config, name, Path(self._tmpdir) / name.lower())
        self.app = create_app()
        # Smart Password Detection is experimental-gated: the scan no-ops unless the
        # feature is enabled. Turn it on so the scan tests exercise the real path.
        self.app.extensions["experimental_store"].update({"passwords": True})
        self.store = self.app.extensions["mail_store"]
        self.store.add_mails([
            make_mail(id="P", subject="creds", received=_recent(1), attachments=[],
                      body="Hello,\nPassword:\n Hunter2xZ\nRegards"),
            make_mail(id="N", subject="newsletter", received=_recent(1), attachments=[],
                      body="Nothing sensitive here at all."),
        ])
        self.client = self.app.test_client()

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(config, name, value)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_get_settings_returns_seeded_defaults(self):
        data = self.client.get("/api/password-settings").get_json()
        self.assertEqual(len(data["patterns"]), len(config.PASSWORD_DEFAULT_PATTERNS))
        self.assertEqual(data["rules"], config.PASSWORD_RULE_DEFAULTS)

    def test_save_settings_persists_and_clamps(self):
        out = self.client.post("/api/password-settings",
                               json={"rules": {"min_length": 99, "max_length": 5}}).get_json()
        # Swapped/clamped bounds repaired server-side.
        self.assertLessEqual(out["rules"]["min_length"], out["rules"]["max_length"])

    def test_save_rejects_non_object(self):
        self.assertEqual(self.client.post("/api/password-settings", json=[1, 2]).status_code, 400)

    def test_scan_flags_and_filter_composes(self):
        scan = self.client.post("/api/passwords/scan").get_json()
        self.assertEqual(scan["scanned"], 2)
        self.assertEqual(scan["flagged"], 1)
        self.assertEqual(scan["pattern_errors"], [])

        # The badge data is on the view model.
        flagged = self.client.get("/api/mail?passwords=1").get_json()["mails"]
        self.assertEqual([m["id"] for m in flagged], ["P"])
        self.assertEqual(flagged[0]["passwords"], ["Hunter2xZ"])
        self.assertTrue(flagged[0]["has_password"])

        # Without the flag both mails show, but only P carries has_password.
        allm = {m["id"]: m for m in self.client.get("/api/mail").get_json()["mails"]}
        self.assertTrue(allm["P"]["has_password"])
        self.assertFalse(allm["N"]["has_password"])

    def test_scan_reflects_updated_rules(self):
        # Tighten min_length so the 9-char password no longer qualifies.
        self.client.post("/api/password-settings", json={"rules": {"min_length": 20}})
        self.assertEqual(self.client.post("/api/passwords/scan").get_json()["flagged"], 0)

    def test_scan_skipped_when_feature_disabled(self):
        # Req 1: with the experimental feature off, the scan must not run at all.
        self.app.extensions["experimental_store"].update({"passwords": False})
        scan = self.client.post("/api/passwords/scan").get_json()
        self.assertEqual(scan["scanned"], 0)
        self.assertEqual(scan["flagged"], 0)
        self.assertTrue(scan.get("skipped"))
        # The badge data is untouched — nothing was flagged.
        allm = {m["id"]: m for m in self.client.get("/api/mail").get_json()["mails"]}
        self.assertFalse(allm["P"]["has_password"])

    def test_scan_excludes_mail_older_than_window(self):
        # Req 6: only mail within PASSWORD_SCAN_MAX_AGE_DAYS is scanned.
        self.store.add_mails([
            make_mail(id="OLD", subject="old creds",
                      received=_recent(config.PASSWORD_SCAN_MAX_AGE_DAYS + 5),
                      attachments=[], body="Hello,\nPassword:\n Hunter2xZ\nRegards"),
        ])
        scan = self.client.post("/api/passwords/scan").get_json()
        # P (recent) is in-window and flagged; OLD is excluded; N has no password.
        self.assertEqual(scan["scanned"], 2)              # P + N, not OLD
        self.assertEqual(scan["flagged"], 1)              # only P
        allm = {m["id"]: m for m in self.client.get("/api/mail").get_json()["mails"]}
        self.assertFalse(allm["OLD"]["has_password"])

    def test_scan_reports_bad_pattern_by_number(self):
        # A component with no <{(password_value)}> marker can't be compiled; it is
        # reported by its 1-based position.
        self.client.post("/api/password-settings", json={"patterns": [
            {"template": "password: <{(password_value)}>"},
            {"template": "no marker here"},
        ]})
        scan = self.client.post("/api/passwords/scan").get_json()
        self.assertEqual([e["component"] for e in scan["pattern_errors"]], [2])


if __name__ == "__main__":
    unittest.main()
