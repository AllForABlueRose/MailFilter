"""The read-side of Composer: the template workbench's sample data and pickers.

Composer is Press's authoring half pulled out into its own view. It renders a
reply template against a mail the user picked -- one of the built-in SAMPLES, or
a real mail out of the cache -- and shows the result. It writes nothing: no
draft, no audit log, no cache mutation. The actual render is delegated to
``bulk_compose.plan_for_mail``, the same call Press's draft path makes, so a
Composer preview cannot disagree with what Press would draft.

Three catalogues live here:

* ``SAMPLES``  -- 10 example mails *and their sheet row*, spread across the
  branches a template author needs to feel out (attached file vs FTP link,
  internal vs external sender, a sender who resolves to an organization vs one
  who doesn't, a missing file on the file server, a blank sender name).
* ``BLOCKS``   -- 10 draggable function blocks. A block *is* its snippet, and
  ``render_blocks`` renders that snippet through ``template_lang`` against
  ``DEMO_CONTEXT`` -- so the input/output the palette advertises is produced by
  the real DSL and cannot drift from it.
* ``FILTERS``  -- the cache-mail picker's emoji filters. Predicates over fields
  the cache already derives (``_has_attachments``/``_has_links``/
  ``_has_password``) plus the org/tag lookups passed in. This is a *picker*, not
  a second copy of ``filters.py`` (which owns the search-query language).

Pure: stdlib + config + bulk_compose + template_lang. No Flask, no COM, and no
store import -- snapshots and resolvers are passed in, the same no-cycle shape as
``customers.py`` and ``automation.py``.
"""

import config

from . import bulk_compose, template_lang


# ----------------------------------------------------------------------------
# Example mails + the sheet data assigned to them
# ----------------------------------------------------------------------------
# Each sample is {id, emoji, label, note, mail, row}. ``mail`` is shaped exactly
# like a cache / shared-inbox mail so it drops straight into the planner; ``row``
# is a normalized sheet row (the config.BULK_COLUMNS aliases, plus free columns a
# real sheet would carry). The two file names below are the real contents of
# config.FILE_SERVER_DIR, so those samples genuinely resolve an attachment.

_FILE_PDF = "Invoice_ACME_2026Q2.pdf"
_FILE_ZIP = "Orion_Drawings_Rev3.zip"

