"""HTTP-layer tests for the Unlock Station routes (/api/workspace/files, /unlock,
/record-assignment, /smart-unlock) via the test client against throwaway caches
and a temp WORKSPACE_DIR. The zip flows use only the stdlib. Skipped wholesale
when `cryptography` is absent (the vault can't unlock without it)."""

import shutil
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import config
from mailfilter import create_app, unlock_ops, vault_crypto, workspace_manifest

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

    def _temp_key(self, secret, scan_dt):
        return self.client.post("/api/vault/entries", json={
            "org_id": self.org["id"], "label": secret, "secret": secret,
            "kind": "temporary", "scan_dt": scan_dt}).get_json()

    def _zip_with_received(self, name, received):
        folder = self._today()
        with zipfile.ZipFile(folder / name, "w") as z:
            z.writestr("inner.txt", b"payload")
        workspace_manifest.record(str(folder), name, {
            "org_id": self.org["id"], "org_name": "Acme Corp", "mail_id": "m1",
            "received": received})
        return folder

    def _smart_unlock_assignments(self):
        """Run smart-unlock with the engine patched out, returning the resolver's
        {filename: assignment} map so key->file pairing can be asserted directly."""
        captured = {}
        def _capture(assignments):
            captured.update(assignments)
            return {"folder": "", "unlocked": [], "errors": []}
        with mock.patch.object(unlock_ops, "unlock_files", side_effect=_capture):
            self.client.post("/api/workspace/smart-unlock", json={})
        return captured

    def _recent(self, hours_ago):
        # scan_dt within VAULT_TEMP_HIDE_AFTER_DAYS so the temporary key isn't pruned.
        return (datetime.now() - timedelta(hours=hours_ago)).strftime(config.RECEIVED_FORMAT)

    def test_smart_unlock_pairs_multiple_files_newest_to_oldest(self):
        # Two temporary keys (newest first: sNew, then sOld) and three zips.
        self._temp_key("sOld", self._recent(2))
        self._temp_key("sNew", self._recent(1))
        self._zip_with_received("old.zip", "2026-01-01 09:00:00")
        self._zip_with_received("mid.zip", "2026-02-02 09:00:00")
        self._zip_with_received("new.zip", "2026-03-03 09:00:00")
        self.client.post("/api/workspace/record-assignment", json={"records": [
            {"org_id": self.org["id"], "file_kind": "zip", "key_kind": "temporary"}]})
        captured = self._smart_unlock_assignments()
        # newest file -> newest key, next -> older key, oldest file left unassigned.
        self.assertEqual(captured["new.zip"]["secret"], "sNew")
        self.assertEqual(captured["mid.zip"]["secret"], "sOld")
        self.assertIsNone(captured["old.zip"]["secret"])

    def test_smart_unlock_single_file_uses_newest_key(self):
        self._temp_key("sOld", self._recent(2))
        self._temp_key("sNew", self._recent(1))
        self._zip_with_received("solo.zip", "2026-03-03 09:00:00")
        self.client.post("/api/workspace/record-assignment", json={"records": [
            {"org_id": self.org["id"], "file_kind": "zip", "key_kind": "temporary"}]})
        captured = self._smart_unlock_assignments()
        self.assertEqual(captured["solo.zip"]["secret"], "sNew")

    def _file_with_org(self, name, content=b"x"):
        folder = self._today()
        (folder / name).write_bytes(content)
        workspace_manifest.record(str(folder), name, {
            "org_id": self.org["id"], "org_name": "Acme Corp", "mail_id": "m1"})
        return folder

    def test_record_assignment_all_files_writes_cross_kind_habit(self):
        self.client.post("/api/workspace/record-assignment",
                         json={"all_files": [self.org["id"]]})
        org = self.client.get("/api/organizations").get_json()["organizations"][0]
        self.assertEqual(org["all_files_key"]["selector"], "managed")

    def test_smart_unlock_broadcasts_all_files_key_across_kinds(self):
        # Org habit: one managed key for every file, regardless of kind. The single
        # managed key ("topsecret") should reach both the zip and the excel.
        self.client.post("/api/workspace/record-assignment",
                         json={"all_files": [self.org["id"]]})
        self._plain_zip("pack.zip")
        self._file_with_org("book.xlsx")
        captured = self._smart_unlock_assignments()
        self.assertEqual(captured["pack.zip"]["secret"], "topsecret")
        self.assertEqual(captured["book.xlsx"]["secret"], "topsecret")
        self.assertEqual(captured["book.xlsx"]["file_kind"], "excel")

    def test_all_files_key_takes_precedence_over_per_kind(self):
        # Even with a per-kind zip pattern recorded, the broadcast habit wins: the
        # zip gets the managed key (not a per-kind temporary pairing).
        self._temp_key("sTemp", self._recent(1))
        self.client.post("/api/workspace/record-assignment", json={"records": [
            {"org_id": self.org["id"], "file_kind": "zip", "key_kind": "temporary"}]})
        self.client.post("/api/workspace/record-assignment",
                         json={"all_files": [self.org["id"]]})
        self._plain_zip("pack.zip")
        captured = self._smart_unlock_assignments()
        self.assertEqual(captured["pack.zip"]["secret"], "topsecret")


if __name__ == "__main__":
    unittest.main()
