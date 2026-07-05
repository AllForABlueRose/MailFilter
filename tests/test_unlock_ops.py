"""Tests for mailfilter.unlock_ops: listing today's workspace files, encryption
detection, and the unzip/decrypt engine. WORKSPACE_DIR is redirected to a temp
folder. The zip-with-key and Excel-decrypt paths need optional packages and are
skipped when those aren't importable; the keyless zip path uses only the stdlib."""

import importlib.util
import shutil
import tempfile
import unittest
import zipfile
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


if __name__ == "__main__":
    unittest.main()
