"""Contact directory: aggregate people from cached mail, resolve their org.

A *contact* is one email address, deduplicated across every cached mail and
tagged with the display name most recently seen for it, how many mails it appears
on, and when it was last seen. ``build_directory`` then resolves each contact to
an **organization** using the org definitions owned by ``customer_store`` —
contact-level overrides beat domain mappings, which beat "unassigned".

This module is pure (stdlib only): it imports no Flask, no COM, and not the
store — orgs are passed in, mirroring how ``automation.py`` is handed the stores
it needs so there is no import cycle (``customer_store`` never imports this).
"""

import config
from config import RECEIVED_FORMAT

from . import expr

# Roles a contact/domain can hold within an org. Kept in sync with
# config.ORG_DOMAIN_ROLES; "member" is normal staff, "representative" is a 3rd
# party (or someone on a foreign domain) fronting the org. Resolution treats them
# the same way — the distinction is for the (future) formality/template engine.


def _normalize_email(email):
    """Lowercased, trimmed SMTP address, or "" if it isn't a usable address.

    Drops blanks and legacy Exchange X.500 DNs (``/O=...``) that failed to
    resolve to SMTP at ingest (see system-design §2) — they can't be domain-keyed.
    """
    email = (email or "").strip().lower()
    if not email or email.startswith("/") or "@" not in email:
        return ""
    return email


def _domain_of(email):
    return email.rsplit("@", 1)[-1]


def _people(mail):
    """Every (name, email) on ``mail`` — sender, To, and CC.

    Name/email lists are paired by index and the short one padded with "" (older
    cache entries can have unequal lengths), matching ``presenter._people``.
    """
    pairs = [(mail.get("sender", ""), mail.get("sender_email", ""))]
    for names_key, emails_key in (
        ("recipient_names", "recipient_emails"),
        ("cc_names", "cc_emails"),
    ):
        names = mail.get(names_key, []) or []
        emails = mail.get(emails_key, []) or []
        for i in range(max(len(names), len(emails))):
            name = names[i] if i < len(names) else ""
            email = emails[i] if i < len(emails) else ""
            pairs.append((name, email))
    return pairs


def aggregate(mails):
    """Deduplicate everyone on ``mails`` into a contact list (no org resolution).

    Returns ``[{email, name, domain, count, last_dt}, ...]`` keyed by lowercased
    email. ``name`` is the display name seen on the most recent mail; ``count`` is
    how many distinct mails the contact appears on; ``last_dt`` is the newest
    ``_received_dt`` (a datetime). Sorted by count desc, then email asc.
    """
    contacts = {}
    for mail in mails:
        received = mail.get("_received_dt")
        mail_id = mail.get("id")
        for name, email in _people(mail):
            email = _normalize_email(email)
            if not email:
                continue
            name = (name or "").strip()
            contact = contacts.get(email)
            if contact is None:
                contact = contacts[email] = {
                    "email": email,
                    "name": "",
                    "domain": _domain_of(email),
                    "_ids": set(),
                    "_name_dt": None,
                    "last_dt": None,
                }
            if mail_id is not None:
                contact["_ids"].add(mail_id)
            if received is not None and (contact["last_dt"] is None or received > contact["last_dt"]):
                contact["last_dt"] = received
            # Newest non-empty display name wins (>= so a same-instant later mail
            # still updates), so a contact shows the name they most recently used.
            if name and (contact["_name_dt"] is None or received is None
                         or received >= contact["_name_dt"]):
                contact["name"] = name
                contact["_name_dt"] = received

    out = []
    for contact in contacts.values():
        out.append({
            "email": contact["email"],
            "name": contact["name"],
            "domain": contact["domain"],
            "count": len(contact["_ids"]),
            "last_dt": contact["last_dt"],
        })
    out.sort(key=lambda c: (-c["count"], c["email"]))
    return out


def _resolution_maps(orgs):
    """Flatten org definitions into four lookups, split by the two axes a contact
    can hold independently — a **base membership** and a **representative-of**:

        member_email / member_domain  -> org   (role "member")
        rep_email    / rep_domain     -> org   (role "representative")

    Membership ("who they work for") and representation ("who they're a contact
    for") are separate facts: a representative is still a *member* of their own
    organization. Keeping the axes apart lets a contact be a Member of org X
    **and** a Representative of org Acme at once (the contracted-contact case).

    ``orgs`` arrive in creation order (the store snapshot), and each map keeps the
    first org seen for a given email/domain — so a value a corrupt/hand-edited
    cache placed in two orgs resolves to the **earliest** org deterministically
    (mirrors the first-wins id dedup in MailStore.add_mails).
    """
    maps = {"member_email": {}, "rep_email": {}, "member_domain": {}, "rep_domain": {}}
    for org in orgs:
        for entry in org.get("contacts", []) or []:
            email = _normalize_email(entry.get("email"))
            if not email:
                continue
            key = "rep_email" if entry.get("role") == "representative" else "member_email"
            maps[key].setdefault(email, org)
        for entry in org.get("domains", []) or []:
            domain = (entry.get("domain") or "").strip().lower()
            if not domain:
                continue
            key = "rep_domain" if entry.get("role") == "representative" else "member_domain"
            maps[key].setdefault(domain, org)
    return maps


