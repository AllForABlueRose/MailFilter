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
import os
from datetime import datetime
from pathlib import Path

import config

from . import customers, outlook, util, workspace_manifest

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


def save_attachments(store, items, append_org_name=False, orgs=None,
                     resolve_customer=False, customer_mappings=None):
    """Save a batch of attachments into ``<WORKSPACE_DIR>/<YYYY-MM-DD>/``.

    ``items`` is ``[{"id": <mail id>, "index": <int>}, ...]``. Bytes are pulled
    from Outlook on demand, one at a time. Returns ``(folder, saved, errors)``;
    per-item failures are collected in ``errors`` rather than aborting the batch.
    The caller owns any tagging (the routes tag "downloaded"; so does the engine).

    A mail's organization comes from the **single shared resolver**
    (:func:`mailfilter.customers.mail_org_resolver`) — the same source behind the
    mail-list pill and the CSV report — with hierarchy Brute Force keyword >
    representative > sender member. Its brute-force tier is active only when
    ``resolve_customer`` is on (``customer_mappings`` = the Suspected Customers List);
    the sender tiers need ``orgs``. Both are off for the automation engine (no
    ``orgs``/``mappings`` → no org).

    The resolved org (real ``name``) is recorded — for **every** saved file, org
    fields blank when unresolved — in the folder's sidecar manifest
    (:mod:`mailfilter.workspace_manifest`), so org identity travels with the file
    (and survives later encryption) without touching the file's own bytes. A
    manifest entry's presence is also the "downloaded by this app" signal Cleanup
    keys off. ``append_org_name`` ("Append Customer Name To Downloads") gates only
    the **filename suffix** (``_<org name>``); a mail that resolves to no org (or with
    append off) still gets a manifest entry, just no suffix.

    Each saved file is also **stamped with its originating mail's received
    datetime** — both the file's own mtime (via :func:`os.utime`) and the manifest's
    ``received`` field — so the datetime travels with the file and the Unlock
    Station can later inherit it and pair keys newest->oldest.
    """
    folder = config.WORKSPACE_DIR / datetime.now().strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)

    resolve_org = customers.mail_org_resolver(
        orgs or [], customer_mappings if resolve_customer else None)
    by_id = {m["id"]: m for m in store.snapshot()}

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
        name = filename or att["filename"]
        mail = by_id.get(mail_id)
        org = resolve_org(mail) if mail is not None else None
        if append_org_name and org and org.get("name"):
            name = append_stem(name, org["name"])
        target = unique_path(folder, name, index)
        target.write_bytes(blob)
        # Stamp the file with its originating mail's received datetime (the derived
        # _received_dt is naive-local, so .timestamp() gives the right local epoch).
        received = (mail or {}).get("received", "")
        mail_dt = (mail or {}).get("_received_dt")
        if mail_dt is not None:
            ts = mail_dt.timestamp()
            os.utime(target, (ts, ts))
        # Record *every* download in the folder manifest (org fields blank when
        # unresolved), so "Cleanup Local Workspace" and the Unlock Station can tell
        # app files from incidental ones and read each file's org (real name) and
        # originating datetime without decoding its bytes (which fails once encrypted).
        workspace_manifest.record(str(folder), target.name, {
            "org_id": org.get("id") if org else "",
            "org_name": org.get("name", "") if org else "",
            "mail_id": mail_id,
            "received": received})
        saved.append({"id": mail_id, "index": index, "name": target.name})

    log.info("Saved %d attachment(s) to %s (%d error(s))", len(saved), folder, len(errors))
    return str(folder), saved, errors


def append_stem(filename, suffix):
    """Append ``_<suffix>`` to ``filename``'s stem, keeping the extension.

    ``report.pdf`` + ``Acme Corp`` -> ``report_Acme Corp.pdf``. The result is
    sanitized later by :func:`unique_path`, so an org name with path-unsafe
    characters can't escape the folder.
    """
    p = Path(filename)
    return f"{p.stem}_{suffix}{p.suffix}"


