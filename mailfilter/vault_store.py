"""The Key Vault: per-organization credential storage, sealed at rest.

``VaultStore`` owns the encrypted vault file (``vault_crypto`` seals/opens it with
AES-256-GCM under a scrypt-derived **master-passphrase** key) and a separate
**non-secret index** (``{org_id: {count, has_managed, has_temporary,
last_scan_dt}}``) that the Customer Management card reads — read-only — without
unlocking. Secrets live ONLY inside the sealed file and only in memory while the
vault is unlocked.

State machine: *uninitialized* -> ``init(passphrase)`` -> *unlocked*; on a later
run *locked* -> ``unlock(passphrase)`` (or ``unlock_with_dpapi`` if the user opted
into remember-on-machine) -> *unlocked* -> ``lock()``. An idle *unlocked* vault
auto-locks after ``config.VAULT_LOCK_TIMEOUT_SECONDS``.

External seam: ``get_secret(org_id, label)`` is the in-process accessor a future
workflow (e.g. browser automation) calls to read a customer key while unlocked —
the only intended way a secret leaves the store programmatically.

Dependency direction: ``vault_store -> vault_crypto (-> crypto), persistence,
util, config``. It never imports the customer store; the org id is just a key.
"""

import logging
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import config

from . import persistence, util, vault_crypto

log = logging.getLogger(__name__)


class VaultLocked(RuntimeError):
    """An operation needing the plaintext was attempted while locked."""


def _now():
    return datetime.now()


def _stamp():
    return _now().strftime(config.RECEIVED_FORMAT)


def _clip(value, cap):
    return str(value or "").strip()[:cap]


def _parse_dt(value):
    """A ``RECEIVED_FORMAT`` datetime string as a ``datetime``, or ``None``."""
    try:
        return datetime.strptime(value, config.RECEIVED_FORMAT)
    except (ValueError, TypeError):
        return None


