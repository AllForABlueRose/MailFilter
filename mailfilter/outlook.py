"""Outlook COM integration — the only module that touches pywin32.

pywin32 is imported lazily (see ``_connect``) so the rest of the app
imports and runs on machines without Outlook; a refresh there simply
reports "Outlook integration unavailable" in the status box while the
UI keeps serving cached mail.

Attachments are handled lazily: a fetch records only each attachment's
filename, and the bytes are pulled from Outlook on demand by
``fetch_attachment`` when the user clicks a download link.
"""

import logging
import os
import tempfile
import threading
from datetime import datetime

from config import (
    FETCH_BATCH_SIZE,
    FETCH_LOOKBACK,
    OUTLOOK_INBOX_FOLDER,
    RECEIVED_FORMAT,
)

from . import util

log = logging.getLogger(__name__)

_fetch_lock = threading.Lock()

# COM HRESULT for "Invalid class string": the ProgID is not registered,
# i.e. classic Outlook desktop is not installed on this machine.
CO_E_CLASSSTRING = -2147221005

# MAPI property tag for an AddressEntry's SMTP address (PR_SMTP_ADDRESS, unicode
# variant). Used to recover the real email when Exchange hands back a legacy
# X.500 DN ("/O=EXCHANGELABS/OU=...") instead of an SMTP address — see
# _smtp_from_address_entry. Covers distribution lists / contacts that have no
# ExchangeUser object.
PR_SMTP_ADDRESS = "http://schemas.microsoft.com/mapi/proptag/0x39FE001F"


class OutlookUnavailableError(RuntimeError):
    """Outlook cannot be reached on this machine (expected on dev boxes)."""


def start_async(store):
    """Initialize the Outlook Desktop integration in a background thread.

    Returns the started daemon thread. Used at server startup so the web
    server comes up immediately (serving cached mail) while Outlook is
    brought online out of band.
    """
    thread = threading.Thread(
        target=initialize,
        args=(store,),
        name="outlook-init",
        daemon=True,
    )
    thread.start()
    return thread


def initialize(store):
    """Bring up the Outlook Desktop integration at server startup.

    Connects to classic Outlook desktop via COM and pulls the latest mail
    into the store. Intended to run off the main thread (see
    :func:`start_async`) so startup is never blocked waiting on Outlook.

    On any failure the error has already been logged to the terminal by
    :func:`refresh`; here we add an explicit notice that the app is falling
    back to the on-disk mail cache, so the operator sees both lines.
    """
    log.info("Initializing Outlook Desktop in the background...")
    refresh(store)
    status = store.status_snapshot()
    if status["fetch_status"] == "Failed":
        log.warning(
            "Outlook initialization failed (%s) — falling back to the mail "
            "cache, serving %d message(s).",
            status["fetch_error"],
            len(store.snapshot()),
        )
    else:
        log.info("Outlook initialization complete — %s.", status["fetch_status"])


def refresh(store):
    """Fetch new mail into the store (incremental, from the high-water mark).

    Returns False (without touching the store) if another fetch is
    already running. The fetch outcome is reported via the store's
    status fields rather than raised.
    """
    return _run_sync(store, full=False)


def initial_sync(store):
    """Cold-start full inbox sync, used when no complete cache exists yet.

    Same contract as :func:`refresh` (returns False if another fetch holds the
    lock; reports outcome via the store's status), but ingests the whole inbox
    rather than only mail above the high-water mark. Driven by
    :mod:`mailfilter.bootstrap`, which owns the decision to run it and the
    in-progress marker that makes an interrupted sync resume on the next start.
    """
    return _run_sync(store, full=True)


def _run_sync(store, full):
    if not _fetch_lock.acquire(blocking=False):
        return False
    try:
        store.set_fetching()
        label = "Initial sync" if full else "Fetch"
        try:
            added = _sync(store, store.latest_received(), store.known_ids(), full)
            store.set_success(added)
            log.info("%s complete: %d new", label, added)
        except OutlookUnavailableError as e:
            # Expected on machines without Outlook — no traceback noise.
            store.set_failure(e)
            log.warning("%s", e)
        except Exception as e:
            store.set_failure(e)
            log.exception("%s failed", label)
        return True
    finally:
        _fetch_lock.release()


