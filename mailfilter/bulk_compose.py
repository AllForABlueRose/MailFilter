"""The shared brain of Composer and Press: turn a row + a mail into a planned draft.

``plan_for_mail`` is the single path to a draft. Given one mail and the row data a
template needs, it (1) refuses the row if a variable the template reads has no value
(``missing row.ref``), (2) classifies the sender, (3) renders the reply body through
the template DSL, (4) resolves either an attachment from the file server or an FTP
link, and (5) computes the reply-all recipients with the drafting mailbox CC'd. The
result is a *plan* dict that Composer shows as a preview and ``draft_ops`` consumes
as a draft -- one function, so the two can never disagree.

The mail is always chosen by the user (Composer picks one, Press loads a worklist),
so nothing is matched. ``match_row_to_mails`` survives for the one case that still
needs it: binding an uploaded spreadsheet row that carries no EntryID back to a
loaded mail item, on a best-effort basis.

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


def _sender_context(mail, orgs, internal=None):
    """The ``sender.*`` half of a template's context.

    ``internal`` is the set of domains that count as internal -- the user's verified
    mailbox domain plus every Root/Partner org's member domains -- built once per
    request by ``customers.internal_domains`` and passed in, so this module stays pure
    and store-free. ``None`` means "nothing is known to be internal", which is what a
    caller with no orgs and no verified mailbox honestly has.
    """
    internal = internal or frozenset()
    name = str(mail.get("sender", "")).strip()
    email = str(mail.get("sender_email", "")).strip()
    domain = _domain_of(email)
    org = customers.resolve(email, orgs)
    return {
        "name": name,
        "first_name": name.split()[0] if name else "",
        "email": email,
        "domain": domain,
        "is_internal": bool(domain) and domain in internal,
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

def _reply_recipients(mail, cc_address=""):
    """Reply-all recipients: original sender -> To; original To+CC -> CC, with
    ``cc_address`` (the mailbox Press drafts from) added and the original sender
    removed from CC. A blank ``cc_address`` CCs no one.

    Indicative for the preview; in live mode draft_ops lets Outlook's ReplyAll
    populate these and only ensures ``cc_address`` is CC'd.
    """
    sender = str(mail.get("sender_email", "")).strip()
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
    cc_address = (cc_address or "").strip()
    if cc_address and cc_address.lower() not in seen:
        cc.append(cc_address)
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

def blank_plan(index, row):
    """A plan dict in its initial (blocked, nothing-resolved) state."""
    return {
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
        "warnings": [],
    }


def invalid_template_plan(index, row, template_error):
    """The plan a row gets when the template itself is stored broken: blocked,
    unrendered. A template that cannot parse blocks every row rather than
    half-rendering some of them."""
    plan = blank_plan(index, row)
    plan["warnings"].append(f"template is invalid: {template_error}")
    return plan


def row_for_mail(mail):
    """The sheet row a cache mail would have had.

    Mail in the cache never came from a spreadsheet, so synthesize the row: the
    known columns come from the mail itself and ``file_name`` from its first
    attachment. Everything a template asks for beyond these resolves to "" (the
    DSL's missing-key rule), exactly as an absent sheet column does -- and Press
    turns those blanks into a "missing row.<name>" failure rather than rendering
    a hole into a draft.
    """
    attachments = mail.get("attachments") or []
    first = attachments[0].get("filename", "") if attachments else ""
    return {
        "subject": str(mail.get("subject", "")),
        "datetime": str(mail.get("received", "")),
        "sender": str(mail.get("sender", "")),
        "file_name": str(first),
        "uses_ftp": "",
    }


def _template_vars(template, conditions):
    if not template:
        return []
    names = template_lang.variables(template.get("body", ""), "row",
                                    conditions=conditions)
    expr = (template.get("attachment_expr") or "").strip()
    if expr:
        names += [n for n in template_lang.variables(expr, "row", is_expression=True)
                  if n not in names]
    return names


def template_variables(template):
    """Every ``row.*`` name a template reads: body first, then attachment expression.

    The single source of Press's columns -- the Excel form's and the worklist's -- so
    the two always offer exactly the fields the chosen template can use.
    """
    return _template_vars(template, conditions=True)


def required_variables(template):
    """The ``row.*`` names whose value would be **printed** into the draft.

    A subset of :func:`template_variables`, and the distinction matters: a blank
    ``row.ref`` in ``{{ upper(row.ref) }}`` renders a *hole* in the reply, but a blank
    ``row.uses_ftp`` in ``{% if row.uses_ftp %}`` just means "no" -- a perfectly good
    answer. Only the former can block an item.
    """
    return _template_vars(template, conditions=False)


def missing_variables(template, row):
    """The required ``row.*`` names whose cell is blank, in template order.

    A template that prints ``row.ref`` against a row with no ``ref`` renders an empty
    string -- the DSL's missing-key rule -- which would silently ship a draft with a
    hole in it. Press calls this first and refuses the item instead.
    """
    return [name for name in required_variables(template)
            if not str(row.get(name, "")).strip()]


def plan_for_mail(index, row, mail, template, orgs, cc_address="", internal=None):
    """Plan ``row`` against an already-chosen ``mail`` (no matching).

    Recipients, subject, the {row, mail, sender} context, the body, and the
    attachment-or-FTP branch. Both callers reach a draft through this one function:
    Composer previews a template against a mail the user picked, and Press computes
    one against a mail item in its worklist -- neither has anything to match, so a
    preview and a draft cannot disagree.

    ``cc_address`` (the mailbox Press drafts from) is always CC'd; blank CCs no one.
    ``internal`` is the set of domains ``sender.is_internal`` is true for
    (``customers.internal_domains``), built once by the caller.
    """
    plan = blank_plan(index, row)
    warnings = plan["warnings"]
    plan["match_count"] = 1
    plan["mail_id"] = mail.get("id", "")
    plan["store_id"] = mail.get("store_id", "")
    plan["to"], plan["cc"] = _reply_recipients(mail, cc_address)
    plan["subject"] = _reply_subject(mail.get("subject", ""))

    # A blank cell for a variable the template needs is a hole in the draft, so it
    # blocks the row BEFORE rendering rather than rendering an empty string into it.
    for name in missing_variables(template, row):
        warnings.append(f"missing row.{name}")
    if warnings:
        return plan

    context = {"row": dict(row), "mail": _mail_context(mail),
               "sender": _sender_context(mail, orgs, internal)}

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


def match_row_to_mails(row, mails, tolerance=None):
    """Every mail item a spreadsheet row plausibly belongs to.

    Used when an uploaded sheet carries no EntryID column (the user filled in a form
    downloaded before any mail was loaded): fall back to the lenient
    subject + datetime(+/- tolerance) + sender match against the **loaded mail
    items**. A row matching 0 or 2+ items is reported unbound rather than guessed.
    """
    if tolerance is None:
        tolerance = config.BULK_MATCH_DATETIME_TOLERANCE_SECONDS
    return _find_matches(row, mails, tolerance)


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


# ``plan_all`` (the whole-sheet planner) and ``plan_row`` (match-then-plan against the
# shared inbox) are retired: Press now works mail-item by mail-item off the cache, so
# there is no sheet to plan wholesale and no inbox to match against. ``press.compute``
# drives ``plan_for_mail`` per item instead.
