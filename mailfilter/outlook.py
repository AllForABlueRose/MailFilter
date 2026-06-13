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

from config import OUTLOOK_INBOX_FOLDER, RECEIVED_FORMAT

from . import util

log = logging.getLogger(__name__)

_fetch_lock = threading.Lock()

# COM HRESULT for "Invalid class string": the ProgID is not registered,
# i.e. classic Outlook desktop is not installed on this machine.
CO_E_CLASSSTRING = -2147221005


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
    """Fetch new mail into the store.

    Returns False (without touching the store) if another fetch is
    already running. The fetch outcome is reported via the store's
    status fields rather than raised.
    """
    if not _fetch_lock.acquire(blocking=False):
        return False
    try:
        store.set_fetching()
        try:
            new_mails = _fetch_new(store.latest_received(), store.known_ids())
            added = store.add_mails(new_mails)
            store.set_success(added)
            log.info("Fetch complete: %d new", added)
        except OutlookUnavailableError as e:
            # Expected on machines without Outlook — no traceback noise.
            store.set_failure(e)
            log.warning("%s", e)
        except Exception as e:
            store.set_failure(e)
            log.exception("Fetch failed")
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


def _fetch_new(since, known_ids):
    pythoncom, pywintypes, win32com = _import_pywin32()
    pythoncom.CoInitialize()
    try:
        outlook = _dispatch(win32com, pywintypes)
        namespace = outlook.GetNamespace("MAPI")
        inbox = namespace.GetDefaultFolder(OUTLOOK_INBOX_FOLDER)
        items = inbox.Items
        items.Sort("[ReceivedTime]", True)  # newest first

        new_mails = []
        for item in items:
            try:
                received = _to_naive_datetime(item.ReceivedTime)
            except Exception:
                log.warning("Skipping item without a readable ReceivedTime")
                continue
            # Items are sorted newest-first, so everything from here on
            # is older than what we already have.
            if since is not None and received <= since:
                break
            entry_id = str(item.EntryID)
            if entry_id in known_ids:
                continue
            try:
                new_mails.append(_parse_item(item, entry_id, received))
            except Exception:
                log.exception("Mail parse failed for %s", entry_id)
        return new_mails
    finally:
        pythoncom.CoUninitialize()


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


def _parse_item(item, entry_id, received):
    sender_email = ""
    try:
        sender_email = str(item.SenderEmailAddress)
    except Exception:
        pass

    recipient_names = []
    recipient_emails = []
    try:
        for recipient in item.Recipients:
            try:
                recipient_names.append(str(recipient.Name))
            except Exception:
                pass
            try:
                recipient_emails.append(str(recipient.Address))
            except Exception:
                pass
    except Exception:
        pass

    return {
        "id": entry_id,
        "subject": str(getattr(item, "Subject", "")),
        "sender": str(getattr(item, "SenderName", "")),
        "sender_email": sender_email,
        "recipient_names": recipient_names,
        "recipient_emails": recipient_emails,
        "body": str(getattr(item, "Body", "")),
        "received": received.strftime(RECEIVED_FORMAT),
        "conversation_id": str(getattr(item, "ConversationID", entry_id)),
        "attachments": _list_attachments(item),
    }


def _list_attachments(item):
    """Record each attachment's filename (no bytes saved — see fetch_attachment).

    The order matches Outlook's collection, so a (mail id, index) pair is
    enough to pull the actual bytes later.
    """
    try:
        attachments = item.Attachments
        count = int(attachments.Count)
    except Exception:
        return []
    listed = []
    for i in range(1, count + 1):  # Outlook collections are 1-indexed.
        try:
            listed.append({"filename": str(attachments.Item(i).FileName)})
        except Exception:
            log.exception("Failed to read attachment %d", i)
            listed.append({"filename": f"attachment_{i}"})
    return listed


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
