"""Cold-start initializer: the first-run full inbox sync.

When the server starts and finds no *complete* mail cache on disk, there is no
incremental high-water mark to fetch from — the whole inbox has to be pulled in
one pass, which is by far the slowest thing the app does. This module owns that
path: startup hands it the store and it runs an optimized full sync
(:func:`mailfilter.outlook.initial_sync`) in the background, so the web server
comes up immediately (serving whatever is already cached) while the sync streams
mail in, batch by batch, reporting progress through the store's status.

Resumability is the reason this is a separate concern from a normal refresh. A
refresh fetches mail *newer* than what is cached; a half-finished initial sync is
missing mail *older* than what is cached, which a refresh would never backfill.
So "is the initial sync done?" cannot be inferred from the cache file alone. An
in-progress marker file (``config.INITIAL_SYNC_MARKER``) is created before the
sync and removed only on success; while it exists (or the cache is absent),
startup re-runs the bootstrap, and because the full sync dedups by EntryID it
skips what was already written and finishes the rest. An existing cache from
before this mechanism has no marker, so it is correctly treated as complete.

Dependency direction: ``bootstrap -> outlook -> store`` (and ``bootstrap`` reads
``config``); nothing imports ``bootstrap`` except the app factory that wires it.
"""

import logging
import threading
from pathlib import Path

import config

from . import outlook

log = logging.getLogger(__name__)


def needs_bootstrap(cache_file=None, marker=None):
    """True if startup should run the cold-start full sync.

    That is the case when no cache exists yet, or when an initial sync was
    started but never finished (its in-progress marker is still on disk).
    """
    cache_file = Path(cache_file if cache_file is not None else config.CACHE_FILE)
    marker = Path(marker if marker is not None else config.INITIAL_SYNC_MARKER)
    return not cache_file.exists() or marker.exists()


def start_async(store):
    """Run :func:`run` on a background daemon thread; returns the thread."""
    thread = threading.Thread(
        target=run, args=(store,), name="mail-bootstrap", daemon=True
    )
    thread.start()
    return thread


def run(store):
    """Perform (or resume) the initial full inbox sync.

    Marks the sync in progress, runs it, and clears the marker only if it
    completed cleanly — a failure (e.g. Outlook unavailable) leaves the marker so
    the next start retries. Safe to call when Outlook is absent: the sync records
    a "Failed" status and the app keeps serving the cache.
    """
    marker = Path(config.INITIAL_SYNC_MARKER)
    log.info("No complete mail cache found — starting initial full inbox sync...")
    _touch(marker)

    outlook.initial_sync(store)

    if store.status_snapshot()["fetch_status"].startswith("Success"):
        _remove(marker)
        log.info("Initial sync finished; cleared in-progress marker.")
    else:
        log.warning(
            "Initial sync did not complete (%s) — it will resume on the next "
            "start.",
            store.status_snapshot()["fetch_status"],
        )


def _touch(path):
    try:
        path.touch()
    except OSError:
        log.warning("Could not write initial-sync marker %s", path)


def _remove(path):
    try:
        path.unlink()
    except OSError:
        log.warning("Could not remove initial-sync marker %s", path)
