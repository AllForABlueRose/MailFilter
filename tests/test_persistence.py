"""Tests for mailfilter.persistence: encoded-JSON file load/save helpers."""

import tempfile
import unittest
from pathlib import Path

from mailfilter import crypto, persistence


class PersistenceTests(unittest.TestCase):
    def test_round_trip_reports_alg(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.json"
            persistence.save_encoded(path, {"a": 1})
            self.assertTrue(path.read_bytes().startswith(crypto.MAGIC))
            obj, alg = persistence.load_encoded(path)
            self.assertEqual(obj, {"a": 1})
            self.assertEqual(alg, crypto.preferred_alg())

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(persistence.load_encoded(Path(d) / "nope.json"), (None, None))

    def test_corrupt_file_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.json"
            path.write_bytes(b"garbage")
            self.assertEqual(persistence.load_encoded(path), (None, None))

    def test_legacy_plaintext_reports_plaintext_alg(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "x.json"
            path.write_text('{"a": 1}')  # headerless legacy file
            obj, alg = persistence.load_encoded(path)
            self.assertEqual(obj, {"a": 1})
            self.assertEqual(alg, crypto.ALG_PLAINTEXT)


if __name__ == "__main__":
    unittest.main()
