"""HTTP-layer tests for the Workshop → Calendar routes (/api/calendar/*) via the
test client against throwaway caches and a temp WORKSPACE_DIR + CALENDAR_PINS_FILE.
Also drives the startup materializer extension end-to-end. Stdlib only."""

import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import config
from mailfilter import create_app, workspace_manifest

_ISOLATED = (
    "CACHE_FILE", "SETTINGS_FILE", "TAGS_FILE", "TEMPLATES_DIR",
    "AUTOMATIONS_FILE", "CUSTOMERS_FILE", "COMPOSE_TEMPLATES_FILE",
    "PASSWORD_SETTINGS_FILE", "EXPERIMENTAL_FILE", "CUSTOMER_MATCH_FILE",
    "VAULT_FILE", "VAULT_INDEX_FILE", "VAULT_KEY_DPAPI_FILE",
    "WORKSPACE_DIR", "CALENDAR_PINS_FILE",
)


class CalendarRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig = {name: getattr(config, name) for name in _ISOLATED}
        for name in _ISOLATED:
            setattr(config, name, Path(self._tmpdir) / name.lower())
        self.app = create_app()
        self.client = self.app.test_client()
        self.today = datetime.now().strftime("%Y-%m-%d")

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(config, name, value)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _seed(self, name="invoice.pdf"):
        folder = config.WORKSPACE_DIR / self.today
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(b"DATA")
        workspace_manifest.record(str(folder), name,
            {"org_id": "o1", "org_name": "Acme Corp", "mail_id": "m1"})
        return folder

    def test_create_workspace(self):
        self.assertFalse(self.client.get("/api/workspace/files").get_json()["exists"])
        r = self.client.post("/api/calendar/create-workspace")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(self.client.get("/api/workspace/files").get_json()["exists"])

    def test_pin_list_and_delete(self):
        self._seed()
        pin = self.client.post("/api/calendar/pins", json={
            "date": self.today, "filename": "invoice.pdf",
            "description": "send it"}).get_json()
        self.assertEqual(pin["org_name"], "Acme Corp")
        pins = self.client.get("/api/calendar/pins").get_json()["pins"]
        self.assertEqual(len(pins), 1)
        r = self.client.delete("/api/calendar/pins/" + pin["id"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.client.get("/api/calendar/pins").get_json()["pins"]), 0)

    def test_pin_missing_file_404(self):
        self._seed()
        r = self.client.post("/api/calendar/pins",
                             json={"date": self.today, "filename": "nope.pdf"})
        self.assertEqual(r.status_code, 404)

    def test_pin_invalid_date_404(self):
        self._seed()
        r = self.client.post("/api/calendar/pins",
                             json={"date": "bad", "filename": "invoice.pdf"})
        self.assertEqual(r.status_code, 404)

    def test_delete_unknown_pin_404(self):
        self.assertEqual(self.client.delete("/api/calendar/pins/nope").status_code, 404)

    def test_materializer_extension_places_pinned_file(self):
        folder = self._seed()
        self.client.post("/api/calendar/pins",
                         json={"date": self.today, "filename": "invoice.pdf"})
        (folder / "invoice.pdf").unlink()  # remove source; only the limbo copy remains
        moved = self.app.extensions["calendar_materializer"]()
        self.assertEqual(moved, ["invoice.pdf"])
        self.assertTrue((folder / "invoice.pdf").is_file())
        self.assertTrue(
            self.client.get("/api/calendar/pins").get_json()["pins"][0]["materialized"])


if __name__ == "__main__":
    unittest.main()
