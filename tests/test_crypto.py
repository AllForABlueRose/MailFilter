"""Tests for mailfilter.crypto: header dispatch, base64 fallback, DPAPI.

The base64 path is exercised everywhere (DPAPI forced off via monkeypatch); the
real DPAPI round-trip only runs where pywin32/Windows makes it available.
"""

import json
import unittest
from unittest import mock

from mailfilter import crypto


class HeaderDispatchTests(unittest.TestCase):
    def test_legacy_plaintext_is_passed_through(self):
        data = b'[{"id": "x"}]'
        payload, alg = crypto.decode(data)
        self.assertEqual(payload, data)
        self.assertEqual(alg, crypto.ALG_PLAINTEXT)

    def test_unknown_algorithm_raises(self):
        with self.assertRaises(ValueError):
            crypto.decode(crypto.MAGIC + bytes([99]) + b"body")

    def test_alg_name(self):
        self.assertEqual(crypto.alg_name(crypto.ALG_DPAPI), "DPAPI")
        self.assertEqual(crypto.alg_name(crypto.ALG_BASE64), "base64")


class Base64FallbackTests(unittest.TestCase):
    def setUp(self):
        # Force the base64 fallback regardless of the host platform.
        patcher = mock.patch.object(crypto, "is_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_preferred_alg_is_base64(self):
        self.assertEqual(crypto.preferred_alg(), crypto.ALG_BASE64)

    def test_round_trip(self):
        payload = json.dumps([{"id": "A", "subject": "secret-subject"}]).encode("utf-8")
        blob = crypto.encode(payload)
        self.assertTrue(blob.startswith(crypto.MAGIC + bytes([crypto.ALG_BASE64])))
        # Obfuscated: the readable text is not present verbatim on disk.
        self.assertNotIn(b"secret-subject", blob)
        out, alg = crypto.decode(blob)
        self.assertEqual(out, payload)
        self.assertEqual(alg, crypto.ALG_BASE64)


@unittest.skipUnless(crypto.is_available(), "DPAPI requires Windows + pywin32")
class DpapiTests(unittest.TestCase):
    def test_preferred_alg_is_dpapi(self):
        self.assertEqual(crypto.preferred_alg(), crypto.ALG_DPAPI)

    def test_round_trip_is_encrypted(self):
        payload = json.dumps([{"id": "A", "subject": "secret-subject"}]).encode("utf-8")
        blob = crypto.encode(payload)
        self.assertTrue(blob.startswith(crypto.MAGIC + bytes([crypto.ALG_DPAPI])))
        self.assertNotIn(b"secret-subject", blob)
        out, alg = crypto.decode(blob)
        self.assertEqual(out, payload)
        self.assertEqual(alg, crypto.ALG_DPAPI)

    def test_tampered_blob_fails_authentication(self):
        blob = crypto.encode(b"hello")
        tampered = blob[:-1] + bytes([blob[-1] ^ 0xFF])
        with self.assertRaises(Exception):
            crypto.decode(tampered)


if __name__ == "__main__":
    unittest.main()
