"""Persistence for the Suspected Customers List.

Backs the experimental "Resolve Customer Name To Downloads" feature: a flat list
of customer names the user wants looked for in mail content at download time. A
single JSON object ``{"customers": [...]}`` guarded by an ``RLock``, written
atomically and encoded at rest through the same ``crypto`` seam as the other
stores. The matching itself (does a name appear in a mail's content?) lives in
``workspace_ops``; this store only persists and sanitizes the list.
"""

import logging
import threading
from pathlib import Path

from config import CUSTOMER_MATCH_MAX_NAMES, CUSTOMER_MATCH_NAME_MAX

from . import persistence

log = logging.getLogger(__name__)


def coerce(raw):
    """Return a cleaned list of customer names from ``raw``.

    Accepts a ``{"customers": [...]}`` dict, a list/tuple, or a newline-separated
    string. Each name is trimmed and length-capped (:data:`CUSTOMER_MATCH_NAME_MAX`);
    blanks and case-insensitive duplicates are dropped; the list is capped at
    :data:`CUSTOMER_MATCH_MAX_NAMES` (first-wins).
    """
    if isinstance(raw, dict):
        raw = raw.get("customers")
    if isinstance(raw, str):
        raw = raw.splitlines()
    if not isinstance(raw, (list, tuple)):
        return []
    out, seen = [], set()
    for item in raw:
        name = str(item if item is not None else "").strip()[:CUSTOMER_MATCH_NAME_MAX]
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= CUSTOMER_MATCH_MAX_NAMES:
            break
    return out


class CustomerMatchStore:

    def __init__(self, cache_file):
        self._cache_file = Path(cache_file)
        self._lock = threading.RLock()
        self._names = []

    def load(self):
        raw, _alg = persistence.load_encoded(self._cache_file)
        if raw is not None:
            with self._lock:
                self._names = coerce(raw)
            log.info("Loaded %d suspected customer name(s) from cache", len(self._names))

    def snapshot(self):
        with self._lock:
            return {"customers": list(self._names)}

    def names(self):
        """The bare list of names (a copy), for the download matcher."""
        with self._lock:
            return list(self._names)

    def update(self, raw):
        """Replace the list with the cleaned ``raw`` input; persist."""
        with self._lock:
            self._names = coerce(raw)
            self._save()
            return {"customers": list(self._names)}

    def _save(self):
        # Caller must hold the lock.
        persistence.save_encoded(self._cache_file, {"customers": self._names})
