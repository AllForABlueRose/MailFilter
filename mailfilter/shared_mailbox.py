"""Read the shared mailbox inbox to locate the mail a Bulk Compose row replies to.

Mail arrives in the shared mailbox and is forwarded to the personal inbox (which
the rest of the app already syncs). A spreadsheet row is matched against the
ORIGINAL sitting in the shared mailbox -- that original is the item
``draft_ops`` calls ``ReplyAll`` on -- so this module reads the shared inbox and
returns mail dicts shaped like the cache (``id``/``store_id``/``subject``/
``sender``/``sender_email``/``received``/``recipient_emails``/``cc_emails``).

``read_inbox()`` dispatches on ``config.BULK_MOCK_MODE``: the mock branch reads
``config.MOCK_SHARED_INBOX_FILE`` (a plain JSON list) so the whole pipeline runs
without Outlook; the live branch talks to Outlook over COM via the shared
mailbox's default Inbox (``GetSharedDefaultFolder``). Only the live branch
imports pywin32, and it does so through ``outlook`` (which imports it lazily), so
this module imports and runs on machines without Outlook.

This is a READ path -- it never mutates the mailbox. Dependency direction:
shared_mailbox -> outlook (COM helpers + SMTP resolution), config.
"""

import json
import logging
from pathlib import Path

import config

from . import outlook

log = logging.getLogger(__name__)


def read_inbox():
    """Shared-inbox mail (newest-first, capped) for row matching.

    Raises :class:`outlook.OutlookUnavailableError` in live mode when Outlook or
    the shared mailbox cannot be reached.
    """
    if config.BULK_MOCK_MODE:
        return _mock_read_inbox()
    return _com_read_inbox()


def _mock_read_inbox():
    path = Path(config.MOCK_SHARED_INBOX_FILE)
    if not path.exists():
        log.warning("Mock shared inbox %s not found; treating as empty", path)
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        log.warning("Could not read mock shared inbox %s: %s", path, e)
        return []
    if not isinstance(raw, list):
        return []
    return [_normalize_mock(m) for m in raw if isinstance(m, dict)][
        :config.BULK_SHARED_READ_LIMIT]


def _normalize_mock(mail):
    """Fill in the fields matching/draft creation expect, leaving the rest alone."""
    return {
        "id": str(mail.get("id", "")),
        "store_id": str(mail.get("store_id", "")),
        "subject": str(mail.get("subject", "")),
        "sender": str(mail.get("sender", "")),
        "sender_email": str(mail.get("sender_email", "")),
        "received": str(mail.get("received", "")),
        "recipient_emails": list(mail.get("recipient_emails", []) or []),
        "cc_emails": list(mail.get("cc_emails", []) or []),
    }


def _com_read_inbox():
    """Live COM read of the shared mailbox's Inbox. Untested off the Outlook host;
    verified there before BULK_MOCK_MODE is turned off."""
    pythoncom, pywintypes, win32com = outlook._import_pywin32()
    pythoncom.CoInitialize()
    try:
        app = outlook._dispatch(win32com, pywintypes)
        namespace = app.GetNamespace("MAPI")
        recipient = namespace.CreateRecipient(config.SHARED_MAILBOX_ADDRESS)
        recipient.Resolve()
        if not recipient.Resolved:
            raise outlook.OutlookUnavailableError(
                f"could not resolve shared mailbox {config.SHARED_MAILBOX_ADDRESS!r} "
                "(check the address and that you have access)"
            )
        inbox = namespace.GetSharedDefaultFolder(recipient, config.OUTLOOK_INBOX_FOLDER)
        store_id = str(getattr(inbox, "StoreID", ""))
        items = inbox.Items
        items.Sort("[ReceivedTime]", True)  # newest first

        mails = []
        for item in items:
            try:
                mails.append(_parse_shared_item(item, store_id))
            except Exception:
                log.exception("Skipping unreadable shared-inbox item")
                continue
            if len(mails) >= config.BULK_SHARED_READ_LIMIT:
                break
        return mails
    finally:
        pythoncom.CoUninitialize()


def _parse_shared_item(item, store_id):
    received = outlook._to_naive_datetime(item.ReceivedTime)
    recipient_emails, cc_emails = [], []
    try:
        for recipient in item.Recipients:
            try:
                rtype = int(recipient.Type)
            except Exception:
                rtype = 1
            addr = outlook._recipient_email(recipient)
            if rtype == 2:      # olCC
                cc_emails.append(addr)
            elif rtype == 3:    # olBCC
                continue
            else:               # olTo / unknown
                recipient_emails.append(addr)
    except Exception:
        pass
    return {
        "id": str(item.EntryID),
        "store_id": store_id,
        "subject": str(getattr(item, "Subject", "")),
        "sender": str(getattr(item, "SenderName", "")),
        "sender_email": outlook._sender_email(item),
        "received": received.strftime(config.RECEIVED_FORMAT),
        "recipient_emails": recipient_emails,
        "cc_emails": cc_emails,
    }
