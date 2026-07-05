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


class SenderOrgMatchesTests(unittest.TestCase):
    def setUp(self):
        self.store = _FakeStore([
            make_mail(id="m1", sender_email="bob@acme.com"),
            make_mail(id="m2", sender_email="eve@nobody.com"),
            make_mail(id="m3", sender_email="sue@acme.com"),
        ])
        self.wanted = {"m1", "m2", "m3"}

    def test_representative_preferred_over_member(self):
        # acme.com is a member of Base Inc but represents Acme Corp — rep wins.
        orgs = [
            {"id": "1", "name": "Base Inc",
             "domains": [{"domain": "acme.com", "role": "member"}], "contacts": []},
            {"id": "2", "name": "Acme Corp",
             "domains": [{"domain": "acme.com", "role": "representative"}], "contacts": []},
        ]
        out = workspace_ops._sender_org_matches(self.store, self.wanted, orgs)
        self.assertEqual(out.get("m1"), {"org_id": "2", "org_name": "Acme Corp"})
        self.assertEqual(out.get("m3"), {"org_id": "2", "org_name": "Acme Corp"})

    def test_member_used_when_no_representative(self):
        orgs = [{"id": "1", "name": "Acme Corp",
                 "domains": [{"domain": "acme.com", "role": "member"}], "contacts": []}]
        self.assertEqual(workspace_ops._sender_org_matches(self.store, self.wanted, orgs).get("m1"),
                         {"org_id": "1", "org_name": "Acme Corp"})

    def test_unresolved_sender_absent(self):
        orgs = [{"id": "1", "name": "Acme Corp",
                 "domains": [{"domain": "acme.com", "role": "member"}], "contacts": []}]
        out = workspace_ops._sender_org_matches(self.store, self.wanted, orgs)
        self.assertNotIn("m2", out)  # eve@nobody.com belongs to no org


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


class BruteForceOrgMatchesTests(unittest.TestCase):
    """The Brute Force Resolve keyword->org content matcher (shared resolver)."""

    def setUp(self):
        self.store = _FakeStore([
            make_mail(id="m1", subject="Re: Globex Industries account", body="hi"),
            make_mail(id="m2", subject="ticket", body="mentions globex industries here"),
            make_mail(id="m3", subject="ticket", body="nothing relevant"),
        ])
        self.wanted = {"m1", "m2", "m3"}
        self.orgs = [{"id": "o-glo", "name": "Globex Inc"}]
        self.mappings = [{"keyword": "Globex Industries", "org_id": "o-glo"}]

    def test_matches_in_subject_or_body_case_insensitively(self):
        out = workspace_ops._brute_force_org_matches(
            self.store, self.wanted, self.mappings, self.orgs)
        self.assertEqual(out.get("m1"), {"org_id": "o-glo", "org_name": "Globex Inc"})  # subject
        self.assertEqual(out.get("m2"), {"org_id": "o-glo", "org_name": "Globex Inc"})  # body
        self.assertNotIn("m3", out)                                                     # no match

    def test_first_keyword_in_list_order_wins(self):
        m = _FakeStore([make_mail(id="x", body="Acme and Globex both appear")])
        orgs = [{"id": "a", "name": "Acme Org"}, {"id": "g", "name": "Globex Org"}]
        mappings = [{"keyword": "Globex", "org_id": "g"}, {"keyword": "Acme", "org_id": "a"}]
        out = workspace_ops._brute_force_org_matches(m, {"x"}, mappings, orgs)
        self.assertEqual(out["x"], {"org_id": "g", "org_name": "Globex Org"})

    def test_keyword_mapped_to_missing_org_is_absent(self):
        mappings = [{"keyword": "Globex Industries", "org_id": "deleted"}]
        out = workspace_ops._brute_force_org_matches(
            self.store, self.wanted, mappings, self.orgs)
        self.assertEqual(out, {})

    def test_empty_mappings_matches_nothing(self):
        self.assertEqual(
            workspace_ops._brute_force_org_matches(self.store, self.wanted, [], self.orgs), {})


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

    def test_resolve_works_on_its_own(self):
        _folder, saved, _errors = self._save(
            resolve_customer=True, customer_mappings=self.mappings, orgs=self.orgs)
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
        self.assertEqual(workspace_manifest.lookup(folder, by_id["m1"]),
                         {"org_id": "2", "org_name": "Globex Inc", "mail_id": "m1"})
        self.assertEqual(workspace_manifest.lookup(folder, by_id["m2"]),
                         {"org_id": "", "org_name": "", "mail_id": "m2"})


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


if __name__ == "__main__":
    unittest.main()
