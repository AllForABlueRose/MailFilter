"""Tests for mailfilter.workspace_manifest: the per-folder org sidecar. Uses a
temp folder, so the encoded manifest round-trips through the real persistence/crypto
seam without touching any real workspace."""

import tempfile
import unittest
from pathlib import Path

import config
from mailfilter import workspace_manifest

META = {"org_id": "o1", "org_name": "Acme Corp", "mail_id": "m1",
        "received": "2026-01-02 09:30:00"}


class WorkspaceManifestTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def _manifest_path(self):
        return Path(self.folder) / config.WORKSPACE_MANIFEST_NAME

    def test_record_then_lookup_roundtrips(self):
        workspace_manifest.record(self.folder, "report.pdf", META)
        self.assertEqual(workspace_manifest.lookup(self.folder, "report.pdf"), META)
        self.assertTrue(self._manifest_path().exists())

    def test_record_coerces_to_known_fields(self):
        workspace_manifest.record(self.folder, "x.zip",
                                  {"org_id": "o", "junk": "drop", "org_name": None})
        self.assertEqual(workspace_manifest.lookup(self.folder, "x.zip"),
                         {"org_id": "o", "org_name": "", "mail_id": "", "received": ""})

    def test_received_roundtrips(self):
        workspace_manifest.record(self.folder, "r.zip",
                                  {"mail_id": "m1", "received": "2026-03-04 12:00:00"})
        got = workspace_manifest.lookup(self.folder, "r.zip")
        self.assertEqual(got["received"], "2026-03-04 12:00:00")

    def test_is_app_file_and_external_files(self):
        workspace_manifest.record(self.folder, "app.zip", META)
        self.assertTrue(workspace_manifest.is_app_file(self.folder, "app.zip"))
        self.assertFalse(workspace_manifest.is_app_file(self.folder, "user.txt"))
        # The manifest file itself is never reported as a workspace file.
        names = ["app.zip", "user.txt", config.WORKSPACE_MANIFEST_NAME]
        self.assertEqual(workspace_manifest.external_files(self.folder, names), ["user.txt"])

    def test_remove_prunes_and_deletes_empty_manifest(self):
        workspace_manifest.record(self.folder, "a.zip", META)
        workspace_manifest.remove(self.folder, "a.zip")
        self.assertIsNone(workspace_manifest.lookup(self.folder, "a.zip"))
        # An emptied manifest is removed from disk entirely.
        self.assertFalse(self._manifest_path().exists())

    def test_rename_moves_entry(self):
        workspace_manifest.record(self.folder, "old.xlsx", META)
        workspace_manifest.rename(self.folder, "old.xlsx", "new.xlsx")
        self.assertIsNone(workspace_manifest.lookup(self.folder, "old.xlsx"))
        self.assertEqual(workspace_manifest.lookup(self.folder, "new.xlsx"), META)

    def test_load_absent_is_empty(self):
        self.assertEqual(workspace_manifest.load(self.folder), {})


if __name__ == "__main__":
    unittest.main()
