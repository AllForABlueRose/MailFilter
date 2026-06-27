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
            errors=tuple(errors),
        )


def filter_mails(mails, query):
    """Select mails matching the query, preserving the input (newest-first) order."""
    results = []
    for mail in mails:
        received = mail["_received_dt"]
        if query.start is not None and received < query.start:
            continue
        if query.end is not None and received > query.end:
            continue
        if query.main is not None and not expr.evaluate(query.main, mail["_search_text"]):
            continue
        if query.exclude is not None and expr.evaluate(query.exclude, mail["_search_text"]):
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
