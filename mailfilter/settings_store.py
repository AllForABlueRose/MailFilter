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
    "exclude_sender": "",
    "exclude_recipient": "",
    "attachment_blacklist": "",
    "links_blacklist": "",
    "resources": False,
    "passwords": False,
    # Experimental: fold full-width <-> half-width on the keyword match. Persisted
    # like the other search toggles so a relaunch (and templates) carry it.
    "normalize_width": False,
    # Experimental: extend the keyword match to attachment names / link URLs.
    "attachment_search": False,
    "link_search": False,
    # Experimental (workspace, not search): append the sender's org name to
    # batch-downloaded files. Persisted as a last-used preference; a saved template
    # doesn't carry it (it's in TEMPLATE_EXCLUDED_FIELDS).
    "append_customer_name": False,
    # Experimental (workspace, not search): append a Suspected Customers List name
    # found in a mail's content to batch-downloaded files. Also template-excluded.
    "resolve_customer_name": False,
}

# Per-string cap so a buggy/hostile client can't grow the file without bound.
MAX_LEN = 500

# Fields the sidebar persists but a saved template deliberately does NOT carry:
# the date range, the width-normalization toggle, and the download-naming toggle
# are per-session/context (or workspace) choices, not part of a reusable search
# preset. `coerce_template` forces these back to their defaults on save, and the UI
# re-applies the live values after switching to a template (see templates.js).
TEMPLATE_EXCLUDED_FIELDS = ("start", "end", "normalize_width", "append_customer_name",
                            "resolve_customer_name")


def coerce(raw, base=None):
    """Return ``base`` with the known string/bool search fields from ``raw`` applied.

    The single source of truth for the search-settings schema: the last-used
    search (:class:`SettingsStore`) and each saved template
    (:class:`mailfilter.template_store.TemplateStore`) both pass their input
    through here, so a settings dict can only ever hold the known fields, typed
    and length-capped. Unknown keys are dropped; missing keys keep ``base``.
    ``base`` defaults to :data:`DEFAULTS`.
    """
    out = dict(DEFAULTS if base is None else base)
    if not isinstance(raw, dict):
        return out
    for key, default in DEFAULTS.items():
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        out[key] = bool(value) if isinstance(default, bool) else str(value)[:MAX_LEN]
    return out


def coerce_template(raw, base=None):
    """Coerce a saved template body: like :func:`coerce`, but the
    :data:`TEMPLATE_EXCLUDED_FIELDS` (date range + width normalization) are reset
    to their defaults so a preset never carries them. The body keeps every key
    (at its default) so its shape stays stable for the UI and export/import.
    """
    out = coerce(raw, base)
    for key in TEMPLATE_EXCLUDED_FIELDS:
        out[key] = DEFAULTS[key]
    return out


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
                self._settings = coerce(raw, DEFAULTS)
            log.info("Loaded search settings from cache")

    def snapshot(self):
        with self._lock:
            return dict(self._settings)

    def update(self, raw):
        """Merge known fields from ``raw`` over the current settings; persist."""
        with self._lock:
            self._settings = coerce(raw, self._settings)
            self._save()
            return dict(self._settings)

    def _save(self):
        # Caller must hold the lock.
        persistence.save_encoded(self._cache_file, self._settings)
