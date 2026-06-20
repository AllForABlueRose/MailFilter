"""Tests for mailfilter.outlook's graceful behaviour without pywin32.

The COM-touching paths can't be exercised off Windows, so these tests cover
the fallback contract: a refresh on a machine without Outlook must not crash —
it records a failure status and keeps serving the cache. The incremental-scan
selection logic (``_collect_new`` / ``_scan_cutoff``) is COM-free by design, so
it is exercised here with fake Outlook items.
"""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from mailfilter import outlook
from mailfilter.store import MailStore


class _FakeAttachments:
    Count = 0


class _FakeItem:
    """The minimal surface ``_collect_new`` / ``_parse_item`` read off an item."""

    def __init__(self, entry_id, received, subject="Subject"):
        self.EntryID = entry_id
        self.ReceivedTime = received  # a real datetime: _to_naive_datetime reads its fields
        self.Subject = subject
        self.SenderName = "Sender"
        self.SenderEmailAddress = "sender@example.com"
        self.Body = "body"
        self.ConversationID = "CONV"
        self.Recipients = []
        self.Attachments = _FakeAttachments()


class ScanCutoffTests(unittest.TestCase):
    def test_no_cutoff_for_empty_cache(self):
        self.assertIsNone(outlook._scan_cutoff(None, timedelta(days=7)))

    def test_no_cutoff_when_lookback_disabled(self):
        since = datetime(2026, 6, 10, 9, 0, 0)
        self.assertIsNone(outlook._scan_cutoff(since, None))

    def test_cutoff_is_lookback_below_high_water_mark(self):
        since = datetime(2026, 6, 10, 9, 0, 0)
        self.assertEqual(
            outlook._scan_cutoff(since, timedelta(days=7)),
            datetime(2026, 6, 3, 9, 0, 0),
        )


class CollectNewTests(unittest.TestCase):
    """The fix for the high-water-mark blind spots (#1 out-of-order/moved-in, #3
    same-second ties). Items are newest-first, as Outlook's Sort produces."""

    def _ids(self, mails):
        return [m["id"] for m in mails]

    def test_same_second_arrival_is_not_dropped_at_the_boundary(self):
        # A new message shares the exact second of the newest cached message.
        since = datetime(2026, 6, 10, 9, 0, 0)
        items = [
            _FakeItem("NEW", since),       # new, same second as high-water mark
            _FakeItem("CACHED", since),    # the message that set the high-water mark
        ]
        new = outlook._collect_new(items, since, {"CACHED"}, timedelta(days=7))
        self.assertEqual(self._ids(new), ["NEW"])

    def test_out_of_order_mail_below_high_water_mark_is_collected(self):
        # Delivered/synced/moved in with a timestamp under the newest cached one,
        # but inside the lookback window.
        since = datetime(2026, 6, 10, 9, 0, 0)
        older = since - timedelta(days=2)
        items = [_FakeItem("CACHED", since), _FakeItem("LATE", older)]
        new = outlook._collect_new(items, since, {"CACHED"}, timedelta(days=7))
        self.assertEqual(self._ids(new), ["LATE"])

    def test_stops_below_the_lookback_window(self):
        since = datetime(2026, 6, 10, 9, 0, 0)
        # Just outside the 7-day window: must be ignored (and stops the scan).
        ancient = since - timedelta(days=8)
        items = [_FakeItem("CACHED", since), _FakeItem("ANCIENT", ancient)]
        new = outlook._collect_new(items, since, {"CACHED"}, timedelta(days=7))
        self.assertEqual(new, [])

    def test_disabled_lookback_scans_whole_folder(self):
        since = datetime(2026, 6, 10, 9, 0, 0)
        ancient = since - timedelta(days=400)
        items = [_FakeItem("CACHED", since), _FakeItem("ANCIENT", ancient)]
        new = outlook._collect_new(items, since, {"CACHED"}, None)
        self.assertEqual(self._ids(new), ["ANCIENT"])

    def test_empty_cache_collects_everything(self):
        items = [
            _FakeItem("A", datetime(2026, 6, 10, 9, 0, 0)),
            _FakeItem("B", datetime(2025, 1, 1, 0, 0, 0)),
        ]
        new = outlook._collect_new(items, None, set(), timedelta(days=7))
        self.assertEqual(self._ids(new), ["A", "B"])


