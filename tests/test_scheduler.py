"""Tests for mailfilter.scheduler.RefreshScheduler start/stop behaviour."""

import threading
import unittest

from mailfilter.scheduler import RefreshScheduler


class RefreshSchedulerTests(unittest.TestCase):
    def test_invokes_refresh_then_stops(self):
        called = threading.Event()
        sched = RefreshScheduler(0.01, called.set)
        sched.start()
        self.addCleanup(sched.stop)
        self.assertTrue(called.wait(2.0), "refresh_fn was never called")
        sched.stop()

    def test_double_start_is_idempotent(self):
        sched = RefreshScheduler(0.01, lambda: None)
        sched.start()
        first = sched._thread
        sched.start()  # must not spawn a second thread
        self.addCleanup(sched.stop)
        self.assertIs(sched._thread, first)

    def test_exception_in_refresh_does_not_kill_loop(self):
        counter = {"n": 0}
        done = threading.Event()

        def flaky():
            counter["n"] += 1
            if counter["n"] == 1:
                raise RuntimeError("first call fails")
            done.set()

        sched = RefreshScheduler(0.01, flaky)
        sched.start()
        self.addCleanup(sched.stop)
        self.assertTrue(done.wait(2.0), "loop did not survive an exception")


if __name__ == "__main__":
    unittest.main()
