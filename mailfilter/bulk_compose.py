"""The brain of Bulk Compose: turn spreadsheet rows into planned reply drafts.

For each row it (1) matches the row to exactly one mail in the shared mailbox by
normalized subject + datetime (within tolerance) + sender, (2) classifies the
sender, (3) renders the reply body through the template DSL, (4) resolves either
an attachment from the file server or an FTP link, and (5) computes the
reply-all recipients with the shared mailbox CC'd. The result is a list of
*plan* dicts the preview table shows and the commit step (draft_ops) consumes.

Pure: stdlib + config + customers (classification) + template_lang (render). No
Flask, no COM, no mailbox mutation. The only side channel is reading the file
server to check that a named attachment exists -- a plain filesystem stat,
confined to ``config.FILE_SERVER_DIR``.

Dependency direction: bulk_compose -> customers, template_lang (-> config). It
never imports the stores, routes, or the COM modules.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

import config

from . import customers, template_lang

log = logging.getLogger(__name__)

_SUBJECT_PREFIX_RE = re.compile(r"^\s*(re|fw|fwd)\s*:\s*", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


# ----------------------------------------------------------------------------
# Matching helpers
# ----------------------------------------------------------------------------

def _normalize_subject(subject):
    """Strip leading RE:/FW:/FWD: prefixes (repeatedly) and collapse whitespace."""
    text = subject or ""
    while True:
        stripped = _SUBJECT_PREFIX_RE.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    return _WS_RE.sub(" ", text).strip().lower()


def _parse_dt(text):
    """Parse a RECEIVED_FORMAT or ISO datetime string, or None."""
    text = (text or "").strip()
    if not text:
        return None
    for parse in (lambda t: datetime.strptime(t, config.RECEIVED_FORMAT),
                  datetime.fromisoformat):
        try:
            return parse(text)
        except ValueError:
            continue
    return None


def _sender_matches(row_sender, mail):
    """Whether a row's sender text identifies ``mail``'s sender.

    Lenient: the (trimmed, lowercased) row value must appear within the mail's
    "name <email>" blob, or vice versa. A blank row sender matches anything (the
    criterion is simply skipped)."""
    rs = (row_sender or "").strip().lower()
    if not rs:
        return True
    blob = (str(mail.get("sender", "")) + " " + str(mail.get("sender_email", ""))).lower()
    return rs in blob or blob.strip() in rs


def _find_matches(row, shared_mails, tolerance):
    """Every shared mail matching the row on subject + datetime + sender."""
    want_subject = _normalize_subject(row.get("subject", ""))
    row_dt = _parse_dt(row.get("datetime", ""))
    row_sender = row.get("sender", "")

    matches = []
    for mail in shared_mails:
        if want_subject and _normalize_subject(mail.get("subject", "")) != want_subject:
            continue
        if row_dt is not None:
            mail_dt = _parse_dt(mail.get("received", ""))
            if mail_dt is None or abs((mail_dt - row_dt).total_seconds()) > tolerance:
                continue
        if not _sender_matches(row_sender, mail):
            continue
        matches.append(mail)
    return matches


# ----------------------------------------------------------------------------
# Sender / template context
# ----------------------------------------------------------------------------

def _domain_of(email):
    email = (email or "").strip().lower()
    return email.rsplit("@", 1)[-1] if "@" in email else ""


def _sender_context(mail, orgs):
    name = str(mail.get("sender", "")).strip()
    email = str(mail.get("sender_email", "")).strip()
    domain = _domain_of(email)
    org = customers.resolve(email, orgs)
    return {
        "name": name,
        "first_name": name.split()[0] if name else "",
        "email": email,
        "domain": domain,
        "is_internal": domain in config.INTERNAL_DOMAINS,
        "org": org["member_org_name"],
        "category": org["member_category"],
        "rep_org": org["rep_org_name"],
        "role": org["role"],
    }


def _mail_context(mail):
    return {
        "id": mail.get("id", ""),
        "subject": str(mail.get("subject", "")),
        "received": str(mail.get("received", "")),
        "sender": str(mail.get("sender", "")),
        "sender_email": str(mail.get("sender_email", "")),
    }


# ----------------------------------------------------------------------------
# Recipients (reply-all + shared CC) and attachment resolution
# ----------------------------------------------------------------------------

def _reply_recipients(mail):
    """Reply-all recipients: original sender -> To; original To+CC -> CC, with the
    shared mailbox always CC'd and the original sender removed from CC.

    Indicative for the preview; in live mode draft_ops lets Outlook's ReplyAll
    populate these and only ensures the shared mailbox is CC'd.
    """
    sender = str(mail.get("sender_email", "")).strip()
    shared = config.SHARED_MAILBOX_ADDRESS
    to = [sender] if sender else []

    seen = {e.lower() for e in to}
    cc = []
    for email in list(mail.get("recipient_emails", []) or []) + list(mail.get("cc_emails", []) or []):
        email = (email or "").strip()
        low = email.lower()
        if not email or low in seen:
            continue
        seen.add(low)
        cc.append(email)
    if shared.lower() not in seen:
        cc.append(shared)
    return to, cc


def resolve_attachment_path(filename):
    """Resolve ``filename`` under ``config.FILE_SERVER_DIR``.

    Returns ``(path, exists, error)``. ``error`` is non-empty when the name is
    blank or escapes the file-server root (traversal); ``path`` is the absolute
    string path otherwise and ``exists`` reflects a real file on disk.
    """
    name = (filename or "").strip()
    if not name:
        return "", False, "no file name"
    root = Path(config.FILE_SERVER_DIR).resolve()
    candidate = (root / name).resolve()
    if candidate != root and root not in candidate.parents:
        return "", False, "file path escapes the file server root"
    return str(candidate), candidate.is_file(), ""


# ----------------------------------------------------------------------------
# Planning
# ----------------------------------------------------------------------------

def plan_row(index, row, shared_mails, template, orgs, tolerance):
    """Build one plan dict for ``row`` (see module docstring for the shape)."""
    warnings = []
    plan = {
        "row_index": index,
        "status": "blocked",
        "mail_id": "",
        "to": [],
        "cc": [],
        "subject": "",
        "body": "",
        "uses_ftp": template_lang.truthy(row.get("uses_ftp", "")),
        "ftp_link": "",
        "attachment": None,
        "match_count": 0,
        "warnings": warnings,
    }

    matches = _find_matches(row, shared_mails, tolerance)
    plan["match_count"] = len(matches)
    if not matches:
        warnings.append("no matching mail found in the shared mailbox")
        return plan
    if len(matches) > 1:
        warnings.append(f"{len(matches)} mails match this row; refine subject/datetime/sender")
        return plan

    mail = matches[0]
    plan["mail_id"] = mail.get("id", "")
    plan["store_id"] = mail.get("store_id", "")
    to, cc = _reply_recipients(mail)
    plan["to"], plan["cc"] = to, cc
    plan["subject"] = _reply_subject(mail.get("subject", ""))

    context = {"row": dict(row), "mail": _mail_context(mail),
               "sender": _sender_context(mail, orgs)}

    try:
        plan["body"] = template_lang.render(template.get("body", ""), context)
    except template_lang.TemplateError as e:
        warnings.append(f"template error: {e}")
        return plan

    if plan["uses_ftp"]:
        _plan_ftp(plan, row, template, context, warnings)
    else:
        _plan_attachment(plan, row, template, context, warnings)

    if not warnings:
        plan["status"] = "ready"
    return plan


def _reply_subject(subject):
    subject = str(subject or "")
    return subject if _SUBJECT_PREFIX_RE.match(subject) else f"RE: {subject}"


def _resolve_filename(row, template, context, warnings):
    """The attachment filename: the template's attachment_expr if set, else the
    row's file_name column. Returns "" (and records a warning) on a DSL error."""
    expr = (template.get("attachment_expr") or "").strip()
    if not expr:
        return str(row.get("file_name", "")).strip()
    try:
        return template_lang.stringify(template_lang.eval_expr(expr, context)).strip()
    except template_lang.TemplateError as e:
        warnings.append(f"attachment name error: {e}")
        return ""


