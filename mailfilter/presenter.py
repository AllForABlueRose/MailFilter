"""Convert cached mail dicts into the JSON view models the UI renders."""

import html
import re
from urllib.parse import quote

from config import PREVIEW_CHARS, PREVIEW_MAX_LINES

from . import expr, safelinks


def _excerpt(body):
    """The card body excerpt: the full body, capped at whichever limit it hits
    first — PREVIEW_MAX_LINES lines or PREVIEW_CHARS characters. Generous enough
    that a value further down the body (e.g. a detected password) is visible,
    while still bounding a runaway message. Detection scans the full body, not
    this."""
    lines = body.split("\n")
    if len(lines) > PREVIEW_MAX_LINES:
        body = "\n".join(lines[:PREVIEW_MAX_LINES])
    return body[:PREVIEW_CHARS]


def to_view_model(mail, main_node, optional_node,
                  attachment_blacklist=None, links_blacklist=None,
                  hide_safe_links=False):
    main_terms = expr.operands(main_node)
    optional_terms = expr.operands(optional_node)
    preview = html.escape(_excerpt(mail.get("body", "")))
    preview = _highlight(preview, main_terms, optional_terms)
    preview = _mark_passwords(preview, mail.get("_passwords", []))
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
        "links": _links(mail, main_terms, optional_terms, links_blacklist, hide_safe_links),
        # Smart Password Detection results from the last manual scan (empty until
        # one runs). Raw candidate strings; the frontend inserts them as DOM text
        # (a title tooltip), never HTML, like the people fields above.
        "has_password": bool(mail.get("_has_password")),
        "passwords": list(mail.get("_passwords", [])),
    }


def _mark_passwords(html_text, passwords):
    """Wrap each occurrence of a detected password in the (already escaped +
    keyword-highlighted) preview with a ``pw-loc`` locator span the UI lights up
    in orange when its chip is hovered.

    Each span carries ``data-pwloc=<index into passwords>`` so a chip can find its
    own occurrences. Passwords are matched as exact escaped literals; one that a
    keyword highlight happened to split simply isn't wrapped (no locator for it),
    which degrades gracefully. Runs after :func:`_highlight` so it never rewrites
    that pass's markup.
    """
    if not passwords:
        return html_text
    index_by_escaped = {}
    for i, pw in enumerate(passwords):
        escaped = html.escape(pw)
        if escaped and escaped not in index_by_escaped:
            index_by_escaped[escaped] = i
    if not index_by_escaped:
        return html_text
    # Longest first so a password isn't pre-empted by a shorter one nested in it.
    alternation = "|".join(re.escape(e) for e in sorted(index_by_escaped, key=len, reverse=True))

    def repl(match):
        return (f'<span class="pw-loc" data-pwloc="{index_by_escaped[match.group(0)]}">'
                f'{match.group(0)}</span>')

    return re.compile(alternation).sub(repl, html_text)


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


def _link_view(url, main_terms, optional_terms):
    """One link as raw URL + a highlighted (escaped) variant for display."""
    return {"url": url, "url_html": _highlight(html.escape(url), main_terms, optional_terms)}


def _links(mail, main_terms, optional_terms, blacklist, hide_safe_links=False):
    """http(s) links as raw URL + a highlighted (escaped) variant for display.

    When ``hide_safe_links`` is set, Outlook Safe Links whose decoded target also
    appears as a plain URL in the same mail are dropped as redundant (see
    :mod:`mailfilter.safelinks`)."""
    urls = mail.get("_links", [])
    hidden = safelinks.hidden_safe_links(urls) if hide_safe_links else set()
    out = []
    for url in urls:
        if url in hidden:
            continue
        if blacklist is not None and expr.evaluate(blacklist, url.lower()):
            continue
        out.append(_link_view(url, main_terms, optional_terms))
    return out


def extra_link_views(urls, main_node, optional_node, existing_urls=(), blacklist=None,
                     hide_safe_links=False):
    """Build ``{url, url_html}`` views for extra links grafted onto a mail.

    Used by the Brute Force Mail Deduplication transform (``routes.api_mail``) to
    append a notification's link(s) to its twin's view model. Skips any URL already
    present (``existing_urls``) and de-duplicates within ``urls``; keeps the escaping
    here so the presenter stays the sole place stored mail becomes HTML.

    ``blacklist`` (a parsed Links-blacklist node, or ``None``) drops any grafted URL
    it matches — the same rule :func:`_links` applies to a mail's own links — so a
    blacklisted link stays hidden even when it arrives via a grafted notification.
    """
    main_terms = expr.operands(main_node)
    optional_terms = expr.operands(optional_node)
    seen = set(existing_urls)
    # A grafted safe link is redundant when its plain twin is present anywhere in the
    # combined set (the twin's own links + the grafted ones).
    hidden = (safelinks.hidden_safe_links(list(existing_urls) + list(urls))
              if hide_safe_links else set())
    out = []
    for url in urls:
        if url in seen or url in hidden:
            continue
        seen.add(url)
        if blacklist is not None and expr.evaluate(blacklist, url.lower()):
            continue
        out.append(_link_view(url, main_terms, optional_terms))
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
