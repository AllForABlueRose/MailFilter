"""On-disk protection for the mail cache.

Two encodings, chosen automatically by :func:`encode` (strongest first):

* **DPAPI** (``win32crypt``, user-scoped) — real encryption on Windows. The key
  is derived from the user's login credentials and managed by the OS, so another
  user account cannot decrypt the file and a copy moved to another machine is
  useless.
* **base64** — an obfuscation fallback used when DPAPI is unavailable (a
  non-Windows dev box, pywin32 not installed). It is trivially reversible and
  only keeps the cache from being read at a casual glance / grep / file preview,
  but it ensures the cache is never written as bare plaintext JSON.

Like ``outlook.py``, ``win32crypt`` is imported lazily so the package imports and
runs anywhere; without it the cache simply falls back to base64.

On-disk layout::

    MAGIC (4 bytes) | alg (1 byte) | body
    b"MFC1"           1 = DPAPI       DPAPI blob
                      2 = base64      base64(JSON bytes)

A file with no ``MAGIC`` prefix is a legacy plaintext JSON cache; :func:`decode`
returns it unchanged and reports :data:`ALG_PLAINTEXT` so the store can migrate
it to a stronger format on load.
"""

import base64
import logging

log = logging.getLogger(__name__)

MAGIC = b"MFC1"

ALG_PLAINTEXT = 0  # legacy: no header, raw JSON on disk
ALG_DPAPI = 1
ALG_BASE64 = 2

_ALG_NAMES = {ALG_PLAINTEXT: "plaintext", ALG_DPAPI: "DPAPI", ALG_BASE64: "base64"}

# Secondary entropy mixed into DPAPI's key derivation: binds a blob to this app
# so another program running as the same user can't decrypt it with a bare
# CryptUnprotectData call. This is not a secret key — it adds no protection
# against an attacker who also has the source.
_ENTROPY = b"MailFilter/mail_cache/v1"

# CRYPTPROTECT_UI_FORBIDDEN: never surface a credential prompt — a background
# refresh thread must fail rather than block on UI.
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class CacheCipherUnavailable(RuntimeError):
    """DPAPI cannot be used here (expected on non-Windows dev boxes)."""


def _win32crypt():
    try:
        import win32crypt
    except ImportError as e:
        raise CacheCipherUnavailable("win32crypt not installed") from e
    return win32crypt


def is_available():
    """True if DPAPI encryption can be used on this machine."""
    try:
        _win32crypt()
        return True
    except CacheCipherUnavailable:
        return False


def preferred_alg():
    """The id of the strongest encoding available right now."""
    return ALG_DPAPI if is_available() else ALG_BASE64


def alg_name(alg):
    return _ALG_NAMES.get(alg, f"alg-{alg}")


def encode(payload):
    """Encode JSON ``bytes`` for disk using the strongest available scheme."""
    if is_available():
        try:
            return _header(ALG_DPAPI) + _dpapi_protect(payload)
        except CacheCipherUnavailable:
            log.warning("DPAPI unavailable at save time; falling back to base64")
    return _header(ALG_BASE64) + base64.b64encode(payload)


def decode(data):
    """Decode on-disk ``bytes`` back to JSON ``bytes``.

    Returns ``(json_bytes, alg)``. ``alg`` is :data:`ALG_PLAINTEXT` for a legacy
    headerless file. Raises on a tampered/unreadable blob or an unknown
    algorithm id.
    """
    if not data.startswith(MAGIC):
        return data, ALG_PLAINTEXT
    alg = data[len(MAGIC)]
    body = data[len(MAGIC) + 1:]
    if alg == ALG_DPAPI:
        return _dpapi_unprotect(body), ALG_DPAPI
    if alg == ALG_BASE64:
        return base64.b64decode(body), ALG_BASE64
    raise ValueError(f"unknown cache algorithm id {alg}")


def _header(alg):
    return MAGIC + bytes([alg])


def _dpapi_protect(payload):
    win32crypt = _win32crypt()
    blob = win32crypt.CryptProtectData(
        payload,
        "MailFilter cache",  # cosmetic description stored alongside the blob
        _ENTROPY,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
    )
    return bytes(blob)


def _dpapi_unprotect(body):
    win32crypt = _win32crypt()
    _description, data = win32crypt.CryptUnprotectData(
        body, _ENTROPY, None, None, _CRYPTPROTECT_UI_FORBIDDEN
    )
    return bytes(data)
