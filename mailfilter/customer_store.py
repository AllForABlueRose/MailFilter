"""Persistence for user-defined organizations (Customer Management).

An organization groups the people the user corresponds with so replies can later
be templated by formality (internal employee vs. customer — the motivating use
case). Each org is a small dict: a name, a card colour, a free-text formality
``category`` (empty by default — configured later) with its own ``category_color``
label accent, a set of ``domains`` and a set
of per-contact ``contacts`` overrides, each carrying a role from
``config.ORG_DOMAIN_ROLES`` ("member" or "representative").

Like the other stores, the single JSON list is guarded by an ``RLock``, written
atomically, and encoded at rest through ``crypto`` (via ``persistence``). This
store owns the *organization definitions* only; ``customers.py`` derives the live
contact directory from the mail cache and resolves it against these definitions.
"""

import logging
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

import config

from . import persistence

log = logging.getLogger(__name__)

DEFAULT_COLOR = "#3b82f6"
DEFAULT_CATEGORY_COLOR = "#6366f1"
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _hex_color(value, fallback):
    """Clamp to a ``#rrggbb`` string, falling back when missing/malformed."""
    return value if isinstance(value, str) and _HEX_RE.match(value) else fallback


def _clean(value, cap):
    return str(value or "").strip()[:cap]


def _role(value):
    """Clamp a role to the known set, defaulting to the first ("member")."""
    role = str(value or "").strip().lower()
    return role if role in config.ORG_DOMAIN_ROLES else config.ORG_DOMAIN_ROLES[0]


def _coerce_domains(raw):
    """Normalize ``[{domain, role}, ...]``: lowercase + cap domains, clamp roles,
    drop blanks, keep the first occurrence of each domain (first-wins dedup)."""
    out, seen = [], set()
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        domain = _clean(entry.get("domain"), config.ORG_DOMAIN_MAX).lower()
        if not domain or domain in seen:
            continue
        seen.add(domain)
        out.append({"domain": domain, "role": _role(entry.get("role"))})
    return out


def _coerce_contacts(raw):
    """Normalize ``[{email, role}, ...]`` the same way as domains (by email)."""
    out, seen = [], set()
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        email = _clean(entry.get("email"), config.ORG_EMAIL_MAX).lower()
        if not email or email in seen:
            continue
        seen.add(email)
        out.append({"email": email, "role": _role(entry.get("role"))})
    return out