def _org_info(prefix, org):
    """``{<prefix>_org_id, _org_name, _org_color, _category}`` for ``org`` (or nulls)."""
    if org is None:
        return {f"{prefix}_org_id": None, f"{prefix}_org_name": "",
                f"{prefix}_org_color": "", f"{prefix}_category": ""}
    return {
        f"{prefix}_org_id": org.get("id"),
        f"{prefix}_org_name": org.get("name", ""),
        f"{prefix}_org_color": org.get("color", ""),
        f"{prefix}_category": org.get("category", ""),
    }


def internal_domains(orgs, user_address=""):
    """Every domain a reply template should treat as **internal**, as a frozenset.

    "Internal" means *not an outside customer*, which is what a template branches its
    register on (``{{ if(sender.is_internal, "Best regards,", "Yours faithfully,") }}``).
    It is decided entirely by data the user entered -- there is no config list of
    internal domains, for the same reason there is no config mailbox address: who you
    are is something the app is told and verifies, not something it is born knowing.

    Two sources, unioned:

    1. **The user's own domain** -- from the mailbox they verified against Outlook
       (``mailbox_store``). Blank until one is verified, which is the honest answer:
       until the app knows who you are, it cannot know who your colleagues are, and
       everyone reads as external.
    2. **Your own and your partners' organizations** -- every **member** domain of every
       org whose category is ``config.ORG_INTERNAL_CATEGORIES`` (``Root`` -- your own
       company, which is how a second or third domain of your own is declared -- and
       ``Partner``, whose staff you treat as colleagues).

    **Member domains only, never representative ones.** A representative fronts an org
    from *outside* it (a third party on a foreign domain, §3.12), so their domain is not
    the org's and must not inherit its internal standing. This is the one place the
    member/representative split changes a template's wording.
    """
    internal_categories = {str(c).strip().lower()
                           for c in config.ORG_INTERNAL_CATEGORIES}
    domains = set()

    own = _domain_of(_normalize_email(user_address))
    if own:
        domains.add(own)

    for org in orgs or []:
        if str(org.get("category", "")).strip().lower() not in internal_categories:
            continue
        for entry in org.get("domains", []) or []:
            # role "member" (the org's own staff), never "representative".
            if entry.get("role") == "representative":
                continue
            domain = (entry.get("domain") or "").strip().lower()
            if domain:
                domains.add(domain)
    return frozenset(domains)


def resolve(email, orgs):
    """Resolve a single ``email`` to its base-membership and representative orgs.

    The single-address analog of :func:`build_directory`'s per-contact resolution
    (override > domain, first-wins), so a reply template can branch on
    who the sender is without rebuilding the whole directory. Returns
    ``{member_org_id, member_org_name, member_category, rep_org_id, rep_org_name,
    role}`` -- ids/strings empty when unresolved; ``role`` is "representative" if a
    rep mapping exists, else "member" if a base membership exists, else "". The ids
    let callers (e.g. the Key Vault capture route) key by org without a name lookup.
    """
    blank = {"member_org_id": None, "member_org_name": "", "member_category": "",
             "rep_org_id": None, "rep_org_name": "", "role": ""}
    email = _normalize_email(email)
    if not email:
        return blank
    maps = _resolution_maps(orgs)
    domain = _domain_of(email)
    member_org = maps["member_email"].get(email) or maps["member_domain"].get(domain)
    rep_org = maps["rep_email"].get(email) or maps["rep_domain"].get(domain)
    return {
        "member_org_id": member_org.get("id") if member_org else None,
        "member_org_name": member_org.get("name", "") if member_org else "",
        "member_category": member_org.get("category", "") if member_org else "",
        "rep_org_id": rep_org.get("id") if rep_org else None,
        "rep_org_name": rep_org.get("name", "") if rep_org else "",
        "role": "representative" if rep_org else ("member" if member_org else ""),
    }


