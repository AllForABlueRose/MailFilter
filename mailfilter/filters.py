"""Pure mail filtering — no Flask, no HTML, no COM.

Each text field (main, optional, exclude, sender, recipient) is a boolean
keyword expression parsed by :mod:`mailfilter.expr` (`,` = OR, `;` = AND,
`[[ ]]` = grouping, `<{( regex )}>`). Matching runs against the derived "_"
fields the MailStore computes at ingest, so a request does no datetime parsing
or string lowering per mail. ``optional`` is parsed only for highlighting; it
does not filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import expr
from .expr import ExprError


def parse_datetime(value):
    """Parse an ISO datetime string (from <input type=datetime-local>) or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class MailQuery:
    start: datetime | None = None
    end: datetime | None = None
    main: object = None          # parsed expr node, or None when blank
    optional: object = None      # parsed for highlighting only (does not filter)
    exclude: object = None
    sender: object = None
    recipient: object = None
    exclude_sender: object = None
    exclude_recipient: object = None
    # Not filters: these drop matching attachments/links from each mail's view
    # model (display + workspace bulk actions), without hiding the mail.
    attachment_blacklist: object = None
    links_blacklist: object = None
    resources_only: bool = False  # attachments and/or links
    passwords_only: bool = False  # only mail with a detected password (last scan)
    # Experimental: fold full-width (全角) <-> half-width (半角) on the main/exclude
    # keyword match so a search on one width also matches the other.
    normalize_width: bool = False
    # Experimental: extend the main/exclude keyword match beyond subject+body to
    # attachment filenames and/or link URLs.
    attachment_search: bool = False
    link_search: bool = False
    # Experimental (Brute Force Mail Deduplication): when on, mails whose subject
    # exactly equals ``dedupe_subject`` are treated as Zendesk notifications; the
    # transform runs in the route (routes.api_mail -> dedup.dedupe), not here.
    dedupe: bool = False
    dedupe_subject: str = ""
    errors: tuple = ()            # human-readable expression parse errors

    @classmethod
    def from_args(cls, args):
        """Build a query from request query-string args.

        Expression fields that fail to parse are recorded in ``errors`` (and
        left as ``None``); the caller should surface ``errors`` and return no
        results rather than filter on a half-understood query.
        """
        errors = []

        def parse_field(name):
            try:
                return expr.parse(args.get(name))
            except ExprError as e:
                errors.append(f"{name}: {e}")
                return None

        return cls(
            start=parse_datetime(args.get("start")),
            end=parse_datetime(args.get("end")),
            main=parse_field("main"),
            optional=parse_field("optional"),
            exclude=parse_field("exclude"),
            sender=parse_field("sender"),
            recipient=parse_field("recipient"),
            exclude_sender=parse_field("exclude_sender"),
            exclude_recipient=parse_field("exclude_recipient"),
            attachment_blacklist=parse_field("attachment_blacklist"),
            links_blacklist=parse_field("links_blacklist"),
            resources_only=args.get("resources") in ("1", "true", "on"),
            passwords_only=args.get("passwords") in ("1", "true", "on"),
            normalize_width=args.get("normalize_width") in ("1", "true", "on"),
            attachment_search=args.get("attachment_search") in ("1", "true", "on"),
            link_search=args.get("link_search") in ("1", "true", "on"),
            dedupe=args.get("dedupe") in ("1", "true", "on"),
            dedupe_subject=args.get("dedupe_subject") or "",
            errors=tuple(errors),
        )


def filter_mails(mails, query):
    """Select mails matching the query, preserving the input (newest-first) order."""
    # Normalize Search Character Width (experimental, keyword fields only): fold
    # the main/exclude query literals once up front and the mail's search text per
    # mail below, so both sides compare in one width. Off (the default) keeps the
    # parse-free fast path — the nodes are reused as-is and no folding runs.
    main, exclude = query.main, query.exclude
    fold = None
    if query.normalize_width and (main is not None or exclude is not None):
        fold = expr.fold_width
        main = expr.fold_node(main, fold)
        exclude = expr.fold_node(exclude, fold)

    # Attachment/Link Search Matching (experimental, keyword fields only): extend
    # the main/exclude text beyond subject+body with the precomputed attachment-name
    # and/or link-URL fields. Off (default) leaves the keyword match at subject+body.
    extra_keys = []
    if query.attachment_search:
        extra_keys.append("_attachment_text")
    if query.link_search:
        extra_keys.append("_links_text")

    results = []
    for mail in mails:
        received = mail["_received_dt"]
        if query.start is not None and received < query.start:
            continue
        if query.end is not None and received > query.end:
            continue
        if main is not None or exclude is not None:
            search_text = mail["_search_text"]
            if extra_keys:
                search_text = "\n".join([search_text] + [mail[k] for k in extra_keys])
            if fold is not None:
                search_text = fold(search_text)
            if main is not None and not expr.evaluate(main, search_text):
                continue
            if exclude is not None and expr.evaluate(exclude, search_text):
                continue
        if query.sender is not None and not expr.evaluate(query.sender, mail["_sender_text"]):
            continue
        if query.recipient is not None and not expr.evaluate(query.recipient, mail["_recipient_text"]):
            continue
        if query.exclude_sender is not None and expr.evaluate(query.exclude_sender, mail["_sender_text"]):
            continue
        if query.exclude_recipient is not None and expr.evaluate(query.exclude_recipient, mail["_recipient_text"]):
            continue
        if query.resources_only and not (
            mail["_has_attachments"] or mail["_has_links"]
        ):
            continue
        if query.passwords_only and not mail.get("_has_password"):
            continue
        results.append(mail)
    return results