SAMPLES = [
    {
        "id": "sample-attached",
        "emoji": "📎",
        "label": "Attached file, external customer",
        "note": "The plain case: an outside sender, a file that exists on the file "
                "server. sender.org resolves if this domain is mapped to an organization.",
        "mail": {
            "id": "sample-attached",
            "subject": "Invoice for Q2",
            "sender": "Kenji Sato",
            "sender_email": "kenji.sato@acme.co.jp",
            "received": "2026-06-30 09:15:00",
            "recipient_emails": ["sales@example.com"],
            "cc_emails": ["accounts@acme.co.jp"],
            "body": "Could you send us the Q2 invoice at your convenience?",
            "attachments": [],
        },
        "row": {
            "subject": "Invoice for Q2",
            "datetime": "2026-06-30 09:15:00",
            "sender": "Kenji Sato",
            "file_name": _FILE_PDF,
            "uses_ftp": "",
            "ref": "acme-1042",
            "qty": "1",
        },
    },
    {
        "id": "sample-ftp",
        "emoji": "🔗",
        "label": "FTP link instead of an attachment",
        "note": "uses_ftp is truthy, so no file is attached -- the body must carry "
                "ftp_link(row.file_name) instead. This is the branch {% if row.uses_ftp %} exists for.",
        "mail": {
            "id": "sample-ftp",
            "subject": "Orion drawings - latest revision",
            "sender": "Priya Nair",
            "sender_email": "priya.nair@orion-eng.com",
            "received": "2026-06-28 14:02:00",
            "recipient_emails": ["drawings@example.com"],
            "cc_emails": [],
            "body": "The file was too big for mail last time. FTP is fine on our side.",
            "attachments": [],
        },
        "row": {
            "subject": "Orion drawings - latest revision",
            "datetime": "2026-06-28 14:02:00",
            "sender": "Priya Nair",
            "file_name": _FILE_ZIP,
            "uses_ftp": "yes",
            "ref": "orion-rev3",
            "qty": "1",
        },
    },
    {
        "id": "sample-internal",
        "emoji": "🏠",
        "label": "Internal colleague",
        "note": "The sender's domain is in config.INTERNAL_DOMAINS, so sender.is_internal "
                "is true -- the hook for a less formal register.",
        "mail": {
            "id": "sample-internal",
            "subject": "Reissue the June statement",
            "sender": "Marie Dubois",
            "sender_email": "marie.dubois@example.com",
            "received": "2026-06-29 11:40:00",
            "recipient_emails": ["billing@example.com"],
            "cc_emails": [],
            "body": "Can you reissue this one with the corrected address?",
            "attachments": [],
        },
        "row": {
            "subject": "Reissue the June statement",
            "datetime": "2026-06-29 11:40:00",
            "sender": "Marie Dubois",
            "file_name": _FILE_PDF,
            "uses_ftp": "",
            "ref": "int-0007",
            "qty": "1",
        },
    },
    {
        "id": "sample-missing-file",
        "emoji": "⚠️",
        "label": "File is not on the file server",
        "note": "The row names a file that does not exist under config.FILE_SERVER_DIR. "
                "The body still renders, but the plan is BLOCKED -- Press would refuse this row.",
        "mail": {
            "id": "sample-missing-file",
            "subject": "Shipping documents",
            "sender": "Tom Becker",
            "sender_email": "t.becker@nordwind.de",
            "received": "2026-06-27 16:20:00",
            "recipient_emails": ["logistics@example.com"],
            "cc_emails": [],
            "body": "Please send the shipping documents for the June container.",
            "attachments": [],
        },
        "row": {
            "subject": "Shipping documents",
            "datetime": "2026-06-27 16:20:00",
            "sender": "Tom Becker",
            "file_name": "Shipping_June_2026.pdf",
            "uses_ftp": "",
            "ref": "nw-3311",
            "qty": "2",
        },
    },
    {
        "id": "sample-no-name",
        "emoji": "👤",
        "label": "Sender has no display name",
        "note": "sender.first_name is empty, so a bare {{ sender.first_name }} greeting "
                "reads as 'Dear ,'. Wrap it: default(sender.first_name, \"Sir/Madam\").",
        "mail": {
            "id": "sample-no-name",
            "subject": "Copy of last month's invoice",
            "sender": "",
            "sender_email": "purchasing@fieldstone.co.uk",
            "received": "2026-06-26 08:05:00",
            "recipient_emails": ["sales@example.com"],
            "cc_emails": [],
            "body": "We seem to have lost our copy. Could you resend it?",
            "attachments": [],
        },
        "row": {
            "subject": "Copy of last month's invoice",
            "datetime": "2026-06-26 08:05:00",
            "sender": "purchasing@fieldstone.co.uk",
            "file_name": _FILE_PDF,
            "uses_ftp": "",
            "ref": "fs-0891",
            "qty": "1",
        },
    },
    {
        "id": "sample-reply",
        "emoji": "↩️",
        "label": "Already a reply (RE: subject)",
        "note": "The subject already carries RE:, so the planned reply subject is left "
                "alone rather than becoming 'RE: RE: ...'.",
        "mail": {
            "id": "sample-reply",
            "subject": "RE: Purchase order 88120",
            "sender": "Lars Petersen",
            "sender_email": "lars@havlund.dk",
            "received": "2026-06-25 13:31:00",
            "recipient_emails": ["orders@example.com"],
            "cc_emails": ["lars.assistant@havlund.dk"],
            "body": "Thanks -- one more question about the delivery window.",
            "attachments": [],
        },
        "row": {
            "subject": "RE: Purchase order 88120",
            "datetime": "2026-06-25 13:31:00",
            "sender": "Lars Petersen",
            "file_name": _FILE_PDF,
            "uses_ftp": "",
            "ref": "hv-88120",
            "qty": "4",
        },
    },
    {
        "id": "sample-many-cc",
        "emoji": "👥",
        "label": "Long CC list (reply-all)",
        "note": "Everyone on To + CC lands in the reply's Cc, the original sender in To, "
                "and the shared mailbox is always added. Watch the Recipients line.",
        "mail": {
            "id": "sample-many-cc",
            "subject": "Kickoff materials for the Meridian project",
            "sender": "Sofia Rossi",
            "sender_email": "s.rossi@meridian-group.it",
            "received": "2026-06-24 10:00:00",
            "recipient_emails": ["projects@example.com", "pm@example.com"],
            "cc_emails": ["legal@meridian-group.it", "finance@meridian-group.it",
                          "a.conti@meridian-group.it"],
            "body": "Sharing the kickoff pack with the wider group.",
            "attachments": [],
        },
        "row": {
            "subject": "Kickoff materials for the Meridian project",
            "datetime": "2026-06-24 10:00:00",
            "sender": "Sofia Rossi",
            "file_name": _FILE_ZIP,
            "uses_ftp": "",
            "ref": "mrd-0001",
            "qty": "1",
        },
    },
    {
        "id": "sample-columns",
        "emoji": "🧮",
        "label": "Extra spreadsheet columns",
        "note": "Every column of the sheet reaches the template under its normalized "
                "header -- not just the known ones. Here: row.ref, row.qty, row.due.",
        "mail": {
            "id": "sample-columns",
            "subject": "Quote request - 12 units",
            "sender": "Ahmed Farouk",
            "sender_email": "ahmed.farouk@delta-trading.ae",
            "received": "2026-06-23 15:47:00",
            "recipient_emails": ["quotes@example.com"],
            "cc_emails": [],
            "body": "Please quote 12 units with delivery before the end of July.",
            "attachments": [],
        },
        "row": {
            "subject": "Quote request - 12 units",
            "datetime": "2026-06-23 15:47:00",
            "sender": "Ahmed Farouk",
            "file_name": _FILE_PDF,
            "uses_ftp": "",
            "ref": "dt-5560",
            "qty": "12",
            "due": "2026-07-31",
        },
    },
    {
        "id": "sample-ftp-truthy",
        "emoji": "✔️",
        "label": "Truthiness of the FTP flag",
        "note": "uses_ftp reads 'TRUE' here. Truthy is anything outside "
                "'', 0, false, no, n, f, none, off (case-insensitive) -- so 'TRUE', 'y' and "
                "'1' all take the FTP branch.",
        "mail": {
            "id": "sample-ftp-truthy",
            "subject": "Rev 3 drawings please",
            "sender": "Grace Okonkwo",
            "sender_email": "g.okonkwo@orion-eng.com",
            "received": "2026-06-22 09:12:00",
            "recipient_emails": ["drawings@example.com"],
            "cc_emails": [],
            "body": "Same as last time -- FTP is easiest.",
            "attachments": [],
        },
        "row": {
            "subject": "Rev 3 drawings please",
            "datetime": "2026-06-22 09:12:00",
            "sender": "Grace Okonkwo",
            "file_name": _FILE_ZIP,
            "uses_ftp": "TRUE",
            "ref": "orion-rev3",
            "qty": "1",
        },
    },
    {
        "id": "sample-no-file",
        "emoji": "🚫",
        "label": "Row with no file name at all",
        "note": "Neither an attachment nor an FTP link can be resolved, so the plan is "
                "BLOCKED. Set the template's attachment expression to name the file instead, "
                "e.g. upper(row.ref) + \".pdf\".",
        "mail": {
            "id": "sample-no-file",
            "subject": "Certificate of conformity",
            "sender": "Yuki Tanaka",
            "sender_email": "yuki.tanaka@acme.co.jp",
            "received": "2026-06-21 17:55:00",
            "recipient_emails": ["quality@example.com"],
            "cc_emails": [],
            "body": "Do you have the certificate of conformity for this batch?",
            "attachments": [],
        },
        "row": {
            "subject": "Certificate of conformity",
            "datetime": "2026-06-21 17:55:00",
            "sender": "Yuki Tanaka",
            "file_name": "",
            "uses_ftp": "",
            "ref": "acme-2210",
            "qty": "1",
        },
    },
]


