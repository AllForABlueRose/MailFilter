"""Persistence for the Suspected Customers List.

Backs the experimental "Brute Force Resolve Customer Name" feature: a list of
**keyword -> organization** mappings the user wants looked for in mail content at
download/report time. Each entry is ``{"keyword": <text>, "org_id": <org id>}``:
when the keyword appears in a mail's content the mail resolves to that
organization. A single JSON object ``{"customers": [...]}`` guarded by an
``RLock``, written atomically and encoded at rest through the same ``crypto`` seam
as the other stores. The matching itself (does a keyword appear in a mail's
content, and which org does it map to?) lives in ``workspace_ops``; this store only
persists and sanitizes the list.

Legacy caches held bare name strings (``{"customers": ["Acme", ...]}``); those
migrate up on load to ``{"keyword": "Acme", "org_id": ""}`` (unmapped until the
user assigns an organization).
"""

import logging
import threading
from pathlib import Path

from config import CUSTOMER_MATCH_MAX_NAMES, CUSTOMER_MATCH_NAME_MAX

from . import persistence

log = logging.getLogger(__name__)


def coerce(raw):
    """Return a cleaned list of ``{"keyword", "org_id"}`` mappings from ``raw``.

    Accepts a ``{"customers": [...]}`` dict or a list/tuple whose items are either
    the new ``{"keyword": ..., "org_id": ...}`` dicts or legacy bare name strings
    (migrated to ``{"keyword": <name>, "org_id": ""}``). Each ``keyword`` is trimmed
    and length-capped (:data:`CUSTOMER_MATCH_NAME_MAX`) and ``org_id`` coerced to a
    trimmed string; blank-keyword rows and case-insensitive duplicate keywords are
    dropped; the list is capped at :data:`CUSTOMER_MATCH_MAX_NAMES` (first-wins).
    """
    if isinstance(raw, dict):
        raw = raw.get("customers")
    if not isinstance(raw, (list, tuple)):
        return []
    out, seen = [], set()
    for item in raw:
        if isinstance(item, dict):
            keyword = str(item.get("keyword") or "").strip()[:CUSTOMER_MATCH_NAME_MAX]
            org_id = str(item.get("org_id") or "").strip()
        else:
            # Legacy bare name: becomes an unmapped keyword.
            keyword = str(item if item is not None else "").strip()[:CUSTOMER_MATCH_NAME_MAX]
            org_id = ""
        if not keyword:
            continue
        key = keyword.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"keyword": keyword, "org_id": org_id})
        if len(out) >= CUSTOMER_MATCH_MAX_NAMES:
            break
    return out


class CustomerMatchStore:

    def __init__(self, cache_file):
        self._cache_file = Path(cache_file)
        self._lock = threading.RLock()
        self._mappings = []

    def load(self):
        raw, _alg = persistence.load_encoded(self._cache_file)
        if raw is not None:
            with self._lock:
                self._mappings = coerce(raw)
            log.info("Loaded %d suspected customer mapping(s) from cache", len(self._mappings))

    def snapshot(self):
        with self._lock:
            return {"customers": [dict(m) for m in self._mappings]}

    def mappings(self):
        """The keyword->org mappings (a copy of dicts), for the download/report matcher."""
        with self._lock:
            return [dict(m) for m in self._mappings]

    def update(self, raw):
        """Replace the list with the cleaned ``raw`` input; persist."""
        with self._lock:
            self._mappings = coerce(raw)
            self._save()
            return {"customers": [dict(m) for m in self._mappings]}

    def _save(self):
        # Caller must hold the lock.
        persistence.save_encoded(self._cache_file, {"customers": self._mappings})
