"""Outlook COM integration — the only module that touches pywin32.

pywin32 is imported lazily inside ``_fetch_new`` so the rest of the app
imports and runs on machines without Outlook; a refresh there simply
reports "Outlook integration unavailable" in the status box while the
UI keeps serving cached mail.
"""

import logging
import threading
from datetime import datetime

from config import OUTLOOK_INBOX_FOLDER, RECEIVED_FORMAT

log = logging.getLogger(__name__)

_fetch_lock = threading.Lock()

# COM HRESULT for "Invalid class string": the ProgID is not registered,
# i.e. classic Outlook desktop is not installed on this machine.
CO_E_CLASSSTRING = -2147221005


class OutlookUnavailableError(RuntimeError):
    """Outlook cannot be reached on this machine (expected on dev boxes)."""


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


def _fetch_new(since, known_ids):
    try:
        import pythoncom
        import pywintypes
        import win32com.client
    except ImportError as e:
        raise OutlookUnavailableError(
            "Outlook integration unavailable on this machine "
            "(pywin32 not installed) — serving cached mail only"
        ) from e

    pythoncom.CoInitialize()
    try:
        try:
            outlook = win32com.client.gencache.EnsureDispatch(
                "Outlook.Application"
            )
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
                f"Outlook is unavailable: {reason} — serving cached mail only"
            ) from e
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
    }