def sample(sample_id):
    """The sample with ``sample_id``, or None."""
    for item in SAMPLES:
        if item["id"] == sample_id:
            return item
    return None


# ----------------------------------------------------------------------------
# Function blocks (the draggable palette)
# ----------------------------------------------------------------------------
# One demo context feeds every block, so the palette reads as one worked example.
# A block IS its snippet: what the palette shows you is exactly what dropping it
# inserts, and the output beside it is that snippet rendered through the real DSL.

DEMO_CONTEXT = {
    "row": {
        "subject": "Invoice for Q2",
        "datetime": "2026-06-30 09:15:00",
        "sender": "Kenji Sato",
        "file_name": _FILE_PDF,
        "uses_ftp": "yes",
        "ref": "acme-1042",
        "qty": "3",
    },
    "mail": {
        "id": "demo",
        "subject": "Invoice for Q2",
        "received": "2026-06-30 09:15:00",
        "sender": "Kenji Sato",
        "sender_email": "kenji.sato@acme.co.jp",
    },
    "sender": {
        "name": "Kenji Sato",
        "first_name": "Kenji",
        "email": "kenji.sato@acme.co.jp",
        "domain": "acme.co.jp",
        "is_internal": False,
        "org": "Acme Manufacturing",
        "category": "customer",
        "rep_org": "",
        "role": "member",
    },
}