class CustomerStore:

    def __init__(self, cache_file):
        self._cache_file = Path(cache_file)
        self._lock = threading.RLock()
        self._items = {}  # id -> organization dict

    def load(self):
        raw, _alg = persistence.load_encoded(self._cache_file)
        items = {}
        if isinstance(raw, list):
            for entry in raw:
                coerced = self._coerce(entry)
                if coerced is not None:
                    items[coerced["id"]] = coerced
        with self._lock:
            self._items = items
        log.info("Loaded %d organization(s)", len(items))

    def snapshot(self):
        """Every organization, oldest-first (creation order), as independent copies.

        Creation order is what gives the first-wins tiebreak when a domain/email
        was placed in two orgs (see customers._resolution_maps).
        """
        with self._lock:
            ordered = sorted(self._items.values(), key=lambda o: o.get("created", ""))
            return [self._copy(o) for o in ordered]

    def create(self, raw):
        coerced = self._coerce(raw, new=True)
        with self._lock:
            self._items[coerced["id"]] = coerced
            self._save()
            return self._copy(coerced)

    def update(self, oid, raw):
        with self._lock:
            current = self._items.get(oid)
            if current is None:
                return None
            merged = self._coerce({**current, **(raw or {}), "id": oid}, base=current)
            self._items[oid] = merged
            self._save()
            return self._copy(merged)

    def delete(self, oid):
        with self._lock:
            existed = self._items.pop(oid, None) is not None
            if existed:
                self._save()
            return existed

    def assign(self, email, oid, role):
        """Make ``email`` a per-contact override of org ``oid`` with ``role``.

        Removes the email from every org's ``contacts`` first, so an email is the
        override of at most one org and the write path never creates a collision.
        Returns the updated target org, or ``None`` for a blank email / unknown org.
        """
        email = _clean(email, config.ORG_EMAIL_MAX).lower()
        with self._lock:
            target = self._items.get(oid)
            if not email or target is None:
                return None
            self._drop_contact(email)
            target["contacts"].append({"email": email, "role": _role(role)})
            self._save()
            return self._copy(target)

    def unassign(self, email):
        """Remove ``email``'s override from whichever org holds it. Returns whether
        anything was removed."""
        email = _clean(email, config.ORG_EMAIL_MAX).lower()
        with self._lock:
            if not email:
                return False
            removed = self._drop_contact(email)
            if removed:
                self._save()
            return removed

    def set_domain(self, oid, domain, role):
        """Map ``domain`` to org ``oid`` with ``role`` (the drag-a-domain action).

        Removes the domain from every org's ``domains`` first, so a domain maps to
        at most one org and the write path never creates a collision — the domain
        analog of :meth:`assign`. Returns the updated target org, or ``None`` for a
        blank domain / unknown org.
        """
        domain = _clean(domain, config.ORG_DOMAIN_MAX).lower()
        with self._lock:
            target = self._items.get(oid)
            if not domain or target is None:
                return None
            self._drop_domain(domain)
            target["domains"].append({"domain": domain, "role": _role(role)})
            self._save()
            return self._copy(target)

    def has_member_base(self, email):
        """Whether ``email`` has a **base membership** — its domain is mapped as a
        member of some org, or the email is an explicit member pin.

        A contact can only be assigned as a *representative* once this is true
        (you must know who they work for before recording who they front for). The
        check uses only the org definitions, so the store stays free of the mail
        cache.
        """
        email = _clean(email, config.ORG_EMAIL_MAX).lower()
        if not email:
            return False
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        with self._lock:
            for org in self._items.values():
                if any(c["email"] == email and c["role"] == "member" for c in org["contacts"]):
                    return True
                if domain and any(d["domain"] == domain and d["role"] == "member"
                                  for d in org["domains"]):
                    return True
        return False

    # ----- internals -----

    def _drop_contact(self, email):
        # Caller must hold the lock. Returns True if any org held the email.
        removed = False
        for org in self._items.values():
            kept = [c for c in org["contacts"] if c["email"] != email]
            if len(kept) != len(org["contacts"]):
                org["contacts"] = kept
                removed = True
        return removed

    def _drop_domain(self, domain):
        # Caller must hold the lock. Returns True if any org held the domain.
        removed = False
        for org in self._items.values():
            kept = [d for d in org["domains"] if d["domain"] != domain]
            if len(kept) != len(org["domains"]):
                org["domains"] = kept
                removed = True
        return removed

    @staticmethod
    def _copy(org):
        # Deep-enough copy so callers can't mutate the stored lists in place.
        clone = dict(org)
        clone["domains"] = [dict(d) for d in org["domains"]]
        clone["contacts"] = [dict(c) for c in org["contacts"]]
        return clone

    def _coerce(self, raw, base=None, new=False):
        """Normalize one organization dict: known fields only, typed and bounded.

        ``new`` mints a fresh id and creation timestamp and defaults the
        category/domains/contacts empty. Returns ``None`` for a non-dict (so a
        corrupt cache entry is dropped on load).
        """
        if not isinstance(raw, dict):
            return None
        base = base or {}

        if new or not raw.get("id"):
            oid = uuid.uuid4().hex
            created = datetime.now().strftime(config.RECEIVED_FORMAT)
        else:
            oid = str(raw["id"])
            created = raw.get("created") or base.get("created") \
                or datetime.now().strftime(config.RECEIVED_FORMAT)

        color = _hex_color(raw.get("color", base.get("color")), base.get("color", DEFAULT_COLOR))
        category_color = _hex_color(
            raw.get("category_color", base.get("category_color")),
            base.get("category_color", DEFAULT_CATEGORY_COLOR))

        name = _clean(raw.get("name", base.get("name", "")), config.ORG_NAME_MAX) or "Untitled"
        category = _clean(raw.get("category", base.get("category", "")), config.ORG_CATEGORY_MAX)

        return {
            "id": oid,
            "name": name,
            "color": color,
            "category": category,
            "category_color": category_color,
            "domains": _coerce_domains(raw.get("domains", base.get("domains", []))),
            "contacts": _coerce_contacts(raw.get("contacts", base.get("contacts", []))),
            "created": created,
        }

    def _save(self):
        # Caller must hold the lock. Persist as a list (creation order).
        ordered = sorted(self._items.values(), key=lambda o: o.get("created", ""))
        persistence.save_encoded(self._cache_file, ordered)
