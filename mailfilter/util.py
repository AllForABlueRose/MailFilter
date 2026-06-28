"""Small shared helpers."""

import os
import re
from pathlib import Path

# Characters unsafe in a single filename component, collapsed to "_".
# Word characters, dot, hyphen, and space are kept.
_UNSAFE_NAME_RE = re.compile(r"[^\w.\- ]+")


def atomic_write_bytes(path, data):
    """Write ``data`` bytes to ``path`` atomically (temp file then ``os.replace``).

    The same write discipline ``persistence`` uses, exposed for callers that hold
    pre-encoded bytes (e.g. the Key Vault's already-sealed file).
    """
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "wb") as f:
        f.write(data)
    os.replace(temp, path)


def safe_filename(name, fallback):
    """Sanitize a string into one safe filename component.

    Collapses runs of unsafe characters to "_" (spaces, dots, and hyphens are
    kept) and trims leading/trailing separators; returns ``fallback`` if nothing
    usable remains.
    """
    cleaned = _UNSAFE_NAME_RE.sub("_", name or "").strip("_. ")
    return cleaned or fallback
