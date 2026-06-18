"""Tests for mailfilter.bootstrap — the cold-start decision and marker lifecycle.

The COM-driven sync itself can't run off Windows, so ``outlook.initial_sync`` is
stubbed to drive the store status the way a real sync would. These tests pin the
two behaviours bootstrap owns: *when* to run a full sync, and the in-progress
marker that makes an interrupted one resume instead of being mistaken for done.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from mailfilter import bootstrap
from mailfilter.store import MailStore


class NeedsBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.cache = self.dir / "mail_cache.json"
        self.marker = self.dir / "mail_cache.syncing"

    def test_true_when_no_cache(self):
        self.assertTrue(bootstrap.needs_bootstrap(self.cache, self.marker))

    def test_false_when_cache_exists_and_no_marker(self):
        # A complete cache from before this mechanism: no marker, treat as done.
        self.cache.write_text("encoded")
        self.assertFalse(bootstrap.needs_bootstrap(self.cache, self.marker))

    def test_true_when_marker_present_even_with_partial_cache(self):
        # An interrupted initial sync left a partial cache and its marker.
        self.cache.write_text("encoded")
        self.marker.touch()
        self.assertTrue(bootstrap.needs_bootstrap(self.cache, self.marker))


class RunMarkerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.marker = self.dir / "mail_cache.syncing"
        self.store = MailStore(self.dir / "mail_cache.json")
        patcher = mock.patch.object(config, "INITIAL_SYNC_MARKER", self.marker)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_marker_cleared_on_success(self):
        def fake_sync(store):
            self.assertTrue(self.marker.exists())  # in progress during the sync
            store.set_success(10)

        with mock.patch("mailfilter.outlook.initial_sync", side_effect=fake_sync):
            bootstrap.run(self.store)
        self.assertFalse(self.marker.exists())

    def test_marker_kept_on_failure_so_next_start_resumes(self):
        with mock.patch(
            "mailfilter.outlook.initial_sync",
            side_effect=lambda store: store.set_failure(RuntimeError("Outlook down")),
        ):
            bootstrap.run(self.store)
        self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
