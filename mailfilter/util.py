"""Small shared helpers."""

import re

# Characters unsafe in a single filename component, collapsed to "_".
# Word characters, dot, hyphen, and space are kept.
_UNSAFE_NAME_RE = re.compile(r"[^\w.\- ]+")


def safe_filename(name, fallback):
    """Sanitize a string into one safe filename component.

    Collapses runs of unsafe characters to "_" (spaces, dots, and hyphens are
    kept) and trims leading/trailing separators; returns ``fallback`` if nothing
    usable remains.
    """
    cleaned = _UNSAFE_NAME_RE.sub("_", name or "").strip("_. ")
    return cleaned or fallback
