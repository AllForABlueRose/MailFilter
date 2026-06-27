"""Persistence for which experimental features the user has enabled.

A small sibling of :class:`mailfilter.settings_store.SettingsStore`: a single JSON
object of ``{feature_id: bool}`` enablement flags, guarded by an ``RLock``, written
atomically and encoded at rest through the same ``crypto`` seam as the other stores.
Saved whenever the user hits "Update Settings" in the Experimental Features panel
and reloaded at startup, so a relaunch restores the mounted feature set.

Enablement is *only* whether a feature's control is mounted in the sidebar box. A
feature's own operational state (the password filter, the normalize-width toggle)
is a search field and lives in :mod:`mailfilter.settings_store`, not here.
"""

import logging
import threading
from pathlib import Path

from config import EXPERIMENTAL_DEFAULTS

from . import persistence

log = logging.getLogger(__name__)

# The known experimental feature ids and their default enablement. The single
# source of truth is config.EXPERIMENTAL_DEFAULTS; copied here so coerce() can
# drop unknown keys the same way settings_store.coerce does.
DEFAULTS = dict(EXPERIMENTAL_DEFAULTS)


def coerce(raw, base=None):
    """Return ``base`` with the known boolean feature flags from ``raw`` applied.

    Unknown keys are dropped; missing keys keep ``base`` (which defaults to
    :data:`DEFAULTS`). Every value is coerced to ``bool``.
    """
    out = dict(DEFAULTS if base is None else base)
    if not isinstance(raw, dict):
        return out
    for key in DEFAULTS:
        if key in raw and raw[key] is not None:
            out[key] = bool(raw[key])
    return out


class ExperimentalStore:

    def __init__(self, cache_file):
        self._cache_file = Path(cache_file)
        self._lock = threading.RLock()
        self._flags = dict(DEFAULTS)

    def load(self):
        raw, _alg = persistence.load_encoded(self._cache_file)
        if isinstance(raw, dict):
            with self._lock:
                self._flags = coerce(raw, DEFAULTS)
            log.info("Loaded experimental-feature flags from cache")

    def snapshot(self):
        with self._lock:
            return dict(self._flags)

    def update(self, raw):
        """Merge known flags from ``raw`` over the current set; persist."""
        with self._lock:
            self._flags = coerce(raw, self._flags)
            self._save()
            return dict(self._flags)

    def _save(self):
        # Caller must hold the lock.
        persistence.save_encoded(self._cache_file, self._flags)
