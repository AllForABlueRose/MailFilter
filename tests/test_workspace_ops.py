"""Tests for mailfilter.workspace_ops: batch-attachment naming and CSV report,
including the experimental "Append Customer Name To Downloads" and "Brute Force
Resolve Customer Name" toggles, the sidecar org manifest, and the report org
column.

Outlook is mocked (the bytes fetch) and WORKSPACE_DIR is redirected to a temp
folder, so nothing touches Outlook or the real workspace.
"""

import csv
import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import config
from mailfilter import workspace_manifest, workspace_ops
from mailfilter.store import MailStore
from tests.factories import make_mail


class AppendStemTests(unittest.TestCase):
    def test_inserts_before_extension(self):
        self.assertEqual(workspace_ops.append_stem("report.pdf", "Acme Corp"),
                         "report_Acme Corp.pdf")

    def test_no_extension(self):
        self.assertEqual(workspace_ops.append_stem("README", "Acme"), "README_Acme")

    def test_only_last_extension_is_kept_outside(self):
        self.assertEqual(workspace_ops.append_stem("archive.tar.gz", "Acme"),
                         "archive.tar_Acme.gz")


class PersonTextTests(unittest.TestCase):
    """The report's per-person cell: name by default, email when name ⊂ email."""

    def test_name_by_default(self):
        self.assertEqual(workspace_ops.person_text("Alice Smith", "alice@x.com"),
                         "Alice Smith")

    def test_email_when_name_is_substring_case_insensitive(self):
        self.assertEqual(workspace_ops.person_text("alice", "alice@x.com"), "alice@x.com")
        self.assertEqual(workspace_ops.person_text("Alice", "alice@x.com"), "alice@x.com")

    def test_name_only_falls_back_to_name(self):
        self.assertEqual(workspace_ops.person_text("Alice", ""), "Alice")

    def test_email_only_falls_back_to_email(self):
        self.assertEqual(workspace_ops.person_text("", "alice@x.com"), "alice@x.com")

    def test_both_empty(self):
        self.assertEqual(workspace_ops.person_text("", ""), "")


class _FakeStore:
    def __init__(self, mails):
        self._mails = [MailStore._with_derived(m) for m in mails]

    def snapshot(self):
        return list(self._mails)


class SaveAttachmentsAppendTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = Path(self._tmp) / "workspace"
        self.store = _FakeStore([
            make_mail(id="m1", sender_email="bob@acme.com",
                      attachments=[{"filename": "report.pdf"}]),
            make_mail(id="m2", sender_email="eve@nobody.com",
                      attachments=[{"filename": "report.pdf"}]),
        ])
        self.orgs = [{"id": "1", "name": "Acme Corp",
                      "domains": [{"domain": "acme.com", "role": "representative"}],
                      "contacts": []}]

    def tearDown(self):
        config.WORKSPACE_DIR = self._orig

    def _save(self, append):
        items = [{"id": "m1", "index": 0}, {"id": "m2", "index": 0}]
        with mock.patch("mailfilter.outlook.fetch_attachment",
                        side_effect=lambda mid, idx: ("report.pdf", b"bytes")):
            return workspace_ops.save_attachments(
                self.store, items, append_org_name=append, orgs=self.orgs)

    def test_off_leaves_names_unchanged(self):
        _folder, saved, _errors = self._save(append=False)
        # Both are "report.pdf"; the second collides and is deduped, neither gets _org.
        self.assertEqual(sorted(s["name"] for s in saved), ["report.pdf", "report_1.pdf"])

    def test_on_appends_org_for_resolved_sender_only(self):
        _folder, saved, _errors = self._save(append=True)
        by_id = {s["id"]: s["name"] for s in saved}
        self.assertEqual(by_id["m1"], "report_Acme Corp.pdf")  # sender in an org
        self.assertEqual(by_id["m2"], "report.pdf")            # sender in no org


class SaveAttachmentsResolveTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = Path(self._tmp) / "workspace"
        self.store = _FakeStore([
            # m1: keyword in content, sender in no org.
            make_mail(id="m1", sender_email="x@nobody.com",
                      body="about Globex Industries", attachments=[{"filename": "a.pdf"}]),
            # m2: keyword in content AND sender resolves to Acme -> keyword org overrides.
            make_mail(id="m2", sender_email="bob@acme.com",
                      body="about Globex Industries", attachments=[{"filename": "b.pdf"}]),
        ])
        self.orgs = [
            {"id": "1", "name": "Acme Corp",
             "domains": [{"domain": "acme.com", "role": "representative"}], "contacts": []},
            {"id": "2", "name": "Globex Inc", "domains": [], "contacts": []},
        ]
        self.mappings = [{"keyword": "Globex Industries", "org_id": "2"}]

    def tearDown(self):
        config.WORKSPACE_DIR = self._orig

    def _save(self, **kw):
        items = [{"id": "m1", "index": 0}, {"id": "m2", "index": 0}]
        with mock.patch("mailfilter.outlook.fetch_attachment",
                        side_effect=lambda mid, idx: (None, b"bytes")):
            return workspace_ops.save_attachments(self.store, items, **kw)

    def test_resolve_alone_records_org_but_appends_nothing(self):
        # Brute Force on but Append off: the suffix is gated by append, so file names
        # are unchanged — yet the resolved (keyword) org is still recorded in the
        # manifest as the single source of truth.
        folder, saved, _errors = self._save(
            resolve_customer=True, customer_mappings=self.mappings, orgs=self.orgs)
        by_id = {s["id"]: s["name"] for s in saved}
        self.assertEqual(by_id["m1"], "a.pdf")
        self.assertEqual(by_id["m2"], "b.pdf")
        self.assertEqual(workspace_manifest.lookup(folder, "a.pdf")["org_name"], "Globex Inc")
        self.assertEqual(workspace_manifest.lookup(folder, "b.pdf")["org_name"], "Globex Inc")

    def test_resolve_and_append_appends_keyword_org(self):
        folder, saved, _errors = self._save(
            append_org_name=True, resolve_customer=True,
            customer_mappings=self.mappings, orgs=self.orgs)
        by_id = {s["id"]: s["name"] for s in saved}
        self.assertEqual(by_id["m1"], "a_Globex Inc.pdf")
        self.assertEqual(by_id["m2"], "b_Globex Inc.pdf")

    def test_bruteforce_overrides_sender_org(self):
        # Baseline: append only -> m2 carries its sender org (Acme).
        _folder, saved, _errors = self._save(append_org_name=True, orgs=self.orgs)
        self.assertEqual({s["id"]: s["name"] for s in saved}["m2"], "b_Acme Corp.pdf")
        # With Brute Force also on, the keyword org (Globex) overrides Acme for m2,
        # and m1 (no sender org) still resolves via the keyword.
        _folder, saved, _errors = self._save(
            append_org_name=True, resolve_customer=True,
            customer_mappings=self.mappings, orgs=self.orgs)
        by_id = {s["id"]: s["name"] for s in saved}
        self.assertEqual(by_id["m1"], "a_Globex Inc.pdf")
        self.assertEqual(by_id["m2"], "b_Globex Inc.pdf")


