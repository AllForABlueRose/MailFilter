"""Tests for mailfilter.dedup: Brute Force Mail Deduplication (pure transform)."""

import unittest

from mailfilter import dedup
from mailfilter.store import MailStore
from tests.factories import make_mail


def _snap(mails):
    return [MailStore._with_derived(m) for m in mails]


# An original mail and a Zendesk notification ~10 min later whose body echoes the
# original's subject + body and carries the ticket link.
ORIG = dict(id="ORIG", subject="Server error report", body="Disk full on node 3",
            received="2026-06-10 09:30:00")
NOTE = dict(id="NOTE", subject="New ticket created", received="2026-06-10 09:40:00",
            body="A ticket was opened.\nSubject: Server error report\n"
                 "Body: Disk full on node 3\nSee https://zendesk.example/tickets/42")
LINK = "https://zendesk.example/tickets/42"


class DedupeTests(unittest.TestCase):
    def test_pairs_notification_with_twin_and_appends_link(self):
        snap = _snap([make_mail(**NOTE), make_mail(**ORIG)])
        hidden, twin = dedup.dedupe(snap, "New ticket created")
        self.assertEqual(hidden, {"NOTE"})
        self.assertEqual(twin, {"ORIG": [LINK]})

    def test_subject_match_is_exact_case_insensitive(self):
        snap = _snap([make_mail(**NOTE), make_mail(**ORIG)])
        # Case/whitespace differences still match…
        self.assertEqual(dedup.dedupe(snap, "  new ticket CREATED ")[0], {"NOTE"})
        # …but a partial/substring subject does not (exact match).
        self.assertEqual(dedup.dedupe(snap, "New ticket")[0], set())

    def test_blank_subject_is_a_noop(self):
        snap = _snap([make_mail(**NOTE), make_mail(**ORIG)])
        self.assertEqual(dedup.dedupe(snap, "   "), (set(), {}))
        self.assertEqual(dedup.dedupe(snap, None), (set(), {}))

    def test_twin_must_be_within_the_window(self):
        far = dict(ORIG, id="FAR", received="2026-06-10 07:00:00")  # >1h before NOTE
        snap = _snap([make_mail(**NOTE), make_mail(**far)])
        hidden, twin = dedup.dedupe(snap, "New ticket created")
        self.assertEqual(hidden, set())   # no twin in range -> nothing deduped
        self.assertEqual(twin, {})

    def test_twin_needs_both_subject_and_body_in_notification_body(self):
        # Subject present but body absent from the notification -> not a twin.
        other = dict(id="OTH", subject="Server error report",
                     body="Totally different content", received="2026-06-10 09:35:00")
        snap = _snap([make_mail(**NOTE), make_mail(**other)])
        self.assertEqual(dedup.dedupe(snap, "New ticket created"), (set(), {}))

    def test_empty_bodied_candidate_is_not_a_twin(self):
        empty = dict(id="EMP", subject="Server error report", body="",
                     received="2026-06-10 09:35:00")
        snap = _snap([make_mail(**NOTE), make_mail(**empty)])
        self.assertEqual(dedup.dedupe(snap, "New ticket created"), (set(), {}))

    def test_multiple_twins_all_get_the_link(self):
        twin2 = dict(ORIG, id="ORIG2", received="2026-06-10 09:45:00")
        snap = _snap([make_mail(**NOTE), make_mail(**ORIG), make_mail(**twin2)])
        hidden, twin = dedup.dedupe(snap, "New ticket created")
        self.assertEqual(hidden, {"NOTE"})
        self.assertEqual(twin, {"ORIG": [LINK], "ORIG2": [LINK]})

    def test_no_notification_means_no_change(self):
        snap = _snap([make_mail(**ORIG)])
        self.assertEqual(dedup.dedupe(snap, "New ticket created"), (set(), {}))


if __name__ == "__main__":
    unittest.main()
