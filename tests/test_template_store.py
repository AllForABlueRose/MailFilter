"""Tests for mailfilter.template_store: a directory of PNG template files.

Each template is one PNG in the store's directory (the same image format the
export/import feature uses); the folder is the storage, so these tests assert on
the files on disk as well as the in-memory index.
"""

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mailfilter import imgcodec, util
from mailfilter.settings_store import DEFAULTS, MAX_LEN
from mailfilter.template_store import (
    MAX_NAME_LEN, MAX_TEMPLATES, TemplateStore, _to_png)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _store():
    return TemplateStore(Path(tempfile.mkdtemp()) / "search_templates")


class SaveAndSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.store = _store()

    def test_empty_snapshot(self):
        self.assertEqual(self.store.snapshot(), {"names": [], "templates": {}})

    def test_save_creates_a_png_file(self):
        self.store.save("Work", {"main": "report"})
        files = list(self.store._dir.glob("*.png"))
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].read_bytes().startswith(_PNG_SIGNATURE))

    def test_save_lists_in_snapshot(self):
        snap = self.store.save("Work", {"main": "report"})
        self.assertEqual(snap["names"], ["Work"])
        self.assertEqual(snap["templates"]["Work"]["main"], "report")

    def test_saved_body_is_coerced_to_known_fields(self):
        snap = self.store.save("T", {"main": "x", "bogus": "drop", "resources": "yes"})
        self.assertEqual(set(snap["templates"]["T"]), set(DEFAULTS))
        self.assertNotIn("bogus", snap["templates"]["T"])
        self.assertIs(snap["templates"]["T"]["resources"], True)

    def test_per_session_fields_not_saved(self):
        # The date range and normalize-width toggle are reset to defaults: a
        # template never carries them (settings_store.TEMPLATE_EXCLUDED_FIELDS).
        snap = self.store.save("T", {
            "main": "x", "start": "2026-01-01T00:00", "end": "2026-02-01T00:00",
            "normalize_width": True,
        })
        body = snap["templates"]["T"]
        self.assertEqual(body["main"], "x")        # ordinary fields still saved
        self.assertEqual(body["start"], "")
        self.assertEqual(body["end"], "")
        self.assertIs(body["normalize_width"], False)

    def test_save_caps_field_length(self):
        snap = self.store.save("T", {"main": "x" * (MAX_LEN * 3)})
        self.assertEqual(len(snap["templates"]["T"]["main"]), MAX_LEN)

    def test_overwrite_same_name_reuses_one_file(self):
        self.store.save("T", {"main": "a"})
        snap = self.store.save("T", {"main": "b"})
        self.assertEqual(snap["names"], ["T"])
        self.assertEqual(snap["templates"]["T"]["main"], "b")
        self.assertEqual(len(list(self.store._dir.glob("*.png"))), 1)

    def test_name_is_trimmed_and_capped(self):
        snap = self.store.save("  " + "n" * (MAX_NAME_LEN * 2) + "  ", {})
        (name,) = snap["names"]
        self.assertEqual(len(name), MAX_NAME_LEN)

    def test_blank_name_rejected(self):
        with self.assertRaises(ValueError):
            self.store.save("   ", {"main": "x"})

    def test_names_that_sanitize_alike_keep_separate_files(self):
        # "a/b" and "a:b" both sanitize toward "a_b"; both must survive as
        # distinct templates (the real name lives in the payload, not the path).
        self.store.save("a/b", {"main": "first"})
        snap = self.store.save("a:b", {"main": "second"})
        self.assertEqual(snap["names"], ["a/b", "a:b"])
        self.assertEqual(len(list(self.store._dir.glob("*.png"))), 2)

    def test_template_limit_enforced_for_new_names(self):
        for i in range(MAX_TEMPLATES):
            self.store.save(f"t{i}", {})
        with self.assertRaises(ValueError):
            self.store.save("one-too-many", {})
        self.assertEqual(self.store.save("t0", {"main": "z"})["templates"]["t0"]["main"], "z")


class GetDeleteExportImportTests(unittest.TestCase):
    def setUp(self):
        self.store = _store()
        self.store.save("A", {"main": "a"})
        self.store.save("B", {"main": "b"})

    def test_get_returns_copy_or_none(self):
        self.assertEqual(self.store.get("A")["main"], "a")
        self.assertIsNone(self.store.get("missing"))
        copy = self.store.get("A")
        copy["main"] = "mutated"
        self.assertEqual(self.store.get("A")["main"], "a")

    def test_delete_removes_template_and_file(self):
        snap = self.store.delete("A")
        self.assertEqual(snap["names"], ["B"])
        self.assertEqual(len(list(self.store._dir.glob("*.png"))), 1)

    def test_delete_unknown_is_a_noop(self):
        self.assertEqual(self.store.delete("missing")["names"], ["A", "B"])

    def test_export_image_returns_png_or_none(self):
        png = self.store.export_image("A")
        self.assertTrue(png.startswith(_PNG_SIGNATURE))
        self.assertIsNone(self.store.export_image("missing"))

    def test_export_then_import_round_trips(self):
        png = self.store.export_image("A")
        self.store.delete("A")
        name, snap = self.store.import_image(png)
        self.assertEqual(name, "A")
        self.assertEqual(snap["templates"]["A"]["main"], "a")

    def test_import_invalid_image_raises(self):
        with self.assertRaises(imgcodec.TemplateImageError):
            self.store.import_image(b"not a png")


