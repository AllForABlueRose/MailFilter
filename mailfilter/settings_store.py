"""Persistence for the sidebar search settings (last-used keywords/filters).

A small sibling of :class:`mailfilter.store.MailStore`: a single JSON object,
guarded by an ``RLock``, written atomically and encoded at rest through the same
``crypto`` seam as the mail cache. Saved on every change from the UI and reloaded
at startup, so relaunching the app restores the previous search.
"""

import logging
import threading
from pathlib import Path

from . import persistence

log = logging.getLogger(__name__)

# The exact fields the sidebar persists. Keys match the /api/mail query params.
# Anything else in a POST body is ignored; missing fields fall back to these.
DEFAULTS = {
    "start": "",
    "end": "",
    "main": "",
    "optional": "",
    "exclude": "",
    "sender": "",
    "recipient": "",
    "resources": False,
}

# Per-string cap so a buggy/hostile client can't grow the file without bound.
MAX_LEN = 500


class SettingsStore:

    def __init__(self, cache_file):
        self._cache_file = Path(cache_file)
        self._lock = threading.RLock()
        self._settings = dict(DEFAULTS)

    def load(self):
        raw, _alg = persistence.load_encoded(self._cache_file)
        if isinstance(raw, dict):
            with self._lock:
                # Start from DEFAULTS so any missing/legacy keys are filled in.
                self._settings = self._coerce(raw, DEFAULTS)
            log.info("Loaded search settings from cache")

    def snapshot(self):
        with self._lock:
            return dict(self._settings)

    def update(self, raw):
        """Merge known fields from ``raw`` over the current settings; persist."""
        with self._lock:
            self._settings = self._coerce(raw, self._settings)
            self._save()
            return dict(self._settings)

    @staticmethod
    def _coerce(raw, base):
        """Return ``base`` with the known string/bool fields from ``raw`` applied."""
        out = dict(base)
        for key, default in DEFAULTS.items():
            if key not in raw or raw[key] is None:
                continue
            value = raw[key]
            out[key] = bool(value) if isinstance(default, bool) else str(value)[:MAX_LEN]
        return out

    def _save(self):
        # Caller must hold the lock.
        persistence.save_encoded(self._cache_file, self._settings)
