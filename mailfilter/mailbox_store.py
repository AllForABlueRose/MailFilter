"""Persistence for the mailboxes Press may draft from, and whether they are proved.

Press writes to a real mailbox, so it refuses to write to one the user has merely
*claimed*. Two mailboxes are held here -- the user's own (``personal``) and one they
have access to (``shared``) -- each with a lifecycle:

    unset  -> the user has not named it
    pending-> named, but Outlook was not running, so it could not be proved yet
    verified-> proved against Outlook; only now do Press's draft controls unlock

The proof itself lives in :mod:`mailfilter.outlook` (``profile_address`` for the
personal mailbox -- it must BE your Outlook profile's address, not merely one you can
open; ``check_mailbox_access`` for the shared one -- you must be able to open it).
This store only records the verdict, so it stays free of COM and testable anywhere.

``pending`` is the important state: on a machine with no Outlook the user can still
type their address, and the check is deferred rather than refused -- it runs the next
time Outlook is reachable. Until then Press keeps every draft control disabled.

One field outlives that lifecycle: ``own_address``, the last address a *personal*
verification proved. It is what the rest of the app derives its **internal domain** from
(``routes._internal_domains`` -> ``customers.internal_domains``), and it is deliberately
**sticky** -- set on the first successful detection, overwritten only when a detection
proves a *different* domain, and never cleared by a failed, rejected or deferred check.
Press's mailbox is allowed to be broken; the mail list, ``sender.is_internal``, org
resolution and the SDS scan are not allowed to change behaviour because of it. That is
the whole of this store's reach outside Press.

Guarded by an ``RLock``, written atomically, encoded at rest through the same
``crypto`` seam as the other stores. It carries no secrets -- an address is not one.
"""

import logging
import threading
from datetime import datetime
from pathlib import Path

import config

from . import persistence, util

log = logging.getLogger(__name__)

DEFAULT_MAILBOX = {"address": "", "status": "unset", "verified_at": "", "error": ""}

DEFAULTS = {
    "personal": dict(DEFAULT_MAILBOX),
    "shared": dict(DEFAULT_MAILBOX),
    "selected": "personal",   # which mailbox drafts are sent on behalf of / CC'd to
    "cc_enabled": True,       # add the selected mailbox to the reply's CC
    # The last address a *personal* verification proved, kept independently of that
    # mailbox's live status -- see `remember_own_address`.
    "own_address": "",
}


def _coerce_mailbox(raw, base=None):
    out = dict(base or DEFAULT_MAILBOX)
    if not isinstance(raw, dict):
        return out
    if "address" in raw:
        out["address"] = str(raw.get("address") or "").strip()[
            :config.MAILBOX_ADDRESS_MAX]
    if "status" in raw:
        status = str(raw.get("status") or "")
        out["status"] = status if status in config.MAILBOX_STATUSES else "unset"
    if "verified_at" in raw:
        out["verified_at"] = str(raw.get("verified_at") or "")
    if "error" in raw:
        out["error"] = str(raw.get("error") or "")
    # A mailbox with no address cannot be in any state but "unset" -- a corrupt or
    # hand-edited cache must not be able to unlock the draft controls.
    if not out["address"]:
        out["status"] = "unset"
        out["verified_at"] = ""
    return out


def coerce(raw, base=None):
    """Known fields only, typed and bounded (a corrupt cache cannot grant `verified`)."""
    base = base or DEFAULTS
    out = {
        "personal": _coerce_mailbox((raw or {}).get("personal"), base.get("personal")),
        "shared": _coerce_mailbox((raw or {}).get("shared"), base.get("shared")),
        "selected": base.get("selected", "personal"),
        "cc_enabled": bool(base.get("cc_enabled", True)),
        "own_address": str(base.get("own_address") or ""),
    }
    if isinstance(raw, dict):
        selected = str(raw.get("selected") or "")
        if selected in config.MAILBOX_KINDS:
            out["selected"] = selected
        if "cc_enabled" in raw and raw["cc_enabled"] is not None:
            out["cc_enabled"] = bool(raw["cc_enabled"])
        if "own_address" in raw:
            out["own_address"] = str(raw.get("own_address") or "").strip()[
                :config.MAILBOX_ADDRESS_MAX]
    return out


