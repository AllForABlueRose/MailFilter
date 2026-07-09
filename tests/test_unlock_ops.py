"""Tests for mailfilter.unlock_ops: listing today's workspace files, encryption
detection, and the unzip/decrypt engine. WORKSPACE_DIR is redirected to a temp
folder. The zip-with-key and Excel-decrypt paths need optional packages and are
skipped when those aren't importable; the keyless zip path uses only the stdlib."""

import importlib.util
import os
import shutil
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

import config
from mailfilter import unlock_ops, workspace_manifest

ORG = {"org_id": "o1", "org_name": "Acme Corp", "mail_id": "m1"}


def _have(mod):
    return importlib.util.find_spec(mod) is not None


def _zip_assignment(secret=None, key_kind=None):
    return {"secret": secret, "org_id": "o1", "org_name": "Acme Corp",
            "key_kind": key_kind, "file_kind": "zip"}


class UnlockOpsBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = config.WORKSPACE_DIR
        config.WORKSPACE_DIR = Path(self._tmp) / "workspace"
        self.folder = unlock_ops.today_folder()
        self.folder.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        config.WORKSPACE_DIR = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _record(self, name, meta=ORG):
        workspace_manifest.record(str(self.folder), name, meta)


class ListWorkspaceFilesTests(UnlockOpsBase):
    def test_missing_folder_reports_not_exists(self):
        shutil.rmtree(self.folder)
        out = unlock_ops.list_workspace_files()
        self.assertFalse(out["exists"])
        self.assertEqual(out["files"], [])

    def test_lists_kind_source_and_org(self):
        with zipfile.ZipFile(self.folder / "pack.zip", "w") as z:
            z.writestr("inner.txt", b"hi")
        self._record("pack.zip")
        (self.folder / "notes.txt").write_text("user dropped this")
        out = unlock_ops.list_workspace_files()
        self.assertTrue(out["exists"])
        by = {f["name"]: f for f in out["files"]}
        self.assertNotIn(config.WORKSPACE_MANIFEST_NAME, by)   # manifest not a file
        self.assertEqual(by["pack.zip"]["kind"], "zip")
        self.assertEqual(by["pack.zip"]["source"], "download")
        self.assertEqual(by["pack.zip"]["org_name"], "Acme Corp")
        self.assertFalse(by["pack.zip"]["encrypted"])
        self.assertEqual(by["notes.txt"]["source"], "external")  # not in manifest
        self.assertEqual(by["notes.txt"]["org_id"], "")
        self.assertEqual(by["notes.txt"]["kind"], "other")


class ZipUnlockTests(UnlockOpsBase):
    def test_keyless_zip_unzips_tags_moves_and_cleans(self):
        with zipfile.ZipFile(self.folder / "pack.zip", "w") as z:
            z.writestr("inner.txt", b"hello world")
        self._record("pack.zip")
        res = unlock_ops.unlock_files({"pack.zip": _zip_assignment()})
        self.assertEqual(res["errors"], [])
        self.assertEqual(len(res["unlocked"]), 1)
        # Archive gone, extracted file renamed with the org and moved up, tagged.
        self.assertFalse((self.folder / "pack.zip").exists())
        out = self.folder / "inner_Acme Corp.txt"
        self.assertTrue(out.exists())
        self.assertEqual(out.read_bytes(), b"hello world")
        self.assertIsNone(workspace_manifest.lookup(str(self.folder), "pack.zip"))
        self.assertEqual(workspace_manifest.lookup(str(self.folder), out.name)["org_id"], "o1")

    def test_per_file_failure_isolated(self):
        (self.folder / "bad.zip").write_bytes(b"this is not a zip")
        with zipfile.ZipFile(self.folder / "good.zip", "w") as z:
            z.writestr("g.txt", b"ok")
        self._record("bad.zip"); self._record("good.zip")
        res = unlock_ops.unlock_files({
            "bad.zip": _zip_assignment(), "good.zip": _zip_assignment()})
        names_ok = {u["name"] for u in res["unlocked"]}
        names_err = {e["name"] for e in res["errors"]}
        self.assertEqual(names_ok, {"good.zip"})
        self.assertEqual(names_err, {"bad.zip"})
        self.assertTrue((self.folder / "g_Acme Corp.txt").exists())

    @unittest.skipUnless(_have("pyzipper"), "pyzipper not installed")
    def test_aes_zip_with_key(self):
        import pyzipper
        with pyzipper.AESZipFile(str(self.folder / "sec.zip"), "w",
                                 encryption=pyzipper.WZ_AES) as z:
            z.setpassword(b"topsecret")
            z.writestr("secret.txt", b"classified")
        self._record("sec.zip")
        res = unlock_ops.unlock_files({
            "sec.zip": _zip_assignment(secret="topsecret", key_kind="managed")})
        self.assertEqual(res["errors"], [])
        out = self.folder / "secret_Acme Corp.txt"
        self.assertTrue(out.exists())
        self.assertEqual(out.read_bytes(), b"classified")

    @unittest.skipUnless(_have("pyzipper"), "pyzipper not installed")
    def test_aes_zip_wrong_key_errors_and_keeps_archive(self):
        import pyzipper
        with pyzipper.AESZipFile(str(self.folder / "sec.zip"), "w",
                                 encryption=pyzipper.WZ_AES) as z:
            z.setpassword(b"topsecret")
            z.writestr("secret.txt", b"classified")
        self._record("sec.zip")
        res = unlock_ops.unlock_files({
            "sec.zip": _zip_assignment(secret="WRONG", key_kind="managed")})
        self.assertEqual(len(res["errors"]), 1)
        self.assertTrue((self.folder / "sec.zip").exists())   # untouched on failure