class IterNewBatchesTests(unittest.TestCase):
    """Batched streaming (B): mail is yielded in chunks so it can be persisted and
    reported progressively instead of all at the end."""

    def _items(self, n):
        # Newest-first, all within any reasonable window of the high-water mark.
        base = datetime(2026, 6, 10, 9, 0, 0)
        return [_FakeItem(f"ID{i}", base - timedelta(minutes=i)) for i in range(n)]

    def test_yields_in_batches_of_batch_size(self):
        batches = list(
            outlook._iter_new_batches(self._items(5), None, set(), None, batch_size=2)
        )
        self.assertEqual([len(b) for b in batches], [2, 2, 1])
        ids = [m["id"] for b in batches for m in b]
        self.assertEqual(ids, [f"ID{i}" for i in range(5)])

    def test_no_batch_size_yields_one_final_batch(self):
        batches = list(
            outlook._iter_new_batches(self._items(3), None, set(), None, batch_size=None)
        )
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 3)

    def test_known_ids_are_skipped_across_batches(self):
        items = self._items(4)  # ID0..ID3
        batches = list(
            outlook._iter_new_batches(items, None, {"ID1"}, None, batch_size=2)
        )
        ids = [m["id"] for b in batches for m in b]
        self.assertEqual(ids, ["ID0", "ID2", "ID3"])

    def test_no_new_mail_yields_nothing(self):
        batches = list(
            outlook._iter_new_batches(self._items(2), None, {"ID0", "ID1"}, None, 2)
        )
        self.assertEqual(batches, [])


class ProgressTextTests(unittest.TestCase):
    def test_full_sync_with_total_shows_percentage(self):
        self.assertEqual(
            outlook._progress_text(1200, 8003, full=True),
            "Initial sync: 1,200/8,003 mails (14%)",
        )

    def test_full_sync_without_total_falls_back(self):
        self.assertEqual(
            outlook._progress_text(50, None, full=True), "Syncing... 50 new so far"
        )

    def test_incremental_progress_text(self):
        self.assertEqual(
            outlook._progress_text(12, None, full=False), "Syncing... 12 new so far"
        )


class _Att:
    """A fake Outlook attachment; any prop set to _RAISE raises on access, as an
    OLE/embedded attachment does for FileName."""

    _RAISE = object()

    def __init__(self, file_name="", display_name="", atype=1):
        self._file_name = file_name
        self._display_name = display_name
        self._type = atype

    def _get(self, value, label):
        if value is _Att._RAISE:
            raise RuntimeError(f"Outlook cannot read {label} for this format")
        return value

    @property
    def FileName(self):
        return self._get(self._file_name, "FileName")

    @property
    def DisplayName(self):
        return self._get(self._display_name, "DisplayName")

    @property
    def Type(self):
        return self._get(self._type, "Type")


class _AttCollection:
    """A 1-indexed fake of Outlook's Attachments collection."""

    def __init__(self, atts):
        self._atts = atts
        self.Count = len(atts)

    def Item(self, i):
        return self._atts[i - 1]


class _ItemWithAtts:
    def __init__(self, atts):
        self.Attachments = _AttCollection(atts)


class ListAttachmentsTests(unittest.TestCase):
    """Issue 1: a com_error on one attachment (e.g. OLE/embedded) must not abort
    the parse; names fall back FileName -> DisplayName -> generic."""

    def test_normal_file_keeps_filename_and_type(self):
        listed = outlook._list_attachments(_ItemWithAtts([_Att("report.pdf", atype=1)]))
        self.assertEqual(listed, [{"filename": "report.pdf", "type": 1}])

    def test_ole_attachment_falls_back_to_displayname(self):
        ole = _Att(file_name=_Att._RAISE, display_name="Picture (DIB)", atype=6)
        listed = outlook._list_attachments(_ItemWithAtts([ole]))
        self.assertEqual(listed, [{"filename": "Picture (DIB)", "type": 6}])

    def test_unreadable_name_uses_generic_label(self):
        bad = _Att(file_name=_Att._RAISE, display_name=_Att._RAISE, atype=_Att._RAISE)
        listed = outlook._list_attachments(_ItemWithAtts([bad]))
        self.assertEqual(listed, [{"filename": "attachment_1", "type": 0}])

    def test_one_bad_attachment_does_not_drop_the_others(self):
        atts = [
            _Att("a.pdf", atype=1),
            _Att(file_name=_Att._RAISE, display_name=_Att._RAISE, atype=6),
            _Att("c.docx", atype=1),
        ]
        listed = outlook._list_attachments(_ItemWithAtts(atts))
        self.assertEqual(
            [a["filename"] for a in listed], ["a.pdf", "attachment_2", "c.docx"]
        )

    def test_unreadable_collection_returns_empty(self):
        class _Broken:
            @property
            def Attachments(self):
                raise RuntimeError("no attachments surface")

        self.assertEqual(outlook._list_attachments(_Broken()), [])


class _FakeExchangeUser:
    def __init__(self, smtp):
        self.PrimarySmtpAddress = smtp


class _FakePropertyAccessor:
    """Maps PR_SMTP_ADDRESS to a value; raises on any other tag, as Outlook does
    when the property is absent."""

    def __init__(self, smtp_by_tag):
        self._smtp_by_tag = smtp_by_tag

    def GetProperty(self, tag):
        if tag in self._smtp_by_tag:
            return self._smtp_by_tag[tag]
        raise RuntimeError("property not found")


