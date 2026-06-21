"""Persistence for user-defined automations.

An automation is a small dict: a name, a card colour, an enabled flag (the card's
running/idle state), a periodic interval, a saved search ``query`` (the same
fields schema the sidebar and templates use), and an ordered subset of
``config.AUTOMATION_STEPS``. Plus run bookkeeping (``last_run``/``last_status``).

Like the other stores, the single JSON object is guarded by an ``RLock``, written
atomically, and encoded at rest through ``crypto`` (via ``persistence``). This
store owns the *definitions* only; ``automation.py`` runs them.
"""

import logging
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

import config

from . import persistence
from .settings_store import coerce as coerce_query

log = logging.getLogger(__name__)

MAX_NAME = 80
DEFAULT_COLOR = "#3b82f6"
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class AutomationStore:

    def __init__(self, cache_file):
        self._cache_file = Path(cache_file)
        self._lock = threading.RLock()
        self._items = {}  # id -> automation dict

    def load(self):
        raw, _alg = persistence.load_encoded(self._cache_file)
        items = {}
        if isinstance(raw, list):
            for entry in raw:
                coerced = self._coerce(entry)
                if coerced is not None:
                    items[coerced["id"]] = coerced
        with self._lock:
            self._items = items
        log.info("Loaded %d automation(s)", len(items))

    def snapshot(self):
        """Every automation, oldest-first (creation order), as independent copies."""
        with self._lock:
            ordered = sorted(self._items.values(), key=lambda a: a.get("created", ""))
            return [dict(a) for a in ordered]

    def create(self, raw):
        coerced = self._coerce(raw, new=True)
        with self._lock:
            self._items[coerced["id"]] = coerced
            self._save()
            return dict(coerced)

    def update(self, aid, raw):
        with self._lock:
            current = self._items.get(aid)
            if current is None:
                return None
            merged = self._coerce({**current, **(raw or {}), "id": aid}, base=current)
            self._items[aid] = merged
            self._save()
            return dict(merged)

    def set_enabled(self, aid, enabled):
        with self._lock:
            current = self._items.get(aid)
            if current is None:
                return None
            current["enabled"] = bool(enabled)
            self._save()
            return dict(current)

    def mark_run(self, aid, status):
        """Record that ``aid`` just ran (timestamp + short status). Ignores unknown ids."""
        with self._lock:
            current = self._items.get(aid)
            if current is None:
                return
            current["last_run"] = datetime.now().strftime(config.RECEIVED_FORMAT)
            current["last_status"] = str(status)[:200]
            self._save()

    def delete(self, aid):
        with self._lock:
            existed = self._items.pop(aid, None) is not None
            if existed:
                self._save()
            return existed

    def _coerce(self, raw, base=None, new=False):
        """Normalize one automation dict: known fields only, typed and bounded.

        ``new`` mints a fresh id and creation timestamp. Returns ``None`` for a
        non-dict (so a corrupt cache entry is dropped on load).
        """
        if not isinstance(raw, dict):
            return None
        base = base or {}

        if new or not raw.get("id"):
            aid = uuid.uuid4().hex
            created = datetime.now().strftime(config.RECEIVED_FORMAT)
        else:
            aid = str(raw["id"])
            created = raw.get("created") or base.get("created") \
                or datetime.now().strftime(config.RECEIVED_FORMAT)

        color = raw.get("color", base.get("color", DEFAULT_COLOR))
        if not (isinstance(color, str) and _HEX_RE.match(color)):
            color = base.get("color", DEFAULT_COLOR)

        try:
            interval = int(raw.get("interval_seconds",
                                   base.get("interval_seconds",
                                            config.AUTOMATION_DEFAULT_INTERVAL_SECONDS)))
        except (TypeError, ValueError):
            interval = config.AUTOMATION_DEFAULT_INTERVAL_SECONDS
        interval = max(config.AUTOMATION_MIN_INTERVAL_SECONDS, interval)

        steps = [s for s in config.AUTOMATION_STEPS
                 if s in (raw.get("steps", base.get("steps", [])) or [])]

        query = coerce_query(raw.get("query", base.get("query")))

        name = str(raw.get("name", base.get("name", "")))[:MAX_NAME].strip() or "Untitled"

        return {
            "id": aid,
            "name": name,
            "color": color,
            "enabled": bool(raw.get("enabled", base.get("enabled", False))),
            "interval_seconds": interval,
            "query": query,
            "steps": steps,
            "created": created,
            "last_run": raw.get("last_run", base.get("last_run")),
            "last_status": raw.get("last_status", base.get("last_status", "")),
        }

    def _save(self):
        # Caller must hold the lock. Persist as a list (creation order).
        ordered = sorted(self._items.values(), key=lambda a: a.get("created", ""))
        persistence.save_encoded(self._cache_file, ordered)
