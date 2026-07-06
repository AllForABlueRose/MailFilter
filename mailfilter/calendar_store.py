"""Persistence for Workshop → Calendar file pins.

A pin records that a file from a day's workspace was dragged onto a calendar day
so it should reappear in *that* day's workspace when it arrives. Each pin is a
small dict: the target ``date`` (``YYYY-MM-DD``), the original ``filename``, the
``limbo_name`` under which a copy waits in the limbo holding folder
(``WORKSPACE_DIR/<WORKSPACE_LIMBO_DIRNAME>/``), an optional free-text
``description`` ("why was this pinned"), the file's customer-organization metadata
carried over from the source folder's manifest (``org_id``/``org_name``/
``mail_id``, so :mod:`mailfilter.calendar_ops` can recreate the manifest record on
materialization), and materialization bookkeeping (``materialized`` +
``materialized_folder``).

Like the other stores, the single JSON list is guarded by an ``RLock``, written
atomically, and encoded at rest through ``crypto`` (via ``persistence``). It
carries no secrets. This store owns the pin *records* only;
:mod:`mailfilter.calendar_ops` performs the filesystem work (copy to limbo,
materialize into the dated folder).
"""

import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path

import config

from . import persistence

log = logging.getLogger(__name__)

_STR_FIELDS = ("date", "filename", "limbo_name", "description",
               "org_id", "org_name", "mail_id", "materialized_folder")
_MAX = {
    "description": config.CALENDAR_PIN_DESCRIPTION_MAX,
}


class CalendarStore:

    def __init__(self, pins_file):
        self._pins_file = Path(pins_file)
        self._lock = threading.RLock()
        self._items = {}  # id -> pin dict

    def load(self):
        raw, _alg = persistence.load_encoded(self._pins_file)
        items = {}
        if isinstance(raw, list):
            for entry in raw:
                coerced = self._coerce(entry)
                if coerced is not None:
                    items[coerced["id"]] = coerced
        with self._lock:
            self._items = items
        log.info("Loaded %d calendar pin(s)", len(items))

    def snapshot(self):
        """Every pin, oldest-first (creation order), as independent copies."""
        with self._lock:
            ordered = sorted(self._items.values(), key=lambda p: p.get("created", ""))
            return [dict(p) for p in ordered]

    def add(self, raw):
        coerced = self._coerce(raw, new=True)
        with self._lock:
            self._items[coerced["id"]] = coerced
            self._save()
            return dict(coerced)

    def mark_materialized(self, pid, folder):
        """Flag a pin materialized and record which dated folder it landed in.

        Idempotent: the caller (calendar_ops) only materializes pins that are not
        already flagged, so a repeated startup on the same day is a no-op.
        """
        with self._lock:
            current = self._items.get(pid)
            if current is None:
                return None
            current["materialized"] = True
            current["materialized_folder"] = str(folder)
            self._save()
            return dict(current)

    def remove(self, pid):
        with self._lock:
            existed = self._items.pop(pid, None) is not None
            if existed:
                self._save()
            return existed

    def get(self, pid):
        with self._lock:
            current = self._items.get(pid)
            return dict(current) if current is not None else None

    def _coerce(self, raw, new=False):
        """Normalize one pin dict: known fields only, typed and bounded.

        ``new`` mints a fresh id and creation timestamp. Returns ``None`` for a
        non-dict (so a corrupt cache entry is dropped on load).
        """
        if not isinstance(raw, dict):
            return None

        if new or not raw.get("id"):
            pid = uuid.uuid4().hex
            created = datetime.now().strftime(config.RECEIVED_FORMAT)
        else:
            pid = str(raw["id"])
            created = raw.get("created") or datetime.now().strftime(config.RECEIVED_FORMAT)

        out = {"id": pid, "created": created,
               "materialized": bool(raw.get("materialized", False))}
        for field in _STR_FIELDS:
            value = str(raw.get(field) or "")
            cap = _MAX.get(field)
            if cap is not None:
                value = value[:cap]
            out[field] = value
        return out

    def _save(self):
        # Caller must hold the lock. Persist as a list (creation order).
        ordered = sorted(self._items.values(), key=lambda p: p.get("created", ""))
        persistence.save_encoded(self._pins_file, ordered)