BLOCKS = [
    {
        "id": "block-if-fn",
        "emoji": "🔀",
        "name": "if",
        "signature": 'if(condition, then, otherwise)',
        "description": "Picks one of two values inline. Both branches are evaluated, so keep them cheap.",
        "snippet": '{{ if(sender.is_internal, "Hi", "Dear") }}',
    },
    {
        "id": "block-default",
        "emoji": "🛟",
        "name": "default",
        "signature": "default(value, fallback)",
        "description": "The fallback when the value is empty or falsey. The cure for a bare 'Dear ,'.",
        "snippet": '{{ default(sender.first_name, "Sir/Madam") }}',
    },
    {
        "id": "block-upper",
        "emoji": "🔠",
        "name": "upper",
        "signature": "upper(text)",
        "description": "Upper-cases the text. Handy in the attachment-name expression.",
        "snippet": "{{ upper(row.ref) }}",
    },
    {
        "id": "block-title",
        "emoji": "🔤",
        "name": "title",
        "signature": "title(text)",
        "description": "Capitalises each word -- for tidying a name that arrived shouting or lowercase.",
        "snippet": '{{ title("acme manufacturing co") }}',
    },
    {
        "id": "block-date",
        "emoji": "📅",
        "name": "date",
        "signature": 'date(value, format)',
        "description": "Reformats a datetime. An unparseable value is passed through untouched.",
        "snippet": '{{ date(mail.received, "%d %b %Y") }}',
    },
    {
        "id": "block-ftp-link",
        "emoji": "🔗",
        "name": "ftp_link",
        "signature": "ftp_link(file_name)",
        "description": "Builds the download URL from config.FTP_LINK_BASE. Use it on the FTP branch.",
        "snippet": "{{ ftp_link(row.file_name) }}",
    },
    {
        "id": "block-contains",
        "emoji": "🔍",
        "name": "contains",
        "signature": "contains(haystack, needle)",
        "description": "Case-insensitive substring test. Returns true/false -- feed it to if().",
        "snippet": '{{ if(contains(mail.subject, "invoice"), "billing team", "support team") }}',
    },
    {
        "id": "block-replace",
        "emoji": "♻️",
        "name": "replace",
        "signature": "replace(text, find, replace_with)",
        "description": "Replaces every occurrence. Good for swapping a file extension or suffix.",
        "snippet": '{{ replace(row.file_name, ".pdf", "_signed.pdf") }}',
    },
    {
        "id": "block-concat",
        "emoji": "🧵",
        "name": "concat",
        "signature": "concat(a, b, ...)",
        "description": "Glues any number of values into one string. The usual way to build a file name.",
        "snippet": '{{ concat(upper(row.ref), "-", row.qty, ".pdf") }}',
    },
    {
        "id": "block-if-tag",
        "emoji": "🪜",
        "name": "{% if %} block",
        "signature": "{% if cond %} ... {% else %} ... {% endif %}",
        "description": "Multi-line branching -- the file-vs-FTP fork. Nestable; {% elif %} is allowed.",
        "snippet": ("{% if row.uses_ftp %}You can download it here: {{ ftp_link(row.file_name) }}\n"
                    "{% else %}Please find {{ row.file_name }} attached.\n"
                    "{% endif %}"),
    },
]


