"""Outlook Safe Links deduplication (experimental) — pure, no Flask/HTML/COM.

Outlook "Safe Links" rewrites a URL into a wrapper of the form
``https://<tenant>.safelinks.protection.outlook.com/?url=<url-encoded target>&data=...``
— the real destination is the ``url`` query parameter. When a mail carries **both** a
plain URL and its safe-link wrapper, the wrapper is redundant noise.

This module is a **read-only view transform**: given the list of URL strings from one
mail (``mail["_links"]``), it returns which safe-link URLs to hide — those whose
decoded target also appears as a plain (non-safe) URL in the same list, matched after
light normalization (case-insensitive scheme+host, trailing '/' on the path ignored).
A safe link with no plain twin is kept. It never mutates anything.
"""

from urllib.parse import urlparse, parse_qs

_SAFELINK_HOST_SUFFIX = "safelinks.protection.outlook.com"


def is_safe_link(url):
    """Whether ``url`` is an Outlook Safe Links wrapper (by host)."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host == _SAFELINK_HOST_SUFFIX or host.endswith("." + _SAFELINK_HOST_SUFFIX)


def safe_link_target(url):
    """The decoded real target of a safe link, or ``None`` when ``url`` isn't a safe
    link or has no ``url`` query parameter. ``parse_qs`` percent-decodes the value."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if not (host == _SAFELINK_HOST_SUFFIX or host.endswith("." + _SAFELINK_HOST_SUFFIX)):
        return None
    values = parse_qs(parsed.query).get("url")
    return values[0] if values else None


def _normalize(url):
    """A loose match key: lowercase scheme+host, drop a trailing '/' on the path, keep
    query/fragment. Falls back to a lowercased trim if the URL can't be parsed."""
    try:
        p = urlparse(url)
    except ValueError:
        return (url or "").strip().lower()
    if not p.scheme or not p.hostname:
        return (url or "").strip().lower()
    port = f":{p.port}" if p.port else ""
    rest = (f"?{p.query}" if p.query else "") + (f"#{p.fragment}" if p.fragment else "")
    return f"{p.scheme.lower()}://{p.hostname.lower()}{port}{p.path.rstrip('/')}{rest}"


def hidden_safe_links(urls):
    """The set of safe-link URLs in ``urls`` whose decoded target also appears as a
    plain (non-safe) URL in the same list."""
    plain = {_normalize(u) for u in urls if not is_safe_link(u)}
    hidden = set()
    for u in urls:
        target = safe_link_target(u)
        if target is not None and _normalize(target) in plain:
            hidden.add(u)
    return hidden