def _import_pywin32():
    """Import pywin32, or raise OutlookUnavailableError if it is missing."""
    try:
        import pythoncom
        import pywintypes
        import win32com.client
    except ImportError as e:
        raise OutlookUnavailableError(
            "Outlook integration unavailable on this machine "
            "(pywin32 not installed) — serving cached mail only"
        ) from e
    return pythoncom, pywintypes, win32com


def _dispatch(win32com, pywintypes):
    """Connect to the Outlook.Application COM object (caller holds CoInitialize)."""
    try:
        return win32com.client.gencache.EnsureDispatch("Outlook.Application")
    except pywintypes.com_error as e:
        hresult = getattr(e, "hresult", None)
        if hresult == CO_E_CLASSSTRING:
            reason = (
                "the Outlook.Application COM class is not registered — "
                "classic Outlook desktop is not installed "
                "(the 'new Outlook' app does not support COM automation)"
            )
        else:
            reason = f"COM error {hresult}: {e}"
        raise OutlookUnavailableError(
            f"Outlook is unavailable: {reason}"
        ) from e


def _sync(store, since, known_ids, full):
    """Connect to Outlook and stream new mail into ``store`` in batches.

    Shared by the incremental refresh (``full=False``) and the cold-start
    initial sync (``full=True``). Persisting per batch (rather than once at the
    end) means mail appears in the UI as it arrives, progress is reported through
    the store's status, and an interruption keeps the batches already written.
    Returns the number of mails added.

    A full sync skips the server-side ``Sort`` — it reads the whole folder
    regardless and the store sorts after each batch — and reads ``Items.Count``
    once for a progress denominator. There is no lookback early-stop on a full
    sync (``since`` is the empty cache's ``None``), so the entire folder is
    walked; dedup by EntryID makes a resumed full sync skip what is already
    cached.
    """
    pythoncom, pywintypes, win32com = _import_pywin32()
    pythoncom.CoInitialize()
    try:
        outlook = _dispatch(win32com, pywintypes)
        namespace = outlook.GetNamespace("MAPI")
        inbox = namespace.GetDefaultFolder(OUTLOOK_INBOX_FOLDER)
        items = inbox.Items
        total = None
        if full:
            try:
                total = int(items.Count)
            except Exception:
                total = None
        else:
            items.Sort("[ReceivedTime]", True)  # newest first: enables the lookback early-stop

        lookback = None if full else FETCH_LOOKBACK
        added = 0
        for batch in _iter_new_batches(items, since, known_ids, lookback, FETCH_BATCH_SIZE):
            added += store.add_mails(batch)
            text = _progress_text(added, total, full)
            store.set_progress(text)
            log.info(text)
        return added
    finally:
        pythoncom.CoUninitialize()


def _progress_text(added, total, full):
    """One-line progress for the status box / log (see MailStore.set_progress)."""
    if full and total:
        pct = int(added * 100 / total) if total else 0
        return f"Initial sync: {added:,}/{total:,} mails ({pct}%)"
    return f"Syncing... {added:,} new so far"


def _scan_cutoff(since, lookback):
    """Lowest ReceivedTime a refresh still scans, or ``None`` to scan everything.

    The fetch walks the folder newest-first and stops at this cutoff. Returning
    ``None`` (empty cache, or ``lookback`` disabled) means "no early stop" — the
    whole folder is scanned. Otherwise it is ``lookback`` below the high-water
    mark, so mail that landed just under the newest cached message is still
    re-examined (and then kept or skipped by EntryID). See docs/system-design.md §3.2.
    """
    if since is None or lookback is None:
        return None
    return since - lookback