class EncryptionDetectionTests(UnlockOpsBase):
    def test_plain_zip_not_encrypted(self):
        with zipfile.ZipFile(self.folder / "p.zip", "w") as z:
            z.writestr("a.txt", b"x")
        self.assertFalse(unlock_ops._is_encrypted(self.folder / "p.zip", "zip"))

    @unittest.skipUnless(_have("pyzipper"), "pyzipper not installed")
    def test_aes_zip_detected_encrypted(self):
        import pyzipper
        with pyzipper.AESZipFile(str(self.folder / "e.zip"), "w",
                                 encryption=pyzipper.WZ_AES) as z:
            z.setpassword(b"pw"); z.writestr("a.txt", b"x")
        self.assertTrue(unlock_ops._is_encrypted(self.folder / "e.zip", "zip"))

    @unittest.skipIf(_have("msoffcrypto"), "covers the no-msoffcrypto magic sniff")
    def test_ole2_magic_sniff_when_no_msoffcrypto(self):
        (self.folder / "enc.xlsx").write_bytes(unlock_ops._OLE2_MAGIC + b"\x00" * 16)
        self.assertTrue(unlock_ops._is_encrypted(self.folder / "enc.xlsx", "excel"))

    @unittest.skipUnless(_have("openpyxl"), "openpyxl not installed")
    def test_plain_xlsx_not_encrypted(self):
        import openpyxl
        openpyxl.Workbook().save(self.folder / "plain.xlsx")
        self.assertFalse(unlock_ops._is_encrypted(self.folder / "plain.xlsx", "excel"))


def _make_encrypted_xlsx(dst, password):
    """Build a password-protected .xlsx fixture, or raise to skip the test."""
    import io
    import openpyxl
    import msoffcrypto
    plain = io.BytesIO()
    wb = openpyxl.Workbook(); wb.active["A1"] = "secret data"; wb.save(plain); plain.seek(0)
    office = msoffcrypto.OfficeFile(plain)
    with open(dst, "wb") as out:
        office.encrypt(password, out)   # AttributeError on libs without encrypt -> skip


@unittest.skipUnless(_have("msoffcrypto") and _have("openpyxl"),
                     "msoffcrypto/openpyxl not installed")
class ExcelUnlockTests(UnlockOpsBase):
    def _encrypted(self, name, password):
        try:
            _make_encrypted_xlsx(self.folder / name, password)
        except Exception as e:
            self.skipTest(f"cannot build encrypted xlsx fixture: {e}")
        self._record(name)

    def test_excel_decrypts_in_place(self):
        self._encrypted("book.xlsx", "pw")
        self.assertTrue(unlock_ops._is_encrypted(self.folder / "book.xlsx", "excel"))
        res = unlock_ops.unlock_files({"book.xlsx": {
            "secret": "pw", "org_id": "o1", "org_name": "Acme Corp",
            "key_kind": "temporary", "file_kind": "excel"}})
        self.assertEqual(res["errors"], [])
        # Same name, no stem suffix, and no longer encrypted.
        self.assertTrue((self.folder / "book.xlsx").exists())
        self.assertFalse(unlock_ops._is_encrypted(self.folder / "book.xlsx", "excel"))
        self.assertEqual(workspace_manifest.lookup(str(self.folder), "book.xlsx")["org_id"], "o1")

    def test_excel_wrong_key_errors_and_keeps_original(self):
        self._encrypted("book.xlsx", "pw")
        original = (self.folder / "book.xlsx").read_bytes()
        res = unlock_ops.unlock_files({"book.xlsx": {
            "secret": "WRONG", "org_id": "o1", "org_name": "Acme Corp",
            "key_kind": "temporary", "file_kind": "excel"}})
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual((self.folder / "book.xlsx").read_bytes(), original)