class SaveAttachmentsManifestTests(unittest.TestCase):
    """Every download is recorded in the folder manifest (org blank if unresolved),
    and the file's own bytes are written verbatim (no embedding)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = Path(self._tmp) / "workspace"
        self.store = _FakeStore([
            make_mail(id="m1", body="about Globex Industries",
                      attachments=[{"filename": "pic.png"}]),
            make_mail(id="m2", body="nothing relevant",
                      attachments=[{"filename": "pic.png"}]),
        ])
        self.orgs = [{"id": "2", "name": "Globex Inc", "domains": [], "contacts": []}]
        self.mappings = [{"keyword": "Globex Industries", "org_id": "2"}]

    def tearDown(self):
        config.WORKSPACE_DIR = self._orig

    def test_manifest_written_on_every_download(self):
        raw = b"the-verbatim-attachment-bytes-for-the-test!!"
        items = [{"id": "m1", "index": 0}, {"id": "m2", "index": 0}]
        with mock.patch("mailfilter.outlook.fetch_attachment",
                        side_effect=lambda mid, idx: ("pic.png", raw)):
            folder, saved, _errors = workspace_ops.save_attachments(
                self.store, items, resolve_customer=True,
                customer_mappings=self.mappings, orgs=self.orgs)
        by_id = {s["id"]: s["name"] for s in saved}
        # File bytes are the raw attachment, untouched.
        self.assertEqual((Path(folder) / by_id["m1"]).read_bytes(), raw)
        # Resolved mail carries the org; unresolved mail is still recorded, org blank.
        # Both record the originating mail's received datetime.
        self.assertEqual(workspace_manifest.lookup(folder, by_id["m1"]),
                         {"org_id": "2", "org_name": "Globex Inc", "mail_id": "m1",
                          "received": "2026-06-10 09:30:00"})
        self.assertEqual(workspace_manifest.lookup(folder, by_id["m2"]),
                         {"org_id": "", "org_name": "", "mail_id": "m2",
                          "received": "2026-06-10 09:30:00"})

    def test_download_stamped_with_mail_datetime(self):
        raw = b"bytes"
        items = [{"id": "m1", "index": 0}]
        with mock.patch("mailfilter.outlook.fetch_attachment",
                        side_effect=lambda mid, idx: ("pic.png", raw)):
            folder, saved, _errors = workspace_ops.save_attachments(self.store, items)
        target = Path(folder) / saved[0]["name"]
        expected = datetime.strptime("2026-06-10 09:30:00", config.RECEIVED_FORMAT).timestamp()
        self.assertEqual(target.stat().st_mtime, expected)


class WriteReportTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = Path(self._tmp) / "workspace"
        self.store = _FakeStore([
            make_mail(id="m1", subject="Re: Globex Industries", body="x"),
            make_mail(id="m2", subject="other", body="nothing relevant"),
        ])
        self.orgs = [{"id": "2", "name": "Globex Inc"}]
        self.mappings = [{"keyword": "Globex Industries", "org_id": "2"}]

    def tearDown(self):
        config.WORKSPACE_DIR = self._orig

    def _rows(self, folder, name):
        text = (Path(folder) / name).read_text(encoding="utf-8-sig")
        return list(csv.reader(io.StringIO(text)))

    def test_org_column_populated_from_bruteforce(self):
        folder, name, count = workspace_ops.write_report(
            self.store, ["m1", "m2"], mappings=self.mappings, orgs=self.orgs)
        rows = self._rows(folder, name)
        self.assertEqual(rows[0],
                         ["Datetime", "subject", "recipient", "sender", "customer organization"])
        self.assertEqual(rows[1][-1], "Globex Inc")  # m1 matched the keyword
        self.assertEqual(rows[2][-1], "")            # m2 did not
        self.assertEqual(count, 2)

    def test_defaults_blank_column_without_mappings(self):
        folder, name, _count = workspace_ops.write_report(self.store, ["m1"])
        rows = self._rows(folder, name)
        self.assertEqual(rows[0][-1], "customer organization")
        self.assertEqual(rows[1][-1], "")

    def test_same_day_export_overwrites_in_place(self):
        folder, name, _ = workspace_ops.write_report(self.store, ["m1", "m2"])
        self.assertEqual(len(self._rows(folder, name)), 3)   # header + 2 rows
        folder2, name2, _ = workspace_ops.write_report(self.store, ["m1"])
        self.assertEqual(name2, name)                        # same fixed filename
        self.assertEqual(len(self._rows(folder2, name2)), 2)  # overwritten: header + 1
        self.assertEqual(len(list(Path(folder2).glob("report_*.csv"))), 1)  # no _1 dedup

    def test_person_cells_follow_name_or_email_rule(self):
        store = _FakeStore([
            make_mail(id="a", sender="alice", sender_email="alice@x.com",
                      recipient_names=["Bob Jones"], recipient_emails=["bjones@x.com"]),
        ])
        folder, name, _ = workspace_ops.write_report(store, ["a"])
        row = self._rows(folder, name)[1]
        self.assertEqual(row[3], "alice@x.com")  # "alice" ⊂ email -> email
        self.assertEqual(row[2], "Bob Jones")    # "Bob Jones" ⊄ email -> name


class CleanupWorkspaceTests(unittest.TestCase):
    """cleanup_workspace deletes only manifest-listed (app-downloaded) files."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = Path(self._tmp) / "workspace"

    def tearDown(self):
        config.WORKSPACE_DIR = self._orig

    def _today_folder(self):
        from datetime import datetime
        folder = config.WORKSPACE_DIR / datetime.now().strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def test_deletes_manifest_listed_keeps_incidental(self):
        folder = self._today_folder()
        (folder / "app.png").write_bytes(b"app-downloaded-image-bytes!!")
        workspace_manifest.record(str(folder), "app.png",
                                  {"org_id": "", "org_name": "", "mail_id": "m1"})
        (folder / "notes.txt").write_text("a file the user dropped here")
        (folder / "manual.png").write_bytes(b"no-manifest-entry-here")

        rfolder, deleted, kept = workspace_ops.cleanup_workspace()

        self.assertEqual(str(folder), rfolder)
        self.assertEqual(deleted, ["app.png"])
        self.assertEqual(sorted(kept), ["manual.png", "notes.txt"])
        self.assertFalse((folder / "app.png").exists())
        self.assertTrue((folder / "notes.txt").exists())
        self.assertTrue((folder / "manual.png").exists())
        # The emptied manifest is pruned away entirely.
        self.assertFalse((folder / config.WORKSPACE_MANIFEST_NAME).exists())

    def test_missing_folder_is_a_noop(self):
        folder, deleted, kept = workspace_ops.cleanup_workspace()
        self.assertEqual(deleted, [])
        self.assertEqual(kept, [])

    def test_ignores_subdirectories(self):
        folder = self._today_folder()
        (folder / "sub").mkdir()
        (folder / "sub" / "inner.png").write_bytes(b"x")
        # Even if a subdir file were somehow recorded, cleanup never recurses.
        workspace_manifest.record(str(folder), "inner.png",
                                  {"org_id": "", "org_name": "", "mail_id": "z"})
        _f, deleted, kept = workspace_ops.cleanup_workspace()
        self.assertEqual(deleted, [])                 # subdir not recursed
        self.assertTrue((folder / "sub" / "inner.png").exists())


