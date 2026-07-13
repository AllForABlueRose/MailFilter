"""Small shared helpers."""

import os
import re
import stat
import time
from pathlib import Path

import config

# Characters unsafe in a single filename component, collapsed to "_".
# Word characters, dot, hyphen, and space are kept.
_UNSAFE_NAME_RE = re.compile(r"[^\w.\- ]+")


def atomic_replace(temp, path):
    """``os.replace(temp, path)`` with a bounded retry on Windows access errors.

    On Windows the rename can raise ``PermissionError``/WinError 5 for two different
    reasons, and both are handled here:

    * an external process (Defender / Search Indexer) momentarily holds a handle to a
      freshly written file -- transient, so the rename is retried a few times before
      giving up;
    * the *destination* carries the read-only attribute -- permanent, and no amount of
      retrying defeats it, so the attribute is cleared before each attempt.

    Shared by :func:`atomic_write_bytes` and ``persistence.save_encoded`` so every
    encoded-at-rest write is covered.
    """
    for attempt in range(config.FILE_REPLACE_RETRIES):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == config.FILE_REPLACE_RETRIES - 1:
                raise
            _clear_read_only(path)
            time.sleep(config.FILE_REPLACE_DELAY_SECONDS)


def _clear_read_only(path):
    """Best-effort: drop the read-only attribute from an existing ``path``."""
    try:
        os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
    except OSError:
        pass


def atomic_write_bytes(path, data):
    """Write ``data`` bytes to ``path`` atomically (temp file then ``os.replace``).

    The same write discipline ``persistence`` uses, exposed for callers that hold
    pre-encoded bytes (e.g. the Key Vault's already-sealed file).
    """
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "wb") as f:
        f.write(data)
    atomic_replace(temp, path)


def domain_of(email):
    """The lowercased domain of an SMTP address, or "" if there isn't one.

    Blanks and legacy Exchange X.500 DNs (``/O=...``) have no domain to key on, so
    they yield "". The single definition of "which domain is this address on",
    shared by ``customers`` (who is internal) and ``mailbox_store`` (whether a newly
    proved mailbox changes the saved internal domain).
    """
    email = (email or "").strip().lower()
    if not email or email.startswith("/") or "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1]


def safe_filename(name, fallback):
    """Sanitize a string into one safe filename component.

    Collapses runs of unsafe characters to "_" (spaces, dots, and hyphens are
    kept) and trims leading/trailing separators; returns ``fallback`` if nothing
    usable remains.
    """
    cleaned = _UNSAFE_NAME_RE.sub("_", name or "").strip("_. ")
    return cleaned or fallback
