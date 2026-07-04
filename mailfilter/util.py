"""Small shared helpers."""

import os
import re
import time
from pathlib import Path

import config

# Characters unsafe in a single filename component, collapsed to "_".
# Word characters, dot, hyphen, and space are kept.
_UNSAFE_NAME_RE = re.compile(r"[^\w.\- ]+")


def atomic_replace(temp, path):
    """``os.replace(temp, path)`` with a bounded retry on Windows access errors.

    On Windows the rename can raise ``PermissionError``/WinError 5 when an external
    process (Defender / Search Indexer) momentarily holds a handle to a freshly
    written file. Retry a few times before re-raising so a transient lock does not
    fail an otherwise-valid write. Shared by :func:`atomic_write_bytes` and
    ``persistence.save_encoded`` so every encoded-at-rest write is covered.
    """
    for attempt in range(config.FILE_REPLACE_RETRIES):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == config.FILE_REPLACE_RETRIES - 1:
                raise
            time.sleep(config.FILE_REPLACE_DELAY_SECONDS)


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


def safe_filename(name, fallback):
    """Sanitize a string into one safe filename component.

    Collapses runs of unsafe characters to "_" (spaces, dots, and hyphens are
    kept) and trims leading/trailing separators; returns ``fallback`` if nothing
    usable remains.
    """
    cleaned = _UNSAFE_NAME_RE.sub("_", name or "").strip("_. ")
    return cleaned or fallback