def _plan_attachment(plan, row, template, context, warnings):
    filename = _resolve_filename(row, template, context, warnings)
    if not filename:
        if not any("attachment name error" in w for w in warnings):
            warnings.append("no file name to attach")
        return
    path, exists, error = resolve_attachment_path(filename)
    plan["attachment"] = {"name": filename, "path": path, "exists": exists}
    if error:
        warnings.append(error)
    elif not exists:
        warnings.append(f"file not found on the file server: {filename}")


def _plan_ftp(plan, row, template, context, warnings):
    """FTP row: no attachment; surface the link the body should already contain."""
    filename = _resolve_filename(row, template, context, warnings)
    if filename:
        plan["ftp_link"] = config.FTP_LINK_BASE + filename
    else:
        plan["ftp_link"] = ""
        if not any("attachment name error" in w for w in warnings):
            warnings.append("FTP row has no file name for the link")


def plan_all(rows, shared_mails, template, orgs, tolerance=None):
    """Plan every row. ``template`` carrying a stored ``error`` blocks the whole run.

    Returns ``{"plans": [...], "summary": {total, ready, blocked}}``.
    """
    if tolerance is None:
        tolerance = config.BULK_MATCH_DATETIME_TOLERANCE_SECONDS
    template = template or {}
    template_error = template.get("error") or ""

    plans = []
    for i, row in enumerate(rows):
        if template_error:
            plans.append({
                "row_index": i, "status": "blocked", "mail_id": "", "to": [], "cc": [],
                "subject": "", "body": "", "uses_ftp": False, "ftp_link": "",
                "attachment": None, "match_count": 0,
                "warnings": [f"template is invalid: {template_error}"],
            })
            continue
        plans.append(plan_row(i, row, shared_mails, template, orgs, tolerance))

    ready = sum(1 for p in plans if p["status"] == "ready")
    return {
        "plans": plans,
        "summary": {"total": len(plans), "ready": ready, "blocked": len(plans) - ready},
    }