def render_blocks():
    """Each block plus the output its snippet actually produces on DEMO_CONTEXT.

    Rendered through ``template_lang`` rather than hardcoded, so the palette can
    never advertise an output the DSL would not produce.
    """
    out = []
    for block in BLOCKS:
        try:
            demo_output = template_lang.render(block["snippet"], DEMO_CONTEXT)
        except template_lang.TemplateError as e:
            demo_output = f"(error: {e})"
        out.append({**block, "demo_output": demo_output})
    return out


# ----------------------------------------------------------------------------
# The cache-mail picker
# ----------------------------------------------------------------------------

FILTERS = [
    {"id": "all", "emoji": "📥", "label": "All"},
    {"id": "attachments", "emoji": "📎", "label": "Attachments"},
    {"id": "links", "emoji": "🔗", "label": "Links"},
    {"id": "org", "emoji": "🏢", "label": "Customer org"},
    {"id": "password", "emoji": "🔑", "label": "Password"},
    {"id": "tag", "emoji": "🏷️", "label": "Tagged"},
]

FILTER_IDS = tuple(f["id"] for f in FILTERS)


def matches(mail, filter_id, org_of, tags_of):
    """Whether ``mail`` belongs in the picker under ``filter_id``.

    ``org_of`` is a mail -> org|None resolver (customers.mail_org_resolver) and
    ``tags_of`` a mail-id -> tags dict (TagStore.tags_for); both are passed in so
    this module imports no store. An unknown filter id shows everything.
    """
    if filter_id == "attachments":
        return bool(mail.get("_has_attachments"))
    if filter_id == "links":
        return bool(mail.get("_has_links"))
    if filter_id == "org":
        return org_of(mail) is not None
    if filter_id == "password":
        return bool(mail.get("_has_password"))
    if filter_id == "tag":
        return bool(tags_of(mail.get("id", "")))
    return True


def page(mails, filter_id, offset, limit, org_of, tags_of):
    """One page of the picker: ``{mails, total, has_more}``.

    ``mails`` is the newest-first snapshot; the filter runs over all of it (so
    ``total`` is the honest count) and only then is the page sliced out.
    """
    selected = [m for m in mails if matches(m, filter_id, org_of, tags_of)]
    offset = max(0, offset)
    window = selected[offset:offset + max(0, limit)]
    return {"mails": window, "total": len(selected),
            "has_more": offset + len(window) < len(selected)}


def row_for_mail(mail):
    """The sheet row a real cache mail would have had.

    Composer previews against mail from the cache, which never came from a
    spreadsheet -- so synthesize the row Press would have matched to it: the
    known columns come from the mail itself, and ``file_name`` from its first
    attachment. Everything else the template asks for under ``row.*`` resolves to
    "" (the DSL's rule for a missing key), exactly as an absent sheet column does.
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


# ----------------------------------------------------------------------------
# Preview
# ----------------------------------------------------------------------------

def preview(template, mail, row, orgs):
    """Plan ``template`` against one already-chosen ``mail``. Writes nothing.

    A thin call through to ``bulk_compose.plan_for_mail`` -- the same function
    Press's draft path runs -- so what Composer shows is what Press would draft.
    A template carrying a stored ``error`` is refused here the way ``plan_all``
    refuses it for a whole run.
    """
    error = (template or {}).get("error") or ""
    if error:
        return bulk_compose.invalid_template_plan(0, row, error)
    return bulk_compose.plan_for_mail(0, row, mail, template, orgs)