def _iter_new_batches(items, since, known_ids, lookback, batch_size):
    """Yield lists (size <= ``batch_size``) of newly-parsed mails from a
    newest-first Outlook ``Items`` collection.

    Stops once a message falls below the lookback window (:func:`_scan_cutoff`);
    everything above the cutoff is deduplicated by EntryID, so out-of-order,
    moved-in, and same-second messages near the high-water mark are still picked
    up. ``batch_size`` of ``None`` (or 0) yields a single final batch. Free of COM
    *setup* (the caller owns CoInitialize/dispatch), so it can be exercised with
    fake item objects in tests.
    """
    cutoff = _scan_cutoff(since, lookback)
    batch = []
    for item in items:
        try:
            received = _to_naive_datetime(item.ReceivedTime)
        except Exception:
            log.warning("Skipping item without a readable ReceivedTime")
            continue
        # Items are sorted newest-first; once we drop below the lookback window
        # everything beyond is older still, so we can stop. Messages *within* the
        # window are not assumed new — EntryID dedup below decides that.
        if cutoff is not None and received < cutoff:
            break
        entry_id = str(item.EntryID)
        if entry_id in known_ids:
            continue
        try:
            batch.append(_parse_item(item, entry_id, received))
        except Exception:
            log.exception("Mail parse failed for %s", entry_id)
            continue
        if batch_size and len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def _collect_new(items, since, known_ids, lookback, batch_size=None):
    """All new mails as one list — a non-streaming convenience over
    :func:`_iter_new_batches` (used in tests)."""
    new_mails = []
    for batch in _iter_new_batches(items, since, known_ids, lookback, batch_size):
        new_mails.extend(batch)
    return new_mails


def _to_naive_datetime(com_time):
    """Convert an Outlook COM datetime into a timezone-naive local datetime."""
    return datetime(
        com_time.year,
        com_time.month,
        com_time.day,
        com_time.hour,
        com_time.minute,
        com_time.second,
    )


def _is_exchange_dn(address):
    """True if ``address`` is a legacy Exchange X.500 DN rather than an SMTP
    address. These start with "/O=" (or "/o=") and carry no "@"; an SMTP address
    never starts with "/". Internal Exchange / Microsoft 365 mail comes back as a
    DN, which is what we need to resolve to a real address."""
    return address.startswith("/")


def _smtp_from_address_entry(address_entry):
    """Best-effort SMTP address for an Outlook AddressEntry, or "" if none.

    For Exchange recipients the AddressEntry's ``.Address`` is a legacy X.500 DN,
    not an email address. Recover the real SMTP address via the Exchange user
    object first, then fall back to the PR_SMTP_ADDRESS MAPI property (which also
    covers distribution lists and contacts that expose no ExchangeUser). Every
    access is defensive: a single unreadable property must never abort the parse.
    """
    if address_entry is None:
        return ""
    try:
        exchange_user = address_entry.GetExchangeUser()
    except Exception:
        exchange_user = None
    if exchange_user is not None:
        try:
            smtp = str(exchange_user.PrimarySmtpAddress)
            if smtp:
                return smtp
        except Exception:
            pass
    try:
        smtp = str(address_entry.PropertyAccessor.GetProperty(PR_SMTP_ADDRESS))
        if smtp:
            return smtp
    except Exception:
        pass
    return ""


def _sender_email(item):
    """SMTP sender address, recovering it from Exchange when Outlook returns a
    legacy X.500 DN (``SenderEmailAddress`` for internal mail). Falls back to the
    raw value if resolution yields nothing."""
    try:
        address = str(item.SenderEmailAddress)
    except Exception:
        return ""
    if not _is_exchange_dn(address):
        return address
    try:
        smtp = _smtp_from_address_entry(item.Sender)
    except Exception:
        smtp = ""
    return smtp or address


def _recipient_email(recipient):
    """SMTP address for a Recipient, recovering it from Exchange (via its
    AddressEntry) when ``.Address`` is a legacy X.500 DN. Falls back to the raw
    value if resolution yields nothing."""
    try:
        address = str(recipient.Address)
    except Exception:
        return ""
    if not _is_exchange_dn(address):
        return address
    try:
        smtp = _smtp_from_address_entry(recipient.AddressEntry)
    except Exception:
        smtp = ""
    return smtp or address