class _FakeAddressEntry:
    def __init__(self, exchange_user=None, prop_smtp=None):
        self._exchange_user = exchange_user
        if prop_smtp is not None:
            self.PropertyAccessor = _FakePropertyAccessor(
                {outlook.PR_SMTP_ADDRESS: prop_smtp}
            )

    def GetExchangeUser(self):
        return self._exchange_user


class _FakeRecipient:
    def __init__(self, address, address_entry=None, name="R"):
        self.Address = address
        self.Name = name
        self.Type = 1
        if address_entry is not None:
            self.AddressEntry = address_entry


class _FakeSenderItem:
    def __init__(self, sender_address, sender=None):
        self.SenderEmailAddress = sender_address
        if sender is not None:
            self.Sender = sender


class ExchangeAddressResolutionTests(unittest.TestCase):
    """The Exchange-DN fix: legacy X.500 DNs ("/O=EXCHANGELABS/...") are resolved
    to real SMTP addresses; plain SMTP addresses pass through untouched."""

    EX_DN = "/O=EXCHANGELABS/OU=EXCHANGE ADMINISTRATIVE GROUP/CN=RECIPIENTS/CN=abc"

    def test_smtp_address_is_not_an_exchange_dn(self):
        self.assertFalse(outlook._is_exchange_dn("alice@example.com"))

    def test_legacy_dn_is_an_exchange_dn(self):
        self.assertTrue(outlook._is_exchange_dn(self.EX_DN))
        self.assertTrue(outlook._is_exchange_dn("/o=lowercase/cn=x"))

    def test_sender_plain_smtp_passes_through(self):
        item = _FakeSenderItem("alice@example.com")  # no Sender needed
        self.assertEqual(outlook._sender_email(item), "alice@example.com")

    def test_sender_dn_resolved_via_exchange_user(self):
        item = _FakeSenderItem(
            self.EX_DN, sender=_FakeAddressEntry(_FakeExchangeUser("bob@corp.com"))
        )
        self.assertEqual(outlook._sender_email(item), "bob@corp.com")

    def test_sender_dn_resolved_via_property_accessor_when_no_exchange_user(self):
        # Distribution lists have no ExchangeUser; PR_SMTP_ADDRESS still resolves.
        item = _FakeSenderItem(
            self.EX_DN, sender=_FakeAddressEntry(prop_smtp="list@corp.com")
        )
        self.assertEqual(outlook._sender_email(item), "list@corp.com")

    def test_sender_dn_falls_back_to_raw_when_unresolvable(self):
        item = _FakeSenderItem(self.EX_DN, sender=_FakeAddressEntry())
        self.assertEqual(outlook._sender_email(item), self.EX_DN)

    def test_recipient_plain_smtp_passes_through(self):
        rcpt = _FakeRecipient("carol@example.com")
        self.assertEqual(outlook._recipient_email(rcpt), "carol@example.com")

    def test_recipient_dn_resolved_via_exchange_user(self):
        rcpt = _FakeRecipient(
            self.EX_DN, _FakeAddressEntry(_FakeExchangeUser("dave@corp.com"))
        )
        self.assertEqual(outlook._recipient_email(rcpt), "dave@corp.com")

    def test_recipient_dn_falls_back_to_raw_when_unresolvable(self):
        rcpt = _FakeRecipient(self.EX_DN, _FakeAddressEntry())
        self.assertEqual(outlook._recipient_email(rcpt), self.EX_DN)


try:
    import win32com  # noqa: F401
    HAVE_PYWIN32 = True
except ImportError:
    HAVE_PYWIN32 = False


@unittest.skipIf(HAVE_PYWIN32, "pywin32 present; the unavailable-path tests don't apply")
class WithoutPywin32Tests(unittest.TestCase):
    def test_import_raises_outlook_unavailable(self):
        with self.assertRaises(outlook.OutlookUnavailableError):
            outlook._import_pywin32()

    def test_refresh_records_failure_without_crashing(self):
        store = MailStore(Path(tempfile.mkdtemp()) / "cache.json")
        self.assertTrue(outlook.refresh(store))  # ran (didn't skip)
        status = store.status_snapshot()
        self.assertEqual(status["fetch_status"], "Failed")
        self.assertTrue(status["fetch_error"])

    def test_fetch_attachment_raises_outlook_unavailable(self):
        with self.assertRaises(outlook.OutlookUnavailableError):
            outlook.fetch_attachment("any-id", 0)


class FetchLockTests(unittest.TestCase):
    def test_refresh_skips_when_a_fetch_is_already_running(self):
        store = MailStore(Path(tempfile.mkdtemp()) / "cache.json")
        acquired = outlook._fetch_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            self.assertIs(outlook.refresh(store), False)
            # Status untouched because the call returned before set_fetching().
            self.assertEqual(store.status_snapshot()["fetch_status"], "Not started")
        finally:
            outlook._fetch_lock.release()


if __name__ == "__main__":
    unittest.main()
