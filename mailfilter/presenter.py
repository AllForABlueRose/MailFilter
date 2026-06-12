"""Convert cached mail dicts into the JSON view models the UI renders."""

import html
import re
from urllib.parse import quote

from config import PREVIEW_CHARS


def to_view_model(mail, highlight_keywords):
    preview = html.escape(mail.get("body", "")[:PREVIEW_CHARS])
    preview = _highlight(preview, highlight_keywords)
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


def _highlight(escaped_text, keywords):
    """Wrap keyword matches in highlight spans.

    Keywords are HTML-escaped the same way as the text so terms like
    "R&D" still match, matching is case-insensitive, and one combined
    pattern keeps a keyword from matching inside another keyword's
    inserted markup.
    """
    patterns = [re.escape(html.escape(k)) for k in keywords if k.strip()]
    if not patterns:
        return escaped_text
    combined = re.compile("|".join(patterns), re.IGNORECASE)
    return combined.sub(
        lambda m: f'<span class="highlight">{m.group(0)}</span>',
        escaped_text,
    )