class VaultStore:

    def __init__(self, vault_file, index_file, key_dpapi_file):
        self._vault_file = Path(vault_file)
        self._index_file = Path(index_file)
        self._key_file = Path(key_dpapi_file)
        self._lock = threading.RLock()
        # Held only while unlocked; cleared on lock().
        self._key = None
        self._salt = None
        self._data = None        # {"version", "entries": {org_id: [entry, ...]}}
        self._unlocked_at = None

    # ----- lifecycle / state -----

    def is_initialized(self):
        return self._vault_file.exists()

    def is_available(self):
        """Whether the AES-GCM cipher is importable here (cryptography present)."""
        return vault_crypto.is_available()

    def is_remembered(self):
        return self._key_file.exists()

    def is_unlocked(self):
        with self._lock:
            if self._key is None:
                return False
            if self._timed_out():
                self._do_lock()
                return False
            return True

    def status(self):
        with self._lock:
            return {
                "available": self.is_available(),
                "initialized": self.is_initialized(),
                "unlocked": self.is_unlocked(),
                "dpapi_available": vault_crypto.dpapi_available(),
                "remembered": self.is_remembered(),
            }

    def init(self, passphrase):
        """Create a brand-new empty vault sealed under ``passphrase``; unlock it.

        Returns ``False`` if a vault already exists (never silently overwrites) or
        the passphrase is too short / the cipher is unavailable.
        """
        with self._lock:
            if self.is_initialized() or not self.is_available():
                return False
            if len(passphrase or "") < config.VAULT_PASSPHRASE_MIN:
                return False
            self._salt = vault_crypto.new_salt()
            self._key = vault_crypto.derive_key(passphrase, self._salt)
            self._data = {"version": 1, "entries": {}}
            self._save_vault()
            self._save_index()
            self._touch()
            log.info("Key Vault initialized")
            return True

    def unlock(self, passphrase):
        """Unlock with the master passphrase. Returns whether it succeeded."""
        with self._lock:
            if not self.is_initialized() or not self.is_available():
                return False
            data = self._vault_file.read_bytes()
            try:
                salt = vault_crypto.salt_of(data)
                key = vault_crypto.derive_key(passphrase, salt)
                plaintext = vault_crypto.open_sealed(data, key)
            except vault_crypto.VaultAuthError:
                return False
            self._adopt(key, salt, plaintext)
            return True

    def unlock_with_dpapi(self):
        """Unlock using the opt-in DPAPI-wrapped key (no passphrase). Bool result."""
        with self._lock:
            if not (self.is_initialized() and self.is_remembered() and self.is_available()):
                return False
            try:
                key = vault_crypto.unwrap_key_dpapi(self._key_file.read_bytes())
                data = self._vault_file.read_bytes()
                salt = vault_crypto.salt_of(data)
                plaintext = vault_crypto.open_sealed(data, key)
            except Exception:
                log.warning("DPAPI vault unlock failed")
                return False
            self._adopt(key, salt, plaintext)
            return True

    def lock(self):
        with self._lock:
            self._do_lock()

    def remember_on_machine(self, enable):
        """Enable/disable passphrase-free unlock by DPAPI-wrapping the live key.

        Returns ``False`` if enabling while locked or DPAPI is unavailable.
        Disabling just removes the wrapped-key file.
        """
        with self._lock:
            if enable:
                if not self.is_unlocked() or not vault_crypto.dpapi_available():
                    return False
                util.atomic_write_bytes(self._key_file, vault_crypto.wrap_key_dpapi(self._key))
                return True
            if self._key_file.exists():
                self._key_file.unlink()
            return True

    # ----- entries (require unlocked) -----

    def entries_by_org(self):
        """Every entry grouped by org id, **secrets redacted** (metadata only).

        The card/list views never receive secrets in bulk — a secret is returned
        only by the explicit :meth:`reveal`.
        """
        with self._lock:
            self._require_unlocked()
            out = {}
            for org_id, items in self._data["entries"].items():
                ordered = self._ordered(self._visible(items))
                if ordered:
                    out[org_id] = [self._public(e) for e in ordered]
            return out

    def add_entry(self, org_id, fields):
        with self._lock:
            self._require_unlocked()
            org_id = _clip(org_id, 64)
            if not org_id:
                return None
            bucket = self._data["entries"].setdefault(org_id, [])
            if len(bucket) >= config.VAULT_MAX_ENTRIES_PER_ORG:
                return None
            entry = self._coerce(fields, org_id, new=True)
            bucket.append(entry)
            self._save_vault()
            self._save_index()
            return self._public(entry)

    def update_entry(self, entry_id, fields):
        with self._lock:
            self._require_unlocked()
            found = self._find(entry_id)
            if found is None:
                return None
            _org, entry = found
            entry.update(self._coerce({**entry, **(fields or {})}, entry["org_id"]))
            self._save_vault()
            self._save_index()
            return self._public(entry)

    def delete_entry(self, entry_id):
        with self._lock:
            self._require_unlocked()
            found = self._find(entry_id)
            if found is None:
                return False
            org_id, entry = found
            bucket = self._data["entries"][org_id]
            bucket.remove(entry)
            if not bucket:
                del self._data["entries"][org_id]
            self._save_vault()
            self._save_index()
            return True

    def reveal(self, entry_id):
        """Return one entry's secret (the explicit, audited single-secret read)."""
        with self._lock:
            self._require_unlocked()
            found = self._find(entry_id)
            return None if found is None else found[1].get("secret", "")

    def reveal_all(self):
        """Every entry's secret as ``{entry_id: secret}`` — the **bulk** secret read.

        This is the one sanctioned exception to "secrets never leave in bulk": it is
        gated on the vault being **unlocked**, never persisted or logged, and backs
        the Workshop hold-Z "reveal all" affordance. Still nothing is written.
        """
        with self._lock:
            self._require_unlocked()
            out = {}
            for items in self._data["entries"].values():
                for e in items:
                    if e.get("secret"):
                        out[e["id"]] = e["secret"]
            return out

    def search(self, query, org_names=None):
        """Entries matching ``query`` (case-insensitive), grouped by org, **redacted**.

        Matches the secret **value**, label, username, url, the created/scan
        datetimes, the source email, and the org's display name (passed in via
        ``org_names`` so the store stays free of any customer-store import). A blank
        query returns everything. Secrets are matched but never returned — the
        result is the same redacted shape as :meth:`entries_by_org`.
        """
        org_names = org_names or {}
        q = str(query or "").strip().lower()
        with self._lock:
            self._require_unlocked()
            out = {}
            for org_id, items in self._data["entries"].items():
                name = str(org_names.get(org_id, "")).lower()
                matched = [e for e in self._visible(items) if self._matches(e, q, name)]
                if matched:
                    out[org_id] = [self._public(e) for e in self._ordered(matched)]
            return out

    @staticmethod
    def _matches(entry, q, org_name):
        if not q:
            return True
        fields = (entry.get("secret", ""), entry.get("label", ""),
                  entry.get("username", ""), entry.get("url", ""),
                  entry.get("created", ""), entry.get("scan_dt", ""),
                  entry.get("source_email", ""), org_name)
        return any(q in str(f).lower() for f in fields)

    def capture_scan(self, org_id, secret, label=None, scan_dt=None, source_email=None):
        """Save a Smart-Password-Detection hit as a **temporary** key for an org.

        ``source_email`` (the mail's sender) is stored so a later Customer
        Management assignment can re-home an :data:`config.VAULT_UNASSIGNED_ORG_ID`
        capture to the org that sender then resolves to (see :meth:`rehome_unassigned`).
        Deduplicated by secret within the org so re-scanning does not pile up
        copies. When a newer mail re-records a secret already stored as a temporary
        key, the entry's ``scan_dt`` is advanced to the newer datetime (never rolled
        back) so an aged, hidden key becomes visible again (see :meth:`_visible`).
        Returns ``(public_entry_or_None, created)``: ``created`` is ``True`` only when
        a **brand-new** key was added — ``False`` on a dedup hit (including a scan_dt
        refresh), so callers can report genuinely-new captures rather than re-hits.
        """
        with self._lock:
            self._require_unlocked()
            org_id = _clip(org_id, 64)
            secret = _clip(secret, config.VAULT_SECRET_MAX)
            if not org_id or not secret:
                return None, False
            for entry in self._data["entries"].get(org_id, []):
                if entry.get("secret") == secret:
                    if entry.get("kind") == "temporary":
                        new_dt = _clip(scan_dt or _stamp(), 32)
                        if new_dt > (entry.get("scan_dt") or ""):
                            entry["scan_dt"] = new_dt
                            entry["updated"] = _stamp()
                            self._save_vault()
                            self._save_index()
                    return self._public(entry), False
            added = self.add_entry(org_id, {
                "label": label or "Detected password",
                "secret": secret,
                "kind": "temporary",
                "scan_dt": scan_dt or _stamp(),
                "source_email": source_email,
            })
            return added, added is not None

    def rehome_unassigned(self, resolver):
        """Move captures parked under :data:`config.VAULT_UNASSIGNED_ORG_ID` to the
        org their ``source_email`` now resolves to.

        ``resolver`` maps an email to an org id (or ``None`` when still
        unresolved). Only **temporary** captures move; a destination that already
        holds the same secret absorbs the entry (no duplicate). Requires the vault
        unlocked; returns how many entries were re-homed.
        """
        with self._lock:
            self._require_unlocked()
            bucket = self._data["entries"].get(config.VAULT_UNASSIGNED_ORG_ID, [])
            if not bucket:
                return 0
            moved = 0
            remaining = []
            for entry in bucket:
                target = (resolver(entry.get("source_email") or "")
                          if entry.get("kind") == "temporary" else None)
                if not target or target == config.VAULT_UNASSIGNED_ORG_ID:
                    remaining.append(entry)
                    continue
                dest = self._data["entries"].setdefault(target, [])
                if any(e.get("secret") == entry.get("secret") for e in dest):
                    moved += 1                       # absorbed by an existing key
                    continue
                if len(dest) >= config.VAULT_MAX_ENTRIES_PER_ORG:
                    remaining.append(entry)
                    continue
                entry["org_id"] = target
                entry["updated"] = _stamp()
                dest.append(entry)
                moved += 1
            if moved:
                if remaining:
                    self._data["entries"][config.VAULT_UNASSIGNED_ORG_ID] = remaining
                else:
                    self._data["entries"].pop(config.VAULT_UNASSIGNED_ORG_ID, None)
                self._save_vault()
                self._save_index()
            return moved

    def get_secret(self, org_id, label=None):
        """External accessor: the secret for an org (by label, else first managed).

        The intended programmatic seam for workflows. Requires the vault unlocked;
        returns ``None`` when locked, absent, or no entry matches.
        """
        with self._lock:
            if not self.is_unlocked():
                return None
            items = self._data["entries"].get(_clip(org_id, 64), [])
            if not items:
                return None
            if label:
                want = label.strip().lower()
                for e in items:
                    if e.get("label", "").strip().lower() == want:
                        self._touch()
                        return e.get("secret", "")
                return None
            managed = [e for e in items if e.get("kind") == "managed"]
            self._touch()
            return (managed or items)[0].get("secret", "")

    # ----- non-secret index (readable while locked) -----

    def index(self):
        """The persisted ``{org_id: {...}}`` non-secret summary (no unlock needed)."""
        with self._lock:
            if self.is_unlocked():
                return self._compute_index()
            obj, _alg = persistence.load_encoded(self._index_file)
            return obj if isinstance(obj, dict) else {}

    # ----- internals -----

    def _adopt(self, key, salt, plaintext):
        import json
        self._key = key
        self._salt = salt
        try:
            self._data = json.loads(plaintext)
        except Exception:
            self._data = {"version": 1, "entries": {}}
        self._data.setdefault("entries", {})
        self._touch()
        # Refresh the on-disk index in case it drifted from the sealed contents.
        self._save_index()

    def _do_lock(self):
        self._key = None
        self._salt = None
        self._data = None
        self._unlocked_at = None

    def _touch(self):
        self._unlocked_at = _now()

    def _timed_out(self):
        if self._unlocked_at is None:
            return True
        return (_now() - self._unlocked_at).total_seconds() > config.VAULT_LOCK_TIMEOUT_SECONDS

    def _require_unlocked(self):
        if not self.is_unlocked():
            raise VaultLocked("vault is locked")
        self._touch()

    def _coerce(self, fields, org_id, new=False):
        fields = fields or {}
        kind = str(fields.get("kind", "managed")).strip().lower()
        if kind not in config.VAULT_KINDS:
            kind = "managed"
        entry = {
            "id": fields.get("id") if not new and fields.get("id") else uuid.uuid4().hex,
            "org_id": org_id,
            "label": _clip(fields.get("label"), config.VAULT_LABEL_MAX) or "Key",
            "username": _clip(fields.get("username"), config.VAULT_USERNAME_MAX),
            "secret": _clip(fields.get("secret"), config.VAULT_SECRET_MAX),
            "url": _clip(fields.get("url"), config.VAULT_URL_MAX),
            "kind": kind,
            "scan_dt": _clip(fields.get("scan_dt"), 32) if kind == "temporary" else "",
            # Sender a capture came from; lets a later org assignment re-home it.
            "source_email": _clip(fields.get("source_email"), config.VAULT_USERNAME_MAX)
            if kind == "temporary" else "",
            "created": fields.get("created") or _stamp(),
            "updated": _stamp(),
        }
        return entry

    @staticmethod
    def _public(entry):
        # Everything except the secret; plus a has_secret flag for the UI.
        out = {k: v for k, v in entry.items() if k != "secret"}
        out["has_secret"] = bool(entry.get("secret"))
        return out

    def _find(self, entry_id):
        for org_id, items in self._data["entries"].items():
            for entry in items:
                if entry["id"] == entry_id:
                    return org_id, entry
        return None

    @staticmethod
    def _visible(items):
        """``items`` minus temporary keys older than the hide window.

        A temporary key whose ``scan_dt`` parses older than
        ``config.VAULT_TEMP_HIDE_AFTER_DAYS`` is omitted from the list/search views
        (still stored — :meth:`capture_scan` re-shows it when a newer mail records the
        same secret). Managed keys, and temporary keys with a missing/unparseable
        ``scan_dt``, are always kept.
        """
        cutoff = _now() - timedelta(days=config.VAULT_TEMP_HIDE_AFTER_DAYS)
        visible = []
        for e in items:
            if e.get("kind") == "temporary":
                dt = _parse_dt(e.get("scan_dt"))
                if dt is not None and dt < cutoff:
                    continue
            visible.append(e)
        return visible

    @staticmethod
    def _ordered(items):
        """Managed keys first, then temporary; each group newest datetime first.

        Two stable passes: sort by the entry's datetime descending (``created`` for
        managed, ``scan_dt`` for temporary), then by kind so managed sorts ahead. The
        stable sort preserves the datetime order within each kind.
        """
        def dt_key(e):
            return (e.get("created", "") if e.get("kind") == "managed"
                    else e.get("scan_dt", "")) or ""
        ordered = sorted(items, key=dt_key, reverse=True)
        ordered.sort(key=lambda e: 0 if e.get("kind") == "managed" else 1)
        return ordered

    def _compute_index(self):
        index = {}
        for org_id, items in self._data["entries"].items():
            if not items:
                continue
            temps = [e.get("scan_dt", "") for e in items if e.get("kind") == "temporary"]
            index[org_id] = {
                "count": len(items),
                "has_managed": any(e.get("kind") == "managed" for e in items),
                "has_temporary": bool(temps),
                "last_scan_dt": max(temps) if temps else "",
            }
        return index

    def _save_vault(self):
        import json
        payload = json.dumps(self._data, ensure_ascii=False).encode("utf-8")
        sealed = vault_crypto.seal(payload, self._key, self._salt)
        util.atomic_write_bytes(self._vault_file, sealed)

    def _save_index(self):
        persistence.save_encoded(self._index_file, self._compute_index())
