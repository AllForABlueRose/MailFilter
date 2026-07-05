"""HTTP-layer tests for the Unlock Station routes (/api/workspace/files, /unlock,
/record-assignment, /smart-unlock) via the test client against throwaway caches
and a temp WORKSPACE_DIR. The zip flows use only the stdlib. Skipped wholesale
when `cryptography` is absent (the vault can't unlock without it)."""

import shutil
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

import config
from mailfilter import create_app, vault_crypto, workspace_manifest

_ISOLATED = (
    "CACHE_FILE", "SETTINGS_FILE", "TAGS_FILE", "TEMPLATES_DIR",
    "AUTOMATIONS_FILE", "CUSTOMERS_FILE", "COMPOSE_TEMPLATES_FILE",
    "PASSWORD_SETTINGS_FILE", "EXPERIMENTAL_FILE", "CUSTOMER_MATCH_FILE",
    "VAULT_FILE", "VAULT_INDEX_FILE", "VAULT_KEY_DPAPI_FILE", "WORKSPACE_DIR",
)


@unittest.skipUnless(vault_crypto.is_available(), "cryptography not installed")
class UnlockRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._orig = {name: getattr(config, name) for name in _ISOLATED}
        for name in _ISOLATED:
            setattr(config, name, Path(self._tmpdir) / name.lower())
        self.app = create_app()
        self.client = self.app.test_client()
        self.org = self.client.post("/api/organizations",
                                    json={"name": "Acme Corp"}).get_json()
        self.client.post("/api/vault/init", json={"passphrase": "passphrase1"})
        self.key = self.client.post("/api/vault/entries", json={
            "org_id": self.org["id"], "label": "Zip key",
            "secret": "topsecret", "kind": "managed"}).get_json()

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(config, name, value)
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _today(self):
        folder = config.WORKSPACE_DIR / datetime.now().strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _plain_zip(self, name="pack.zip", inner="inner.txt", org=True):
        folder = self._today()
        with zipfile.ZipFile(folder / name, "w") as z:
            z.writestr(inner, b"payload")
        if org:
            workspace_manifest.record(str(folder), name, {
                "org_id": self.org["id"], "org_name": "Acme Corp", "mail_id": "m1"})
        return folder

    def test_files_route_lists_download_and_external(self):
        folder = self._plain_zip()
        (folder / "user.txt").write_text("dropped")
        data = self.client.get("/api/workspace/files").get_json()
        self.assertTrue(data["exists"])
        by = {f["name"]: f for f in data["files"]}
        self.assertEqual(by["pack.zip"]["source"], "download")
        self.assertEqual(by["pack.zip"]["org_name"], "Acme Corp")
        self.assertEqual(by["user.txt"]["source"], "external")

    def test_unlock_423_when_locked(self):
        self._plain_zip()
        self.client.post("/api/vault/lock")
        self.assertEqual(self.client.post("/api/workspace/unlock", json={}).status_code, 423)

    def test_smart_unlock_423_when_locked(self):
        self._plain_zip()
        self.client.post("/api/vault/lock")
        self.assertEqual(self.client.post("/api/workspace/smart-unlock", json={}).status_code, 423)

    def test_manual_unlock_with_assigned_key(self):
        folder = self._plain_zip()
        res = self.client.post("/api/workspace/unlock", json={
            "assignments": {"pack.zip": self.key["id"]}}).get_json()
        self.assertEqual(res["errors"], [])
        self.assertEqual(len(res["unlocked"]), 1)
        self.assertEqual(res["unlocked"][0]["key_kind"], "managed")
        self.assertFalse((folder / "pack.zip").exists())
        self.assertTrue((folder / "inner_Acme Corp.txt").exists())

    def test_record_assignment_writes_pattern(self):
        self.client.post("/api/workspace/record-assignment", json={"records": [
            {"org_id": self.org["id"], "file_kind": "zip", "key_kind": "managed"}]})
        org = self.client.get("/api/organizations").get_json()["organizations"][0]
        self.assertEqual(org["key_assignments"],
                         [{"file_kind": "zip", "selector": "managed",
                           "recorded": org["key_assignments"][0]["recorded"]}])

    def test_smart_unlock_replays_recorded_pattern(self):
        folder = self._plain_zip()
        # Teach the org: zip files unlock with the managed key.
        self.client.post("/api/workspace/record-assignment", json={"records": [
            {"org_id": self.org["id"], "file_kind": "zip", "key_kind": "managed"}]})
        res = self.client.post("/api/workspace/smart-unlock", json={}).get_json()
        self.assertEqual(res["errors"], [])
        self.assertEqual(len(res["unlocked"]), 1)
        self.assertTrue((folder / "inner_Acme Corp.txt").exists())


if __name__ == "__main__":
    unittest.main()
