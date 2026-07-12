"""Persistence for the selectable organization **categories** (Customer Management).

A category is an organization's formality class -- "Root", "Partner", "Vendor",
"Customer" and whatever else the user invents. It was free text on each org, which
meant no list to pick from, no autocomplete, and no way to know what "Partner" was
spelled like last time. This store holds the list.

* Seeded with ``config.ORG_DEFAULT_CATEGORIES`` on the **first ever run** (the app
  factory, keyed on the cache file never having existed) -- so the four are simply
  there, and deleting them is respected.
* ``add`` on a category that is not in the list **creates** it: typing a new one on an
  organization is all it takes to make it selectable from then on.
* Case-insensitively deduplicated, first spelling wins -- so "partner" typed later
  does not shadow the "Partner" the whole app already agrees on. This matters beyond
  cosmetics: ``config.ORG_PARTNER_CATEGORY`` decides whose domains count as internal
  (``customers.internal_domains``), and that comparison is case-insensitive precisely
  because a category is typed by hand.

Order is meaningful (it is the order the picker offers), so this is a list, not a set.
Guarded by an ``RLock``, written atomically, encoded at rest through the same
``crypto`` seam as the other stores. It carries no secrets.
"""

import logging
import threading
from pathlib import Path

import config

from . import persistence

log = logging.getLogger(__name__)


def coerce(raw):
    """A clean category list: trimmed, capped, case-insensitively deduped, bounded."""
    if isinstance(raw, dict):
        raw = raw.get("categories")
    if not isinstance(raw, list):
        return []
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, str):
            continue
        name = item.strip()[:config.ORG_CATEGORY_MAX]
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
        if len(out) >= config.ORG_CATEGORIES_MAX:
            break
    return out


class CategoryStore:

    def __init__(self, cache_file):
        self._cache_file = Path(cache_file)
        self._lock = threading.RLock()
        self._items = []

    def load(self):
        raw, _alg = persistence.load_encoded(self._cache_file)
        items = coerce(raw)
        with self._lock:
            self._items = items
        log.info("Loaded %d organization category/categories", len(items))

    def seed(self, names):
        """Fill an empty store with ``names`` (first run only). Returns the list."""
        with self._lock:
            if not self._items:
                self._items = coerce(list(names))
                self._save()
            return list(self._items)

    def snapshot(self):
        with self._lock:
            return list(self._items)

    def add(self, name):
        """Add ``name`` if it is not already present (case-insensitive). Returns True
        when it was genuinely created -- typing a new category is what creates it."""
        name = str(name or "").strip()[:config.ORG_CATEGORY_MAX]
        if not name:
            return False
        with self._lock:
            if any(existing.lower() == name.lower() for existing in self._items):
                return False
            if len(self._items) >= config.ORG_CATEGORIES_MAX:
                log.warning("Category list is full (%d); not adding %r",
                            config.ORG_CATEGORIES_MAX, name)
                return False
            self._items.append(name)
            self._save()
            log.info("Created organization category %r", name)
            return True

    def update(self, raw):
        """Replace the whole list (the user reordering/pruning it)."""
        with self._lock:
            self._items = coerce(raw)
            self._save()
            return list(self._items)

    def _save(self):
        # Caller must hold the lock.
        persistence.save_encoded(self._cache_file, self._items)
