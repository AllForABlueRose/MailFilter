"""Headless workspace operations shared by the HTTP routes and the automation
engine.

Both "download a batch of attachments" and "export a CSV report" write into a
dated subfolder of ``config.WORKSPACE_DIR``. Keeping the file/Outlook plumbing
here (rather than inline in ``routes.py``) lets the background automation engine
run the exact same operations a user triggers from the workspace tray, with no
Flask or duplication. Pure of Flask; depends only on store/outlook/util/config.
"""

import csv
import io
import logging
from datetime import datetime
from pathlib import Path

import config

from . import outlook, util

log = logging.getLogger(__name__)


def find_attachment(store, mail_id, index):
    """Look up a single stored attachment entry, or None if absent."""
    for mail in store.snapshot():
        if mail["id"] == mail_id:
            attachments = mail.get("attachments", [])
            if isinstance(index, int) and 0 <= index < len(attachments):
                return attachments[index]
            return None
    return None


def save_attachments(store, items):
    """Save a batch of attachments into ``<WORKSPACE_DIR>/<YYYY-MM-DD>/``.

    ``items`` is ``[{"id": <mail id>, "index": <int>}, ...]``. Bytes are pulled
    from Outlook on demand, one at a time. Returns ``(folder, saved, errors)``;
    per-item failures are collected in ``errors`` rather than aborting the batch.
    The caller owns any tagging (the routes tag "downloaded"; so does the engine).
    """
    folder = config.WORKSPACE_DIR / datetime.now().strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)

    saved, errors = [], []
    for item in items:
        mail_id = (item or {}).get("id")
        index = (item or {}).get("index")
        att = find_attachment(store, mail_id, index) if isinstance(index, int) else None
        if att is None:
            errors.append(f"{mail_id}#{index}: unknown attachment")
            continue
        try:
            filename, blob = outlook.fetch_attachment(mail_id, index)
        except outlook.OutlookUnavailableError as e:
            errors.append(str(e))
            continue
        except LookupError as e:
            errors.append(f"{mail_id}#{index}: {e}")
            continue
        target = unique_path(folder, filename or att["filename"], index)
        target.write_bytes(blob)
        saved.append({"id": mail_id, "index": index, "name": target.name})

    log.info("Saved %d attachment(s) to %s (%d error(s))", len(saved), folder, len(errors))
    return str(folder), saved, errors


def write_report(store, ids):
    """Write a CSV report of the given mail ids into the dated workspace folder.

    Columns, left to right: ``Datetime, subject, recipient, sender``. Rows follow
    the order of ``ids``; unknown ids are skipped. Returns ``(folder, name, count)``.
    """
    by_id = {m["id"]: m for m in store.snapshot()}
    rows = []
    for mail_id in ids:
        mail = by_id.get(mail_id)
        if mail is None:
            continue
        rows.append([
            mail.get("received", ""),
            mail.get("subject", ""),
            people_text(mail.get("recipient_names", []), mail.get("recipient_emails", [])),
            person_text(mail.get("sender", ""), mail.get("sender_email", "")),
        ])

    now = datetime.now()
    folder = config.WORKSPACE_DIR / now.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    target = unique_path(folder, f"report_{now.strftime('%Y-%m-%d')}.csv", 0)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Datetime", "subject", "recipient", "sender"])
    writer.writerows(rows)
    # utf-8-sig so Excel opens the file with the right encoding.
    target.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")

    log.info("Exported report of %d mail(s) to %s", len(rows), target)
    return str(folder), target.name, len(rows)


def person_text(name, email):
    """Format one person as ``Name <email>`` (falling back to whichever exists)."""
    name = (name or "").strip()
    email = (email or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return name or email


def people_text(names, emails):
    """Join paired name/email lists into ``Name <email>; ...`` for one CSV cell."""
    people = []
    for i in range(max(len(names), len(emails))):
        text = person_text(
            names[i] if i < len(names) else "",
            emails[i] if i < len(emails) else "",
        )
        if text:
            people.append(text)
    return "; ".join(people)


def unique_path(folder, filename, index):
    """A non-colliding path inside ``folder`` for a sanitized ``filename``."""
    safe = util.safe_filename(filename, f"attachment_{index}")
    candidate = folder / safe
    if not candidate.exists():
        return candidate
    stem, suffix = Path(safe).stem, Path(safe).suffix
    n = 1
    while (folder / f"{stem}_{n}{suffix}").exists():
        n += 1
    return folder / f"{stem}_{n}{suffix}"
