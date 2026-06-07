"""Pure mail filtering — no Flask, no HTML, no COM.

Operates on the derived "_" fields the MailStore computes at ingest
time, so a request does no datetime parsing or string lowering per mail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


def parse_datetime(value):
    """Parse an ISO datetime string (from <input type=datetime-local>) or None."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _split_keywords(raw):
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


@dataclass(frozen=True)
class MailQuery:
    start: datetime | None = None
    end: datetime | None = None
    main: list = field(default_factory=list)        # lowercased
    optional: list = field(default_factory=list)    # original case (highlighting only)
    exclude: list = field(default_factory=list)     # lowercased
    sender: str = ""                                # lowercased
    recipient: str = ""                             # lowercased

    @classmethod
    def from_args(cls, args):
        """Build a query from request query-string args."""
        return cls(
            start=parse_datetime(args.get("start")),
            end=parse_datetime(args.get("end")),
            main=[k.lower() for k in _split_keywords(args.get("main"))],
            optional=_split_keywords(args.get("optional")),
            exclude=[k.lower() for k in _split_keywords(args.get("exclude"))],
            sender=args.get("sender", "").strip().lower(),
            recipient=args.get("recipient", "").strip().lower(),
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
        text = mail["_search_text"]
        if query.main and not any(keyword in text for keyword in query.main):
            continue
        if query.exclude and any(keyword in text for keyword in query.exclude):
            continue
        if query.sender and query.sender not in mail["_sender_text"]:
            continue
        if query.recipient and query.recipient not in mail["_recipient_text"]:
            continue
        results.append(mail)
    return results