class LoadTests(unittest.TestCase):
    def test_load_scans_existing_files(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d) / "search_templates"
            TemplateStore(directory).save("Saved", {"main": "needle", "resources": True})

            reloaded = TemplateStore(directory)
            reloaded.load()
            self.assertEqual(reloaded.snapshot()["names"], ["Saved"])
            self.assertEqual(reloaded.get("Saved")["main"], "needle")
            self.assertIs(reloaded.get("Saved")["resources"], True)

    def test_load_strips_per_session_fields_from_legacy_file(self):
        # A template file written before the exclusion (carrying a date range +
        # normalize_width) is stripped on load, not trusted as-is.
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d) / "search_templates"
            directory.mkdir(parents=True)
            legacy = {"main": "x", "start": "2026-01-01T00:00",
                      "end": "2026-02-01T00:00", "normalize_width": True}
            (directory / "legacy.png").write_bytes(_to_png("Legacy", legacy))

            store = TemplateStore(directory)
            store.load()
            body = store.get("Legacy")
            self.assertEqual(body["main"], "x")
            self.assertEqual(body["start"], "")
            self.assertEqual(body["end"], "")
            self.assertIs(body["normalize_width"], False)

    def test_load_missing_directory_stays_empty(self):
        store = _store()
        store.load()
        self.assertEqual(store.snapshot()["names"], [])

    def test_load_skips_non_template_files(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d) / "search_templates"
            store = TemplateStore(directory)
            store.save("Good", {"main": "ok"})
            # A bogus .png that the codec can't decode must be ignored, not fatal.
            (directory / "junk.png").write_bytes(b"not a real png")

            reloaded = TemplateStore(directory)
            reloaded.load()
            self.assertEqual(reloaded.snapshot()["names"], ["Good"])


class WindowsWriteTests(unittest.TestCase):
    """The template write goes through the shared atomic-write helper.

    It used to call ``os.replace`` directly, so a Windows WinError 5 -- Defender or the
    Search Indexer briefly holding a handle on the freshly written file, or a read-only
    destination -- escaped as a 500 on import. Every other store already wrote through
    ``util.atomic_replace``, which retries; this one didn't.
    """

    def setUp(self):
        self.store = _store()

    def test_a_transient_permission_error_is_retried(self):
        real_replace = os.replace
        calls = []

        def flaky(temp, path):
            calls.append(path)
            if len(calls) == 1:
                raise PermissionError(5, "Access is denied")
            return real_replace(temp, path)

        with mock.patch.object(util.os, "replace", side_effect=flaky):
            self.store.save("PKI", {"main": "x"})

        self.assertEqual(len(calls), 2)                       # failed once, then wrote
        self.assertEqual(self.store.get("PKI")["main"], "x")
        self.assertTrue(self.store._paths["PKI"].exists())

    def test_a_permanently_denied_write_still_raises(self):
        with mock.patch.object(util.os, "replace",
                               side_effect=PermissionError(5, "Access is denied")):
            with self.assertRaises(PermissionError):
                self.store.save("PKI", {"main": "x"})

    def test_a_read_only_destination_is_overwritten(self):
        # POSIX rename ignores the destination's mode, so Windows is simulated: deny
        # the replace while the destination is read-only, allow it once cleared. Retries
        # alone can never win this one -- the attribute has to come off.
        real_replace = os.replace

        def windows_like(temp, path):
            if not os.stat(path).st_mode & stat.S_IWUSR:
                raise PermissionError(5, "Access is denied")
            return real_replace(temp, path)

        self.store.save("PKI", {"main": "first"})
        path = self.store._paths["PKI"]
        path.chmod(stat.S_IREAD)                             # the read-only attribute

        with mock.patch.object(util.os, "replace", side_effect=windows_like):
            self.store.save("PKI", {"main": "second"})

        self.assertEqual(self.store.get("PKI")["main"], "second")
        reloaded = TemplateStore(path.parent)
        reloaded.load()
        self.assertEqual(reloaded.get("PKI")["main"], "second")


if __name__ == "__main__":
    unittest.main()
