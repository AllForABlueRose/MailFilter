"""Background refresh thread with clean start/stop semantics."""

import logging
import threading

log = logging.getLogger(__name__)


class RefreshScheduler:

    def __init__(self, interval_seconds, refresh_fn):
        self._interval = interval_seconds
        self._refresh = refresh_fn
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="mail-refresh",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                self._refresh()
            except Exception:
                log.exception("Background refresh failed")
            # Interruptible sleep: stop() wakes it immediately.
            self._stop.wait(self._interval)