def _org_label(org):
    """A mail-list label for ``org``: ``{name, color, card_style, card_ink}``. ``name``
    is the display name (the nickname if set, else the real name — mirroring the
    frontend's ``orgDisplayName``); ``color`` is the org's card colour. ``card_style``
    (outline/filled) and ``card_ink`` (white/black) let the pill mirror the card's
    appearance. Display-name-only, so it never leaks the real name when a nickname is
    set."""
    display = (org.get("display_name") or "").strip() or org.get("name", "")
    return {"name": display, "color": org.get("color", ""),
            "card_style": org.get("card_style", "outline"),
            "card_ink": org.get("card_ink", "white")}


# Public alias: the mail-list pill for a single resolved org (display name + colour).
org_label = _org_label


def mail_org_resolver(orgs, mappings=None):
    """Return ``mail -> org|None`` — the **single source** of a mail item's customer
    organization, shared by the mail-list pill, the download file name, and the CSV
    report so they never disagree.

    Hierarchy, first match wins:

    1. **Brute Force** keyword — only when ``mappings`` is given (the "Suspected
       Customers List", ``[{keyword, org_id}, ...]``): the first mapping (in list
       order) whose ``keyword`` **expression** matches the mail's ``subject``/``body``
       (case-insensitively) and whose ``org_id`` is a live org. Each ``keyword`` is a
       full boolean-search expression parsed by :mod:`mailfilter.expr` — the same
       grammar the search fields use (``,`` OR, ``;`` AND, ``[[ ]]`` groups,
       ``<{( regex )}>``) — so one mapping can require several terms. A matched
       mapping pointing at a missing org yields no brute-force hit and falls through
       to the sender; a keyword that fails to parse is skipped.
    2. **Representative** — the sender resolves (email override > domain) to an org
       they are a *representative of*.
    3. **Member** — else the sender's base-membership org.

    Returns the org dict from ``orgs`` (callers read ``id``/``name``/``display_name``/
    ``color`` per their need), or ``None``. The maps and compiled keyword expressions
    are built **once** so a whole list resolves without rebuilding them; pass
    ``mappings=None`` (or ``[]``) to disable the brute-force tier — e.g. when its
    experimental feature is off. Pure: no store, no HTML.
    """
    maps = _resolution_maps(orgs)
    orgs_by_id = {o.get("id"): o for o in orgs or []}
    # Compile each mapping's keyword into a boolean-search node once. A blank keyword
    # or one that fails to parse (e.g. a bad regex) is dropped — resolution never
    # raises over a hand-entered mapping.
    compiled = []
    for m in (mappings or []):
        keyword = str((m or {}).get("keyword") or "").strip()
        if not keyword:
            continue
        try:
            node = expr.parse(keyword)
        except expr.ExprError:
            continue
        if node is None:
            continue
        compiled.append((node, str(m.get("org_id") or "")))

    def resolve_one(mail):
        if compiled:
            content = (mail.get("subject", "") + "\n" + mail.get("body", "")).lower()
            for node, org_id in compiled:
                if expr.evaluate(node, content):
                    org = orgs_by_id.get(org_id)
                    if org is not None:
                        return org
                    break  # first keyword hit decides; missing org -> fall to sender
        email = _normalize_email(mail.get("sender_email", ""))
        if email:
            domain = _domain_of(email)
            return (maps["rep_email"].get(email) or maps["rep_domain"].get(domain)
                    or maps["member_email"].get(email) or maps["member_domain"].get(domain))
        return None

    return resolve_one


def resolve_mail_org(mail, orgs, mappings=None):
    """One-shot :func:`mail_org_resolver` for a single mail (see it for the hierarchy)."""
    return mail_org_resolver(orgs, mappings)(mail)


def build_directory(mails, orgs):
    """Aggregate contacts from ``mails`` and resolve each on both axes.

    Per contact and per axis, an explicit per-contact override (by email) beats a
    domain mapping (by domain). The result carries ``member_*`` (the base
    organization) and ``rep_*`` (the organization represented), each null when
    unset, plus ``rep_pinned`` (the representative came from a per-contact pin, so
    it can be cleared individually). No HTML — the frontend inserts every value as
    DOM text, per the people-field rule.
    """
    maps = _resolution_maps(orgs)
    directory = []
    for contact in aggregate(mails):
        email, domain = contact["email"], contact["domain"]
        member_org = maps["member_email"].get(email) or maps["member_domain"].get(domain)
        rep_override = maps["rep_email"].get(email)
        rep_org = rep_override or maps["rep_domain"].get(domain)
        last_dt = contact.pop("last_dt")
        contact["last_received"] = last_dt.strftime(RECEIVED_FORMAT) if last_dt else ""
        contact.update(_org_info("member", member_org))
        contact.update(_org_info("rep", rep_org))
        contact["rep_pinned"] = rep_override is not None
        directory.append(contact)
    return directory