def write_report(store, ids, mappings=None, orgs=None):
    """Write a CSV report of the given mail ids into the dated workspace folder.

    Columns, left to right: ``Datetime, subject, recipient, sender, customer
    organization``. The last column is the mail's org from the **single shared
    resolver** (:func:`mailfilter.customers.mail_org_resolver`, real ``name``) — the
    same source as the pill and the download name — so it reflects Brute Force
    keyword > representative > sender member. ``mappings`` enables the brute-force
    tier (the caller passes them only when that experimental feature is on). Rows
    follow the order of ``ids``; unknown ids are skipped. Returns
    ``(folder, name, count)``.
    """
    by_id = {m["id"]: m for m in store.snapshot()}
    resolve_org = customers.mail_org_resolver(orgs or [], mappings)
    rows = []
    for mail_id in ids:
        mail = by_id.get(mail_id)
        if mail is None:
            continue
        org = resolve_org(mail)
        rows.append([
            mail.get("received", ""),
            mail.get("subject", ""),
            people_text(mail.get("recipient_names", []), mail.get("recipient_emails", [])),
            person_text(mail.get("sender", ""), mail.get("sender_email", "")),
            org.get("name", "") if org else "",
        ])

    now = datetime.now()
    folder = config.WORKSPACE_DIR / now.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    # A same-day re-export overwrites the file in place (fixed name, no _1 dedup).
    target = folder / util.safe_filename(f"report_{now.strftime('%Y-%m-%d')}.csv", "report")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Datetime", "subject", "recipient", "sender", "customer organization"])
    writer.writerows(rows)
    # utf-8-sig so Excel opens the file with the right encoding.
    target.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")

    log.info("Exported report of %d mail(s) to %s", len(rows), target)
    return str(folder), target.name, len(rows)


def cleanup_workspace():
    """Delete this app's downloaded files from **today's** workspace folder.

    Scans ``WORKSPACE_DIR/<YYYY-MM-DD>/`` and removes each regular file recorded in
    the folder's sidecar manifest (:func:`workspace_manifest.is_app_file`) — every
    download is recorded there, so incidental files a user placed in the folder are
    absent from the manifest and left in place. The manifest file itself is never
    reported or deleted as content; each deleted file is pruned from it (and the
    manifest is removed once emptied). Returns ``(folder, deleted, kept)`` (lists of
    file names); a missing folder yields empty lists. Never recurses into
    subdirectories.
    """
    folder = config.WORKSPACE_DIR / datetime.now().strftime("%Y-%m-%d")
    deleted, kept = [], []
    if not folder.is_dir():
        return str(folder), deleted, kept
    for entry in sorted(folder.iterdir()):
        if not entry.is_file() or entry.name == config.WORKSPACE_MANIFEST_NAME:
            continue
        if not workspace_manifest.is_app_file(str(folder), entry.name):
            kept.append(entry.name)
            continue
        try:
            entry.unlink()
            workspace_manifest.remove(str(folder), entry.name)
            deleted.append(entry.name)
        except OSError as e:
            log.warning("Cleanup: cannot delete %s: %s", entry, e)
            kept.append(entry.name)
    log.info("Cleanup removed %d app file(s) from %s (%d kept)", len(deleted), folder, len(kept))
    return str(folder), deleted, kept


def person_text(name, email):
    """Format one person as their display **name** by default.

    Falls back to the email when there is no name, or when the name is a
    (case-insensitive) continuous substring of the email address — in that case the
    "name" is just derived from the address, so the full email is more informative
    (e.g. ``alice`` / ``alice@x.com`` → the email; ``Alice Smith`` / ``alice@x.com``
    → the name). Email-only and name-only inputs return whichever exists.
    """
    name = (name or "").strip()
    email = (email or "").strip()
    if not name:
        return email
    if not email:
        return name
    if name.lower() in email.lower():
        return email
    return name


def people_text(names, emails):
    """Join paired name/email lists into ``person; person; ...`` for one CSV cell.

    Each person is formatted by :func:`person_text` (name by default, email when the
    name is a substring of it).
    """
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
