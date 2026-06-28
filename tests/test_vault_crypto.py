"""Tests for mailfilter.vault_crypto: scrypt KDF + AES-GCM seal/open."""

import unittest

from mailfilter import vault_crypto


@unittest.skipUnless(vault_crypto.is_available(), "cryptography not installed")
class SealTests(unittest.TestCase):
    def test_seal_open_round_trip(self):
        salt = vault_crypto.new_salt()
        key = vault_crypto.derive_key("correct horse", salt)
        sealed = vault_crypto.seal(b'{"secret":"hunter2"}', key, salt)
        self.assertTrue(sealed.startswith(vault_crypto.MAGIC))
        self.assertNotIn(b"hunter2", sealed)  # never plaintext on disk
        self.assertEqual(vault_crypto.open_sealed(sealed, key), b'{"secret":"hunter2"}')

    def test_salt_is_embedded_and_recoverable(self):
        salt = vault_crypto.new_salt()
        key = vault_crypto.derive_key("pw", salt)
        sealed = vault_crypto.seal(b"x", key, salt)
        self.assertEqual(vault_crypto.salt_of(sealed), salt)

    def test_derive_key_is_deterministic_per_salt(self):
        salt = vault_crypto.new_salt()
        self.assertEqual(vault_crypto.derive_key("pw", salt), vault_crypto.derive_key("pw", salt))
        # A different salt (or passphrase) yields a different key.
        self.assertNotEqual(vault_crypto.derive_key("pw", salt),
                            vault_crypto.derive_key("pw", vault_crypto.new_salt()))
        self.assertNotEqual(vault_crypto.derive_key("pw", salt),
                            vault_crypto.derive_key("PW", salt))

    def test_wrong_passphrase_fails_auth(self):
        salt = vault_crypto.new_salt()
        sealed = vault_crypto.seal(b"data", vault_crypto.derive_key("right", salt), salt)
        wrong = vault_crypto.derive_key("wrong", salt)
        with self.assertRaises(vault_crypto.VaultAuthError):
            vault_crypto.open_sealed(sealed, wrong)

    def test_tampering_is_detected(self):
        salt = vault_crypto.new_salt()
        key = vault_crypto.derive_key("pw", salt)
        sealed = bytearray(vault_crypto.seal(b"data", key, salt))
        sealed[-1] ^= 0x01  # flip a ciphertext/tag bit
        with self.assertRaises(vault_crypto.VaultAuthError):
            vault_crypto.open_sealed(bytes(sealed), key)

    def test_bad_magic_rejected(self):
        with self.assertRaises(vault_crypto.VaultAuthError):
            vault_crypto.open_sealed(b"XXXX\x01" + b"\0" * 40, b"\0" * 32)


if __name__ == "__main__":
    unittest.main()
