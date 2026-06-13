"""Convert cached mail dicts into the JSON view models the UI renders."""

import html
import re
from urllib.parse import quote

from config import PREVIEW_CHARS

from . import expr


def to_view_model(mail, main_node, optional_node,
                  attachment_blacklist=None, links_blacklist=None):
    main_terms = expr.operands(main_node)
    optional_terms = expr.operands(optional_node)
    preview = html.escape(mail.get("body", "")[:PREVIEW_CHARS])
    preview = _highlight(preview, main_terms, optional_terms)
    preview = preview.replace("\n", "<br>")
    return {
        "id": mail.get("id", ""),
        "subject": html.escape(mail.get("subject", "")),
        # People are raw {name, email}; the frontend inserts them via the DOM as
        # text (never HTML), so they are not escaped here.
        "sender": {"name": mail.get("sender", ""), "email": mail.get("sender_email", "")},
        "recipients": _people(mail.get("recipient_names", []), mail.get("recipient_emails", [])),
        "cc": _people(mail.get("cc_names", []), mail.get("cc_emails", [])),
        "received": mail["received"],
        "preview": preview,
        "is_thread": mail["is_thread"],
        "icon": "🧵" if mail["is_thread"] else "✉️",
        # Filenames and URLs carry a highlighted (escaped + span-wrapped) variant
        # for display, alongside the raw value used for download/href/drag.
        # Blacklisted attachments/links are dropped here (display + workspace).
        "attachments": _attachments(mail, main_terms, optional_terms, attachment_blacklist),
        "links": _links(mail, main_terms, optional_terms, links_blacklist),
    }


def _people(names, emails):
    """Pair name/email lists by index into ``[{name, email}, ...]``.

    Tolerates lists of unequal length (older cache entries), padding the short
    one with "" and dropping entries that have neither a name nor an email.
    """
    people = []
    for i in range(max(len(names), len(emails))):
        name = names[i] if i < len(names) else ""
        email = emails[i] if i < len(emails) else ""
        if name or email:
            people.append({"name": name, "email": email})
    return people


def _attachments(mail, main_terms, optional_terms, blacklist):
    """Attachment filenames (raw + highlighted) paired with their download URLs.

    The URL/``index`` are keyed by the *original* attachment position, so a
    blacklisted attachment can be skipped without misaligning the rest.
    """
    mail_id = quote(str(mail.get("id", "")), safe="")
    out = []
    for i, att in enumerate(mail.get("attachments", [])):
        filename = att.get("filename", "attachment")
        if blacklist is not None and expr.evaluate(blacklist, filename.lower()):
            continue
        out.append({
            "filename": filename,
            "filename_html": _highlight(html.escape(filename), main_terms, optional_terms),
            "index": i,
            "url": f"/attachments/{mail_id}/{i}",
        })
    return out


def _links(mail, main_terms, optional_terms, blacklist):
    """http(s) links as raw URL + a highlighted (escaped) variant for display."""
    out = []
    for url in mail.get("_links", []):
        if blacklist is not None and expr.evaluate(blacklist, url.lower()):
            continue
        out.append({"url": url, "url_html": _highlight(html.escape(url), main_terms, optional_terms)})
    return out


def _highlight(escaped_text, main_terms, optional_terms):
    """Wrap main- and optional-keyword matches in differently-coloured spans.

    ``main_terms`` and ``optional_terms`` are expression operand leaves
    (``('lit', ...)`` / ``('re', ...)`` from :mod:`mailfilter.expr`). Literal
    terms are HTML-escaped the same way as the text so terms like "R&D" still
    match; regex terms are applied as written. Matching is case-insensitive,
    and main matches take precedence over optional where they overlap.
    """
    main_src = _operands_to_pattern(main_terms)
    optional_src = _operands_to_pattern(optional_terms)

    parts = []
    if main_src:
        parts.append(f"(?P<m>{main_src})")
    if optional_src:
        parts.append(f"(?P<o>{optional_src})")
    if not parts:
        return escaped_text

    combined = re.compile("|".join(parts), re.IGNORECASE)

    def repl(match):
        # main wins over optional when a span matched both branches
        cls = "highlight-main" if match.groupdict().get("m") is not None else "highlight"
        return f'<span class="{cls}">{match.group(0)}</span>'

    return combined.sub(repl, escaped_text)


def _operands_to_pattern(terms):
    """Build an alternation source from expression operand leaves."""
    sources = []
    for term in terms:
        if term[0] == "lit":
            if term[1]:
                sources.append(re.escape(html.escape(term[1])))
        else:  # ('re', compiled, source)
            sources.append(term[2])
    return "|".join(s for s in sources if s)
