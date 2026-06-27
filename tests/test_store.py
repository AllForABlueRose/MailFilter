"""Tests for mailfilter.store: link extraction, derived fields, dedup,
thread detection, sorting, atomic persistence, and fetch status."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from mailfilter import crypto
from mailfilter.store import MailStore, extract_links, own_message_body
from tests.factories import make_mail


class ExtractLinksTests(unittest.TestCase):
    def test_http_and_https_only(self):
        text = "go to http://a.com and https://b.com but not javascript:alert(1) or ftp://c.com"
        self.assertEqual(extract_links(text), ["http://a.com", "https://b.com"])

    def test_deduplicated_in_first_seen_order(self):
        text = "http://a.com/x then http://a.com/x then http://b.com/y"
        self.assertEqual(extract_links(text), ["http://a.com/x", "http://b.com/y"])

    def test_trailing_punctuation_trimmed(self):
        self.assertEqual(extract_links("see (http://a.com/p)."), ["http://a.com/p"])

    def test_none_and_empty(self):
        self.assertEqual(extract_links(None), [])
        self.assertEqual(extract_links(""), [])


class OwnMessageBodyTests(unittest.TestCase):
    def test_plain_message_is_unchanged(self):
        body = "Just a note.\nNo quoted history here."
        self.assertEqual(own_message_body(body), body)

    def test_cuts_at_outlook_reply_header(self):
        body = (
            "My reply with http://new.example/x\n\n"
            "From: Someone\nSent: today\n"
            "Old link http://old.example/y\n"
        )
        self.assertEqual(own_message_body(body).strip(), "My reply with http://new.example/x")

    def test_cuts_at_original_message_divider(self):
        body = "Top text\n-----Original Message-----\nquoted stuff\n"
        self.assertEqual(own_message_body(body).strip(), "Top text")

    def test_cuts_at_on_wrote_marker(self):
        body = "Sounds good.\nOn Tue, Jun 9, 2026, Alice wrote:\n> old line\n"
        self.assertEqual(own_message_body(body).strip(), "Sounds good.")

    def test_cuts_at_quoted_lines(self):
        body = "Agreed.\n> previous message\n> with http://old.example/z\n"
        self.assertEqual(own_message_body(body).strip(), "Agreed.")

    def test_empty(self):
        self.assertEqual(own_message_body(""), "")
        self.assertEqual(own_message_body(None), "")


class ThreadLinkScopingTests(unittest.TestCase):
    def test_derived_links_exclude_quoted_history(self):
        body = (
            "See the rollback PR: https://example.com/pull/1\n\n"
            "-----Original Message-----\n"
            "From: Sofia\n"
            "Dashboard: https://example.com/grafana\n"
            "Runbook: https://example.com/wiki\n"
        )
        m = MailStore._with_derived(make_mail(body=body))
        self.assertEqual(m["_links"], ["https://example.com/pull/1"])


class DerivedFieldsTests(unittest.TestCase):
    def setUp(self):
        self.store = _temp_store()

    def test_with_derived_computes_expected_fields(self):
        m = MailStore._with_derived(make_mail())
        self.assertEqual(m["_received_dt"], datetime(2026, 6, 10, 9, 30, 0))
        self.assertEqual(m["_links"], ["http://example.com/log", "https://example.com/log"])
        self.assertTrue(m["_has_links"])
        self.assertTrue(m["_has_attachments"])
        self.assertIn("alice@example.com", m["_sender_text"])
        self.assertIn("bob jones", m["_recipient_text"])
        # search text is lowercased and spans subject/body/sender/recipient
        self.assertIn("server error report", m["_search_text"])
        self.assertEqual(m["_search_text"], m["_search_text"].lower())

    def test_no_attachments_no_links(self):
        m = MailStore._with_derived(
            make_mail(attachments=[], body="plain text, no urls")
        )
        self.assertFalse(m["_has_attachments"])
        self.assertFalse(m["_has_links"])
        self.assertEqual(m["_links"], [])


class MutationTests(unittest.TestCase):
    def setUp(self):
        self.store = _temp_store()

    def test_add_mails_deduplicates_by_id(self):
        self.assertEqual(self.store.add_mails([make_mail(id="A")]), 1)
        # same id again is ignored; a new id is added
        added = self.store.add_mails([make_mail(id="A"), make_mail(id="B")])
        self.assertEqual(added, 1)
        self.assertEqual(self.store.known_ids(), {"A", "B"})

    def test_thread_flag_set_when_conversation_shared(self):
        self.store.add_mails([
            make_mail(id="A", conversation_id="C1"),
            make_mail(id="B", conversation_id="C1"),
            make_mail(id="C", conversation_id="C2"),
        ])
        by_id = {m["id"]: m for m in self.store.snapshot()}
        self.assertTrue(by_id["A"]["is_thread"])
        self.assertTrue(by_id["B"]["is_thread"])
        self.assertFalse(by_id["C"]["is_thread"])

    def test_snapshot_sorted_newest_first(self):
        self.store.add_mails([
            make_mail(id="old", received="2026-06-01 08:00:00"),
            make_mail(id="new", received="2026-06-12 08:00:00"),
            make_mail(id="mid", received="2026-06-05 08:00:00"),
        ])
        self.assertEqual([m["id"] for m in self.store.snapshot()], ["new", "mid", "old"])

    def test_latest_received(self):
        self.assertIsNone(self.store.latest_received())
        self.store.add_mails([
            make_mail(id="a", received="2026-06-01 08:00:00"),
            make_mail(id="b", received="2026-06-09 08:00:00"),
        ])
        self.assertEqual(self.store.latest_received(), datetime(2026, 6, 9, 8, 0, 0))


class ThreadForTests(unittest.TestCase):
    def setUp(self):
        self.store = _temp_store()
        self.store.add_mails([
            make_mail(id="m1", conversation_id="C", received="2026-06-03 08:00:00"),
            make_mail(id="m2", conversation_id="C", received="2026-06-01 08:00:00"),
            make_mail(id="m3", conversation_id="C", received="2026-06-02 08:00:00"),
            make_mail(id="other", conversation_id="D", received="2026-06-05 08:00:00"),
        ])

    def test_returns_conversation_members_earliest_first(self):
        ids = [m["id"] for m in self.store.thread_for("m1")]
        self.assertEqual(ids, ["m2", "m3", "m1"])  # ascending by received time

    def test_excludes_other_conversations(self):
        self.assertEqual([m["id"] for m in self.store.thread_for("other")], ["other"])

    def test_unknown_id_returns_empty(self):
        self.assertEqual(self.store.thread_for("nope"), [])


class PersistenceTests(unittest.TestCase):
    def test_save_encodes_on_disk_and_reload_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "cache.json"
            store = MailStore(cache)
            store.add_mails([make_mail(id="A"), make_mail(id="B", conversation_id="CONV1")])

            data = cache.read_bytes()
            # The cache is encoded (not bare plaintext JSON) on disk.
            self.assertTrue(data.startswith(crypto.MAGIC))
            self.assertNotEqual(data.lstrip()[:1], b"[")

            payload, alg = crypto.decode(data)
            self.assertEqual(alg, crypto.preferred_alg())

            # Decoded JSON must not contain any derived ("_"-prefixed) keys.
            raw = json.loads(payload)
            self.assertEqual(len(raw), 2)
            for entry in raw:
                self.assertFalse(
                    any(k.startswith("_") for k in entry),
                    f"derived field leaked to disk: {entry.keys()}",
                )

            # A fresh store reloads and recomputes derived fields.
            reloaded = MailStore(cache)
            reloaded.load()
            self.assertEqual(reloaded.known_ids(), {"A", "B"})
            self.assertTrue(all("_received_dt" in m for m in reloaded.snapshot()))

    def test_legacy_plaintext_cache_is_migrated_on_load(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "cache.json"
            # A pre-encryption cache: bare JSON, no header.
            cache.write_text(json.dumps([make_mail(id="A")]), encoding="utf-8")
            store = MailStore(cache)
            store.load()
            self.assertEqual(store.known_ids(), {"A"})
            # Loading upgrades it away from plaintext to the encoded format.
            self.assertTrue(cache.read_bytes().startswith(crypto.MAGIC))

    def test_load_skips_malformed_entries(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "cache.json"
            good = make_mail(id="good")
            bad = {"id": "bad"}  # missing 'received' -> _with_derived raises
            cache.write_text(json.dumps([good, bad]), encoding="utf-8")
            store = MailStore(cache)
            store.load()
            self.assertEqual(store.known_ids(), {"good"})

    def test_load_missing_file_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            store = MailStore(Path(d) / "absent.json")
            store.load()  # must not raise
            self.assertEqual(store.snapshot(), [])


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.store = _temp_store()

    def test_status_transitions(self):
        self.assertEqual(self.store.status_snapshot()["fetch_status"], "Not started")
        self.store.set_fetching()
        self.assertEqual(self.store.status_snapshot()["fetch_status"], "Fetching...")
        self.store.set_success(3)
        snap = self.store.status_snapshot()
        self.assertEqual(snap["fetch_status"], "Success (3 new)")
        self.assertEqual(snap["fetch_error"], "")
        self.assertNotEqual(snap["last_refresh"], "Never")
        self.store.set_failure(RuntimeError("boom"))
        snap = self.store.status_snapshot()
        self.assertEqual(snap["fetch_status"], "Failed")
        self.assertEqual(snap["fetch_error"], "boom")

    def test_progress_is_reported_then_cleared_by_terminal_states(self):
        self.store.set_fetching()
        self.store.set_progress("Initial sync: 100/200 mails (50%)")
        self.assertEqual(
            self.store.status_snapshot()["fetch_progress"],
            "Initial sync: 100/200 mails (50%)",
        )
        # A terminal status clears the in-progress line.
        self.store.set_success(5)
        self.assertEqual(self.store.status_snapshot()["fetch_progress"], "")
        self.store.set_progress("Syncing... 3 new so far")
        self.store.set_failure(RuntimeError("boom"))
        self.assertEqual(self.store.status_snapshot()["fetch_progress"], "")


class PasswordScanTests(unittest.TestCase):
    def setUp(self):
        self.store = _temp_store()
        self.store.add_mails([make_mail(id="A"), make_mail(id="B")])

    def test_derived_defaults_before_any_scan(self):
        m = self.store.snapshot()[0]
        self.assertEqual(m["_passwords"], [])
        self.assertFalse(m["_has_password"])

    def test_apply_sets_counts_and_clears(self):
        self.assertEqual(self.store.apply_password_scan({"A": ["hunter2"]}), 1)
        by_id = {m["id"]: m for m in self.store.snapshot()}
        self.assertEqual(by_id["A"]["_passwords"], ["hunter2"])
        self.assertTrue(by_id["A"]["_has_password"])
        self.assertFalse(by_id["B"]["_has_password"])
        # A later scan that no longer matches A clears the flag.
        self.assertEqual(self.store.apply_password_scan({}), 0)
        self.assertFalse({m["id"]: m for m in self.store.snapshot()}["A"]["_has_password"])

    def test_runtime_fields_are_not_persisted(self):
        self.store.apply_password_scan({"A": ["hunter2"]})
        self.store._save()
        reloaded = MailStore(self.store._cache_file)
        reloaded.load()
        m = {x["id"]: x for x in reloaded.snapshot()}["A"]
        self.assertEqual(m["_passwords"], [])      # re-derived default on load
        self.assertFalse(m["_has_password"])


def _temp_store():
    # Cache path inside a temp dir; the file need not exist yet.
    d = tempfile.mkdtemp()
    return MailStore(Path(d) / "cache.json")


if __name__ == "__main__":
    unittest.main()
