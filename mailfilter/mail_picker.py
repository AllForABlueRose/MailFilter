"""The emoji-filtered, lazily-paged cache-mail picker, shared by Composer and Press.

Composer picks ONE mail to preview a template against; Press loads MANY mail items
into its worklist. Both want the same thing: a page of cached mail, narrowed by a
few cheap predicates, newest-first. That lives here once rather than twice.

This is a *picker*, not a second copy of ``filters.py``. ``filters.py`` owns the
search-query language (the boolean keyword grammar, date ranges, the sidebar). This
module answers a much smaller question -- "which cached mail do I want to work on
right now" -- with predicates over fields the cache has already derived
(``_has_attachments`` / ``_has_links`` / ``_has_password``) plus the org and tag
lookups the caller passes in.

Pure: stdlib only. It imports **no store** -- the org resolver and the tag lookup are
injected, the same no-cycle shape as ``customers.py`` and ``composer.py``.
"""

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

    ``org_of`` is a mail -> org|None resolver (``customers.mail_org_resolver``) and
    ``tags_of`` a mail-id -> tags dict (``TagStore.tags_for``); both are passed in so
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


def card(mail, org_label=None, tags=None):
    """The slim picker card both views render.

    Raw strings -- the frontend inserts every one as DOM **text** (this is a picker,
    not the mail list: no escaping, no highlighting, no HTML).
    """
    return {
        "id": mail.get("id", ""),
        "subject": str(mail.get("subject", "")),
        "sender": {"name": str(mail.get("sender", "")),
                   "email": str(mail.get("sender_email", ""))},
        "received": str(mail.get("received", "")),
        "has_attachments": bool(mail.get("_has_attachments")),
        "has_links": bool(mail.get("_has_links")),
        "has_password": bool(mail.get("_has_password")),
        "tags": tags or {},
        "org_labels": [org_label] if org_label else [],
    }
