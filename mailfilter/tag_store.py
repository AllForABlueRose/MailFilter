"""Persistence for per-mail workspace action tags.

Records, per mail id, when an action was performed (attachments downloaded,
links opened) so the tags survive restarts. A tag set within the last
:data:`RECENT_DAYS` days reports as ``"recent"`` (the UI colours it); an older
one reports as ``"old"`` (the UI greys it but keeps it visible).

Like the other stores, the single JSON object is guarded by an ``RLock``,
written atomically, and encoded at rest through ``crypto``.
"""

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import persistence

log = logging.getLogger(__name__)

RECENT_DAYS = 7
ACTIONS = ("downloaded", "links", "marked")
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


class TagStore:

    def __init__(self, cache_file):
        self._cache_file = Path(cache_file)
        self._lock = threading.RLock()
        self._tags = {}  # mail_id -> {action: timestamp_str}

    def load(self):
        raw, _alg = persistence.load_encoded(self._cache_file)
        if isinstance(raw, dict):
            with self._lock:
                self._tags = {
                    mid: {
                        a: ts for a, ts in actions.items()
                        if a in ACTIONS and isinstance(ts, str)
                    }
                    for mid, actions in raw.items()
                    if isinstance(actions, dict)
                }
            log.info("Loaded action tags for %d mail(s)", len(self._tags))

    def record(self, mail_id, action):
        """Mark ``action`` as performed on ``mail_id`` now (ignores junk input)."""
        if not mail_id or action not in ACTIONS:
            return
        now = datetime.now().strftime(_TS_FORMAT)
        with self._lock:
            self._tags.setdefault(mail_id, {})[action] = now
            self._save()

    def remove(self, mail_id, action):
        """Clear ``action`` from ``mail_id`` (e.g. the user unmarks a mail)."""
        if not mail_id or action not in ACTIONS:
            return
        with self._lock:
            actions = self._tags.get(mail_id)
            if not actions or action not in actions:
                return
            del actions[action]
            if not actions:
                del self._tags[mail_id]
            self._save()

    def tags_for(self, mail_id):
        """Return ``{action: "recent"|"old"}`` for actions recorded on this mail."""
        with self._lock:
            actions = self._tags.get(mail_id)
            if not actions:
                return {}
            cutoff = datetime.now() - timedelta(days=RECENT_DAYS)
            out = {}
            for action, ts in actions.items():
                try:
                    when = datetime.strptime(ts, _TS_FORMAT)
                except ValueError:
                    continue
                out[action] = "recent" if when >= cutoff else "old"
            return out

    def _save(self):
        # Caller must hold the lock.
        persistence.save_encoded(self._cache_file, self._tags)