class MailboxStore:

    def __init__(self, cache_file):
        self._cache_file = Path(cache_file)
        self._lock = threading.RLock()
        self._state = coerce(None)

    def load(self):
        raw, _alg = persistence.load_encoded(self._cache_file)
        if isinstance(raw, dict):
            with self._lock:
                self._state = coerce(raw)
            log.info("Loaded Press mailbox settings from cache")

    def snapshot(self):
        with self._lock:
            return {
                "personal": dict(self._state["personal"]),
                "shared": dict(self._state["shared"]),
                "selected": self._state["selected"],
                "cc_enabled": self._state["cc_enabled"],
                "own_address": self._state["own_address"],
            }

    def get(self, kind):
        with self._lock:
            return dict(self._state[kind]) if kind in config.MAILBOX_KINDS else None

    def selected_address(self):
        """The address of the selected mailbox, but ONLY once verified.

        Returns ``""`` for an unset/pending mailbox -- so a caller that forgets to
        check the status still cannot draft from an unproved address.
        """
        with self._lock:
            box = self._state[self._state["selected"]]
            return box["address"] if box["status"] == "verified" else ""

    def is_ready(self):
        """Whether the selected mailbox is proved and Press may create drafts."""
        with self._lock:
            return self._state[self._state["selected"]]["status"] == "verified"

    def set_address(self, kind, address, status, error=""):
        """Record a mailbox and the verdict on it. A mismatch clears the address.

        ``status`` is ``verified`` (proved), ``pending`` (Outlook unavailable, check
        deferred) or ``unset`` (rejected -- the address is dropped and the user is
        asked again, per the rule that a wrong address must not linger).
        """
        if kind not in config.MAILBOX_KINDS:
            raise ValueError(f"unknown mailbox kind {kind!r}")
        with self._lock:
            if status == "unset":
                box = dict(DEFAULT_MAILBOX)
                box["error"] = error
            else:
                box = _coerce_mailbox({
                    "address": address,
                    "status": status,
                    "verified_at": (datetime.now().strftime(config.RECEIVED_FORMAT)
                                    if status == "verified" else ""),
                    "error": error,
                })
            self._state[kind] = box
            self._save()
            return dict(box)

    def own_address(self):
        """The last address a *personal* verification proved. Sticky.

        This -- not the personal mailbox's live status -- is what the rest of the app
        keys "who am I" off (``routes._internal_domains``). Press's mailbox may be
        unset, pending or rejected at any moment; the mail list, ``sender.is_internal``,
        org resolution and the SDS scan must not change behaviour because of it.
        """
        with self._lock:
            return self._state["own_address"]

    def remember_own_address(self, address):
        """Record ``address`` as the identity the app derives its internal domain from.

        Written only when there is nothing saved yet (the first successful detection)
        or when the new address is on a **different domain** than the saved one. A
        re-verification on the same domain leaves it alone, and a blank address -- a
        failed, rejected or deferred check -- never clears it. Returns the saved value.
        """
        with self._lock:
            saved = self._state["own_address"]
            new = (address or "").strip()[:config.MAILBOX_ADDRESS_MAX]
            if not new:
                return saved
            if saved and util.domain_of(saved) == util.domain_of(new):
                return saved
            self._state["own_address"] = new
            self._save()
            log.info("Internal domain is now %r (from the verified personal mailbox)",
                     util.domain_of(new))
            return new

    def pending_kinds(self):
        """Kinds whose check was deferred because Outlook was unavailable."""
        with self._lock:
            return [k for k in config.MAILBOX_KINDS
                    if self._state[k]["status"] == "pending"]

    def update(self, raw):
        """Merge the user-settable fields (``selected``, ``cc_enabled``); persist.

        Deliberately cannot set an address or a status -- those only move through
        :meth:`set_address`, which is reached by an actual verification.
        """
        with self._lock:
            merged = dict(self._state)
            if isinstance(raw, dict):
                selected = str(raw.get("selected") or "")
                if selected in config.MAILBOX_KINDS:
                    merged["selected"] = selected
                if "cc_enabled" in raw and raw["cc_enabled"] is not None:
                    merged["cc_enabled"] = bool(raw["cc_enabled"])
            self._state = merged
            self._save()
            return self.snapshot()

    def _save(self):
        # Caller must hold the lock.
        persistence.save_encoded(self._cache_file, self._state)
