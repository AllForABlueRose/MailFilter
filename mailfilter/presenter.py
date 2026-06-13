"""Convert cached mail dicts into the JSON view models the UI renders."""

import html
import re
from urllib.parse import quote

from config import PREVIEW_CHARS

from . import expr


def to_view_model(mail, main_node, optional_node):
    preview = html.escape(mail.get("body", "")[:PREVIEW_CHARS])
    preview = _highlight(
        preview, expr.operands(main_node), expr.operands(optional_node)
    )
    preview = preview.replace("\n", "<br>")
    return {
        "subject": html.escape(mail.get("subject", "")),
        "sender": html.escape(mail.get("sender", "")),
        "received": mail["received"],
        "preview": preview,
        "is_thread": mail["is_thread"],
        "icon": "🧵" if mail["is_thread"] else "✉️",
        "attachments": _attachments(mail),
        "links": mail.get("_links", []),
    }


def _attachments(mail):
    """Attachment filenames paired with their download URLs.

    The URL is keyed by mail id + index, which the /attachments route maps
    back to the stored file — so nothing from the body reaches the path.
    """
    mail_id = quote(str(mail.get("id", "")), safe="")
    return [
        {
            "filename": att.get("filename", "attachment"),
            "url": f"/attachments/{mail_id}/{i}",
        }
        for i, att in enumerate(mail.get("attachments", []))
    ]


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
