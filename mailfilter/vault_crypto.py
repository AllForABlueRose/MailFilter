"""Authenticated encryption for the Key Vault — the app's strongest at-rest seam.

Unlike ``crypto.py`` (DPAPI/base64, which only stops *other* OS users and is
transparent to your own account), the vault is sealed with **AES-256-GCM** under
a key derived from a user **master passphrase** via **scrypt**. The sealed file is
therefore useless without the passphrase even to the same logged-in user — that
is what makes it "more secure than the caches".

Sealed-file layout::

    MAGIC (4) | ver (1) | scrypt salt (16) | GCM nonce (12) | ciphertext+tag
    b"MFV1"     1                                              (AES-256-GCM)

The salt is stored in the file so unlock re-derives the same key from the
passphrase; GCM's tag authenticates the ciphertext (a wrong passphrase or any
tampering fails the open). ``MAGIC|ver`` is fed as GCM associated data so the
header cannot be swapped either.

Dependency direction: ``vault_crypto -> crypto`` (only for the optional DPAPI
key-wrap) ``+ config``. ``cryptography`` and ``win32crypt`` are imported lazily,
so the package still imports on a box without them (the vault is simply
unusable / DPAPI-assist simply unavailable there).
"""

import hashlib
import os

import config

from . import crypto

MAGIC = b"MFV1"
VERSION = 1
_SALT_LEN = 16
_NONCE_LEN = 12

# Binds a DPAPI-wrapped vault key to this app (see crypto.dpapi_protect). Not a
# secret — only stops a bare CryptUnprotectData by another program as the user.
_DPAPI_ENTROPY = b"MailFilter/vault_key/v1"


class VaultCipherUnavailable(RuntimeError):
    """The ``cryptography`` package is not installed, so the vault cannot be used."""


class VaultAuthError(RuntimeError):
    """Decryption failed — wrong passphrase, wrong key, or a tampered file."""


def _aesgcm(key):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:  # pragma: no cover - exercised only without the dep
        raise VaultCipherUnavailable("cryptography not installed") from e
    return AESGCM(key)


def is_available():
    """True if the vault cipher (``cryptography``) can be used on this machine."""
    try:
        _aesgcm(b"\0" * config.VAULT_KEY_LEN)
        return True
    except VaultCipherUnavailable:
        return False


def new_salt():
    return os.urandom(_SALT_LEN)


def derive_key(passphrase, salt):
    """Derive the 32-byte vault key from ``passphrase`` (str) and ``salt`` bytes.

    scrypt is memory-hard; ``maxmem`` is passed explicitly so the cost parameters
    in ``config`` are not clamped by the library default.
    """
    n, r, p = config.VAULT_SCRYPT_N, config.VAULT_SCRYPT_R, config.VAULT_SCRYPT_P
    return hashlib.scrypt(
        (passphrase or "").encode("utf-8"),
        salt=salt,
        n=n, r=r, p=p,
        dklen=config.VAULT_KEY_LEN,
        maxmem=132 * n * r,
    )


def _aad():
    return MAGIC + bytes([VERSION])


def seal(plaintext, key, salt):
    """Encrypt ``plaintext`` bytes into the on-disk sealed-file bytes under ``key``.

    ``salt`` (the one used to derive ``key``) is embedded so unlock can re-derive.
    """
    nonce = os.urandom(_NONCE_LEN)
    ct = _aesgcm(key).encrypt(nonce, plaintext, _aad())
    return MAGIC + bytes([VERSION]) + salt + nonce + ct


def salt_of(data):
    """Read the scrypt salt out of a sealed file (needed before key derivation)."""
    _check_header(data)
    return data[5:5 + _SALT_LEN]


def open_sealed(data, key):
    """Decrypt sealed-file ``data`` with ``key``; return the plaintext bytes.

    Raises :class:`VaultAuthError` on a wrong key / tampered file, so callers can
    treat a bad passphrase and corruption identically (no oracle).
    """
    _check_header(data)
    off = 5 + _SALT_LEN
    nonce = data[off:off + _NONCE_LEN]
    ct = data[off + _NONCE_LEN:]
    try:
        return _aesgcm(key).decrypt(nonce, ct, _aad())
    except VaultCipherUnavailable:
        raise
    except Exception as e:  # InvalidTag and friends -> uniform auth failure
        raise VaultAuthError("vault decryption failed") from e


def _check_header(data):
    if not data[:4] == MAGIC:
        raise VaultAuthError("not a vault file (bad magic)")
    if len(data) < 5 + _SALT_LEN + _NONCE_LEN:
        raise VaultAuthError("vault file truncated")
    if data[4] != VERSION:
        raise VaultAuthError(f"unsupported vault version {data[4]}")


# ----- optional DPAPI "remember on this machine" key-wrap -----

def dpapi_available():
    return crypto.is_available()


def wrap_key_dpapi(key):
    """DPAPI-wrap the derived ``key`` for the opt-in remember-on-machine unlock."""
    return crypto.dpapi_protect(key, _DPAPI_ENTROPY)


def unwrap_key_dpapi(blob):
    """Reverse :func:`wrap_key_dpapi`; raises if foreign/tampered."""
    return crypto.dpapi_unprotect(blob, _DPAPI_ENTROPY)