class BringLastWorkspaceTests(unittest.TestCase):
    """workspace_ops.bring_last_workspace_to_today: rename the newest past dated
    folder to today so it becomes today's workspace."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = Path(self._tmp) / "workspace"
        self._today = datetime.now().strftime("%Y-%m-%d")

    def tearDown(self):
        config.WORKSPACE_DIR = self._orig

    def _make(self, name, *files):
        folder = config.WORKSPACE_DIR / name
        folder.mkdir(parents=True)
        for f in files:
            (folder / f).write_text(f)
        return folder

    def test_renames_previous_folder_and_moves_contents(self):
        self._make("2020-01-02", "carried.txt")
        result = workspace_ops.bring_last_workspace_to_today()
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "2020-01-02")
        today_path = config.WORKSPACE_DIR / self._today
        self.assertTrue((today_path / "carried.txt").exists())
        self.assertFalse((config.WORKSPACE_DIR / "2020-01-02").exists())

    def test_picks_the_newest_of_several(self):
        self._make("2019-05-01")
        self._make("2021-11-30", "newest.txt")
        self._make("2020-07-15")
        result = workspace_ops.bring_last_workspace_to_today()
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "2021-11-30")
        self.assertTrue((config.WORKSPACE_DIR / self._today / "newest.txt").exists())

    def test_refuses_when_today_already_exists(self):
        self._make(self._today)
        self._make("2020-01-02", "carried.txt")
        result = workspace_ops.bring_last_workspace_to_today()
        self.assertFalse(result["ok"])
        self.assertIn("already exists", result["error"])
        self.assertTrue((config.WORKSPACE_DIR / "2020-01-02").exists())  # untouched

    def test_errors_when_no_candidate(self):
        config.WORKSPACE_DIR.mkdir(parents=True)
        # Only the limbo sibling and a non-date folder — neither is a candidate.
        (config.WORKSPACE_DIR / config.WORKSPACE_LIMBO_DIRNAME).mkdir()
        (config.WORKSPACE_DIR / "notes").mkdir()
        result = workspace_ops.bring_last_workspace_to_today()
        self.assertFalse(result["ok"])
        self.assertIn("No previous workspace", result["error"])

    def test_missing_workspace_dir_is_an_error_not_a_crash(self):
        result = workspace_ops.bring_last_workspace_to_today()
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_carried_report_is_redated_to_today(self):
        self._make("2020-01-02", "report_2020-01-02.csv", "carried.txt")
        result = workspace_ops.bring_last_workspace_to_today()
        self.assertTrue(result["ok"])
        today_path = config.WORKSPACE_DIR / self._today
        self.assertTrue((today_path / f"report_{self._today}.csv").exists())
        self.assertFalse((today_path / "report_2020-01-02.csv").exists())
        self.assertTrue((today_path / "carried.txt").exists())   # other files untouched

    def test_no_report_still_succeeds(self):
        self._make("2020-01-02", "carried.txt")
        result = workspace_ops.bring_last_workspace_to_today()
        self.assertTrue(result["ok"])
        today_path = config.WORKSPACE_DIR / self._today
        self.assertEqual(list(today_path.glob("report_*.csv")), [])


class StampFileOrgTests(unittest.TestCase):
    """workspace_ops.stamp_file_org: set/overwrite/clear a today's-workspace file's org."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = Path(self._tmp) / "workspace"
        self.folder = config.WORKSPACE_DIR / datetime.now().strftime("%Y-%m-%d")
        self.folder.mkdir(parents=True)

    def tearDown(self):
        config.WORKSPACE_DIR = self._orig

    def test_stamp_sets_org_on_external_file(self):
        (self.folder / "notes.xlsx").write_text("x")
        res = workspace_ops.stamp_file_org("notes.xlsx", "o1", "Acme Corp")
        self.assertTrue(res["ok"])
        meta = workspace_manifest.lookup(str(self.folder), "notes.xlsx")
        self.assertEqual((meta["org_id"], meta["org_name"]), ("o1", "Acme Corp"))

    def test_stamp_merges_preserving_mail_fields(self):
        (self.folder / "inv.pdf").write_text("x")
        workspace_manifest.record(str(self.folder), "inv.pdf", {
            "org_id": "", "org_name": "", "mail_id": "m9", "received": "2026-05-05 08:00:00"})
        workspace_ops.stamp_file_org("inv.pdf", "o2", "Orion")
        meta = workspace_manifest.lookup(str(self.folder), "inv.pdf")
        self.assertEqual(meta["org_id"], "o2")
        self.assertEqual(meta["mail_id"], "m9")            # preserved
        self.assertEqual(meta["received"], "2026-05-05 08:00:00")

    def test_clear_removes_manifest_entry(self):
        (self.folder / "inv.pdf").write_text("x")
        workspace_manifest.record(str(self.folder), "inv.pdf", {
            "org_id": "o1", "org_name": "Acme Corp", "mail_id": "m1", "received": ""})
        res = workspace_ops.stamp_file_org("inv.pdf", "", "")
        self.assertTrue(res["ok"])
        self.assertIsNone(workspace_manifest.lookup(str(self.folder), "inv.pdf"))

    def test_missing_file_is_error(self):
        self.assertFalse(workspace_ops.stamp_file_org("ghost.txt", "o1", "Acme")["ok"])

    def test_path_traversal_rejected(self):
        self.assertFalse(workspace_ops.stamp_file_org("../escape.txt", "o1", "Acme")["ok"])


if __name__ == "__main__":
    unittest.main()
