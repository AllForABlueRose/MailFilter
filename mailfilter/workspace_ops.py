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

    Two independent, experimental ways to resolve a mail's **organization**; both
    are off for the automation engine:

    * ``append_org_name`` — "Append Customer Name To Downloads": the file's mail's
      **sender** resolves to an organization in ``orgs`` (representative-of
      preferred over base membership).
    * ``resolve_customer`` — "Brute Force Resolve Customer Name": one of the
      ``customer_mappings`` keywords (``[{"keyword", "org_id"}, ...]``) appears in
      the mail's content, mapping it to that organization (looked up in ``orgs``).

    Brute Force Resolve takes **priority**: when both resolve a mail, its keyword
    org overrides the sender org. The resolved org's name is appended to the file
    stem (``_<org name>``) and recorded — for **every** saved file, org fields
    blank when unresolved — in the folder's sidecar manifest
    (:mod:`mailfilter.workspace_manifest`), so org identity travels with the file
    (and survives later encryption) without touching the file's own bytes. A
    manifest entry's presence is also the "downloaded by this app" signal Cleanup
    keys off. A mail that resolves to no org still gets a (blank-org) manifest
    entry, but no filename suffix.
    """
    folder = config.WORKSPACE_DIR / datetime.now().strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)

    wanted = {(item or {}).get("id") for item in items}
    bruteforce = (_brute_force_org_matches(store, wanted, customer_mappings, orgs)
                  if resolve_customer else {})
    sender = _sender_org_matches(store, wanted, orgs) if append_org_name else {}

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
        org = bruteforce.get(mail_id) or sender.get(mail_id)
        if org and org["org_name"]:
            name = append_stem(name, org["org_name"])
        target = unique_path(folder, name, index)
        target.write_bytes(blob)
        # Record *every* download in the folder manifest (org fields blank when
        # unresolved), so "Cleanup Local Workspace" and the Unlock Station can tell
        # app files from incidental ones and read each file's org without decoding
        # its bytes (which fails once the file is encrypted).
        workspace_manifest.record(str(folder), target.name, {
            "org_id": org["org_id"] if org else "",
            "org_name": org["org_name"] if org else "",
            "mail_id": mail_id})
        saved.append({"id": mail_id, "index": index, "name": target.name})

    log.info("Saved %d attachment(s) to %s (%d error(s))", len(saved), folder, len(errors))
    return str(folder), saved, errors


def _sender_org_matches(store, wanted, orgs):
    """Map each wanted mail id to ``{org_id, org_name}`` for its sender's org.

    Resolves the sender on both axes (``customers.resolve``) and prefers the
    representative-of org over the base membership. Built once per batch so each
    mail resolves a single time regardless of how many attachments it has. Mails
    whose sender resolves to no org are absent.
    """
    by_id = {m["id"]: m for m in store.snapshot()}
    out = {}
    for mail_id in wanted:
        mail = by_id.get(mail_id)
        if mail is None:
            continue
        res = customers.resolve(mail.get("sender_email", ""), orgs or [])
        if res["rep_org_id"]:
            out[mail_id] = {"org_id": res["rep_org_id"], "org_name": res["rep_org_name"]}
        elif res["member_org_id"]:
            out[mail_id] = {"org_id": res["member_org_id"], "org_name": res["member_org_name"]}
    return out


def _brute_force_org_matches(store, wanted, mappings, orgs):
    """Map each wanted mail id to ``{org_id, org_name}`` via the keyword->org list.

    For every wanted mail, scan the mail's content (subject + body,
    case-insensitively) for the **first** ``mappings`` keyword (in list order) that
    appears, and resolve that mapping's ``org_id`` to a live org in ``orgs``. Mails
    with no keyword match — or whose matched keyword maps to an org that no longer
    exists — are absent. Shared by the download namer and the CSV report so the
    resolution rule lives in one place.
    """
    cleaned = [(str(m.get("keyword") or "").strip(), str(m.get("org_id") or ""))
               for m in (mappings or []) if str((m or {}).get("keyword") or "").strip()]
    if not cleaned:
        return {}
    orgs_by_id = {o.get("id"): o for o in (orgs or [])}
    by_id = {m["id"]: m for m in store.snapshot()}
    matches = {}
    for mail_id in wanted:
        mail = by_id.get(mail_id)
        if mail is None:
            continue
        content = (mail.get("subject", "") + "\n" + mail.get("body", "")).lower()
        for keyword, org_id in cleaned:
            if keyword.lower() in content:
                org = orgs_by_id.get(org_id)
                if org is not None:
                    matches[mail_id] = {"org_id": org.get("id"),
                                        "org_name": org.get("name", "")}
                break
    return matches


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
    organization``. The last column is the org resolved for each mail by the
    "Brute Force Resolve Customer Name" keyword->org ``mappings`` (looked up in
    ``orgs``), blank when no keyword matches. Rows follow the order of ``ids``;
    unknown ids are skipped. Returns ``(folder, name, count)``.
    """
    by_id = {m["id"]: m for m in store.snapshot()}
    org_by_id = _brute_force_org_matches(store, set(ids), mappings, orgs)
    rows = []
    for mail_id in ids:
        mail = by_id.get(mail_id)
        if mail is None:
            continue
        org = org_by_id.get(mail_id)
        rows.append([
            mail.get("received", ""),
            mail.get("subject", ""),
            people_text(mail.get("recipient_names", []), mail.get("recipient_emails", [])),
            person_text(mail.get("sender", ""), mail.get("sender_email", "")),
            org["org_name"] if org else "",
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