def _parse_item(item, entry_id, received):
    sender_email = _sender_email(item)

    # Split recipients by type (olTo=1, olCC=2, olBCC=3). BCC is not exposed on
    # received mail, so it never appears here. Names and addresses are appended
    # together (with "" for a missing one) so the two lists stay index-aligned.
    recipient_names, recipient_emails = [], []
    cc_names, cc_emails = [], []
    try:
        for recipient in item.Recipients:
            try:
                rtype = int(recipient.Type)
            except Exception:
                rtype = 1  # treat as "To" if the type can't be read
            try:
                name = str(recipient.Name)
            except Exception:
                name = ""
            addr = _recipient_email(recipient)
            if rtype == 2:  # olCC
                cc_names.append(name)
                cc_emails.append(addr)
            elif rtype == 3:  # olBCC — skip (not present on received mail anyway)
                continue
            else:  # olTo or unknown
                recipient_names.append(name)
                recipient_emails.append(addr)
    except Exception:
        pass

    return {
        "id": entry_id,
        "subject": str(getattr(item, "Subject", "")),
        "sender": str(getattr(item, "SenderName", "")),
        "sender_email": sender_email,
        "recipient_names": recipient_names,
        "recipient_emails": recipient_emails,
        "cc_names": cc_names,
        "cc_emails": cc_emails,
        "body": str(getattr(item, "Body", "")),
        "received": received.strftime(RECEIVED_FORMAT),
        "conversation_id": str(getattr(item, "ConversationID", entry_id)),
        "attachments": _list_attachments(item),
    }


def _list_attachments(item):
    """Record each attachment's filename and type code (no bytes saved — see
    fetch_attachment).

    The order matches Outlook's collection, so a (mail id, index) pair is enough
    to pull the actual bytes later. Some attachment kinds — OLE / embedded
    objects in particular — do not support ``.FileName`` and raise a com_error on
    access ("Outlook cannot perform this action on this attachment format"), so a
    single such attachment must never abort the parse (and, during a batch sync,
    take the whole batch with it). We fall back ``FileName`` -> ``DisplayName`` ->
    a generic label, and record the type code so a later download can tell a
    non-savable format (notably OLE) apart from a normal file.
    """
    try:
        attachments = item.Attachments
        count = int(attachments.Count)
    except Exception:
        return []
    listed = []
    for i in range(1, count + 1):  # Outlook collections are 1-indexed.
        try:
            att = attachments.Item(i)
        except Exception:
            log.warning("Failed to read attachment %d", i)
            listed.append({"filename": f"attachment_{i}", "type": 0})
            continue
        listed.append({"filename": _attachment_name(att, i), "type": _attachment_type(att)})
    return listed


def _attachment_name(att, i):
    """Best-effort display name: ``FileName``, then ``DisplayName``, then a
    generic label. OLE / embedded attachments raise on ``.FileName`` but usually
    still expose a ``DisplayName`` (e.g. "Picture (Device Independent Bitmap)")."""
    for prop in ("FileName", "DisplayName"):
        try:
            name = str(getattr(att, prop))
        except Exception:
            continue
        if name:
            return name
    log.warning("Attachment %d has no readable name", i)
    return f"attachment_{i}"


def _attachment_type(att):
    """OlAttachmentType code (1=byValue, 4=byReference, 5=embedded item, 6=OLE),
    or 0 when it can't be read."""
    try:
        return int(att.Type)
    except Exception:
        return 0


def fetch_attachment(entry_id, index):
    """Lazily pull one attachment's bytes from Outlook by mail EntryID.

    ``index`` is the 0-based position in the mail's attachment list (as stored
    in the cache). Returns ``(filename, data)``. Raises OutlookUnavailableError
    if Outlook can't be reached, or LookupError if the mail or attachment no
    longer exists (e.g. it was deleted or moved since it was cached).

    Safe to call from a Flask request thread: it does its own
    CoInitialize/CoUninitialize.
    """
    pythoncom, pywintypes, win32com = _import_pywin32()
    pythoncom.CoInitialize()
    try:
        outlook = _dispatch(win32com, pywintypes)
        namespace = outlook.GetNamespace("MAPI")
        try:
            item = namespace.GetItemFromID(entry_id)
        except pywintypes.com_error as e:
            raise LookupError("mail no longer exists in Outlook") from e

        attachments = item.Attachments
        if index < 0 or index >= int(attachments.Count):
            raise LookupError("attachment index out of range")
        att = attachments.Item(index + 1)  # Outlook collections are 1-indexed.
        filename = str(att.FileName)

        # SaveAsFile needs a real path; write to a temp file, read it back,
        # then remove it so nothing is persisted to disk.
        tmp_dir = tempfile.mkdtemp(prefix="mailfilter_att_")
        tmp_path = os.path.join(
            tmp_dir, util.safe_filename(filename, f"attachment_{index}")
        )
        try:
            att.SaveAsFile(tmp_path)
            with open(tmp_path, "rb") as f:
                data = f.read()
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass
        return filename, data
    finally:
        pythoncom.CoUninitialize()