class ZipNameDecodingTests(UnlockOpsBase):
    """Japanese entry names come out correctly instead of as 文字化け mojibake, for
    both flag states, while clean ASCII/UTF-8/accented-Latin names are preserved."""

    class _Info:
        def __init__(self, filename, flag_bits):
            self.filename = filename
            self.flag_bits = flag_bits

    def test_legacy_shift_jis_name_recovered(self):
        # zipfile mis-decodes cp932 bytes as CP437 when bit 0x800 is clear.
        raw = "見積書.txt".encode("cp932")
        info = self._Info(raw.decode("cp437"), 0)
        self.assertEqual(unlock_ops._decode_zip_name(info), "見積書.txt")

    def test_utf8_bytes_without_flag_recovered(self):
        # The regression the aggressive decoder fixes: UTF-8 bytes stored WITHOUT the
        # UTF-8 flag. The old code forced cp932 and produced mojibake; now UTF-8 wins.
        raw = "契約書.pdf".encode("utf-8")
        info = self._Info(raw.decode("cp437"), 0)
        self.assertEqual(unlock_ops._decode_zip_name(info), "契約書.pdf")

    def test_utf8_flagged_name_unchanged(self):
        info = self._Info("日本語.txt", 0x800)
        self.assertEqual(unlock_ops._decode_zip_name(info), "日本語.txt")

    def test_ascii_name_unchanged(self):
        info = self._Info("invoice_2026.pdf", 0)
        self.assertEqual(unlock_ops._decode_zip_name(info), "invoice_2026.pdf")

    def test_accented_latin_utf8_preserved(self):
        # A genuine accented-Latin UTF-8 name must NOT be turned into half-width kana
        # by a coincidental cp932 decode — the scorer penalises that misread.
        raw = "café_Zürich.pdf".encode("utf-8")
        info = self._Info(raw.decode("cp437"), 0)
        self.assertEqual(unlock_ops._decode_zip_name(info), "café_Zürich.pdf")

    def test_mojibake_score_ranks_japanese_clean(self):
        clean = unlock_ops._mojibake_score("見積書.txt")
        garbled = unlock_ops._mojibake_score("è¦\x8bç©\x8dæ\x9b¸.txt")
        self.assertEqual(clean, 0)
        self.assertGreater(garbled, clean)

    def test_extract_members_writes_japanese_name(self):
        src_zip = self.folder / "jp.zip"
        with zipfile.ZipFile(src_zip, "w") as z:
            z.writestr("placeholder.txt", b"data")
        extract_dir = self.folder / "out"
        extract_dir.mkdir()
        # Simulate a legacy archive: rewrite the in-memory entry as CP437-mis-decoded
        # Shift-JIS with the UTF-8 flag cleared (orig_filename is left intact so the
        # local-header content read still succeeds).
        legacy = "見積書.txt".encode("cp932").decode("cp437")
        with zipfile.ZipFile(src_zip) as z:
            for info in z.infolist():
                info.filename = legacy
                info.flag_bits &= ~0x800
            unlock_ops._extract_members(z, extract_dir)
        self.assertEqual([p.name for p in extract_dir.iterdir()], ["見積書.txt"])

    def test_safe_extract_dest_rejects_zip_slip(self):
        with self.assertRaises(ValueError):
            unlock_ops._safe_extract_dest(self.folder, "../escape.txt")


class DatetimeInheritanceTests(UnlockOpsBase):
    """Unlock outputs inherit the originating mail's datetime (manifest ``received``),
    falling back to the source file's mtime for files with no mail origin."""

    def test_zip_output_inherits_manifest_received(self):
        with zipfile.ZipFile(self.folder / "pack.zip", "w") as z:
            z.writestr("inner.txt", b"hi")
        self._record("pack.zip", {"org_id": "o1", "org_name": "Acme Corp",
                                  "mail_id": "m1", "received": "2026-05-05 08:00:00"})
        unlock_ops.unlock_files({"pack.zip": _zip_assignment()})
        out = self.folder / "inner_Acme Corp.txt"
        self.assertTrue(out.exists())
        expected = datetime.strptime("2026-05-05 08:00:00", config.RECEIVED_FORMAT).timestamp()
        self.assertEqual(out.stat().st_mtime, expected)

    def test_external_zip_output_inherits_source_mtime(self):
        zpath = self.folder / "ext.zip"
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("inner.txt", b"hi")
        # No manifest entry -> external file, so outputs inherit the archive's mtime.
        src_ts = datetime.strptime("2020-01-02 03:04:00", config.RECEIVED_FORMAT).timestamp()
        os.utime(zpath, (src_ts, src_ts))
        unlock_ops.unlock_files({"ext.zip": {
            "secret": None, "org_id": "", "org_name": "",
            "key_kind": None, "file_kind": "zip"}})
        out = self.folder / "inner.txt"
        self.assertTrue(out.exists())
        self.assertEqual(out.stat().st_mtime, src_ts)


if __name__ == "__main__":
    unittest.main()
