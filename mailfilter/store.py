"""Thread-safe mail cache: persistence, derived fields, thread detection.

The MailStore is the single owner of all mutable state (the mail list and
the fetch status). Every public method takes the internal lock, so it is
safe to call from the Flask request threads, the background refresh
thread, and ad-hoc /refresh threads at the same time.

Keys prefixed with "_" (e.g. ``_received_dt``, ``_search_text``) are
derived once at ingest time so filtering does no per-request parsing.
They are stripped again before the cache is written to disk.
"""

import logging
import re
import threading
from datetime import datetime
from pathlib import Path

from config import RECEIVED_FORMAT

from . import crypto, persistence

log = logging.getLogger(__name__)

# Only http(s) URLs are treated as links: this keeps "javascript:" and other
# schemes out of the clickable links the UI renders.
_LINK_RE = re.compile(r"""https?://[^\s<>"')]+""")

# Start of the quoted history in a reply/forward. A threaded message's body
# carries the full quoted conversation beneath the new text, so link extraction
# would otherwise surface every link from the whole thread on a single message.
# We cut at the first of these boundaries so only the message's own links show.
_QUOTE_BOUNDARY_RE = re.compile(
    r"""
      ^\s*-{2,}\s*Original\s+Message\s*-{2,}    # -----Original Message-----
    | ^\s*_{5,}\s*$                              # Outlook underscore divider
    | ^\s*From:\s.+$                             # Outlook reply/forward header
    | ^\s*On\s.+\bwrote:\s*$                     # "On <date>, <name> wrote:"
    | ^\s*>                                      # plain-text quoted line
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)


def own_message_body(body):
    """Return only the new portion of ``body``, dropping any quoted reply history."""
    if not body:
        return body or ""
    match = _QUOTE_BOUNDARY_RE.search(body)
    return body[: match.start()] if match else body


def extract_links(text):
    """Return the unique http(s) URLs in ``text``, in first-seen order."""
    seen = set()
    links = []
    for raw in _LINK_RE.findall(text or ""):
        url = raw.rstrip(".,;:!?)'\"")
        if url and url not in seen:
            seen.add(url)
            links.append(url)
    return links


def _strip_derived(mail):
    return {k: v for k, v in mail.items() if not k.startswith("_")}


class MailStore:

    def __init__(self, cache_file):
        self._cache_file = Path(cache_file)
        self._lock = threading.RLock()
        self._mails = []
        self._last_refresh = None
        self._fetch_status = "Not started"
        self._fetch_error = ""
        self._fetch_progress = ""

    # ----- persistence -----

    def load(self):
        raw, alg = persistence.load_encoded(self._cache_file)
        if raw is None:
            return
        mails = []
        for entry in raw:
            try:
                mails.append(self._with_derived(entry))
            except Exception:
                log.warning(
                    "Skipping malformed cache entry: %r",
                    entry.get("id", "<no id>") if isinstance(entry, dict) else entry,
                )
        with self._lock:
            self._mails = mails
            self._rebuild_threads()
            self._sort()
            # Upgrade the on-disk encoding if a stronger scheme is now available
            # (legacy plaintext -> obfuscated/encrypted, or base64 -> DPAPI).
            if alg != crypto.preferred_alg():
                self._save()
                log.info(
                    "Migrated cache on disk to %s",
                    crypto.alg_name(crypto.preferred_alg()),
                )
        log.info("Loaded %d mails from cache", len(mails))

    def _save(self):
        # Caller must hold the lock.
        persistence.save_encoded(self._cache_file, [_strip_derived(m) for m in self._mails])

    # ----- derived fields -----

    @staticmethod
    def _with_derived(mail):
        mail = dict(mail)
        sender_text = " ".join(
            [mail.get("sender", ""), mail.get("sender_email", "")]
        ).lower()
        # Recipient search/exclude covers everyone the mail went to — To and CC.
        recipient_text = " ".join(
            mail.get("recipient_names", []) + mail.get("recipient_emails", [])
            + mail.get("cc_names", []) + mail.get("cc_emails", [])
        ).lower()
        mail["_received_dt"] = datetime.strptime(mail["received"], RECEIVED_FORMAT)
        mail["_sender_text"] = sender_text
        mail["_recipient_text"] = recipient_text
        # Links come from this message's own text only — not the quoted thread
        # history a reply carries below it. (Attachments are already per-item:
        # Outlook scopes its Attachments collection to the single MailItem.)
        mail["_links"] = extract_links(own_message_body(mail.get("body", "")))
        mail["_has_links"] = bool(mail["_links"])
        mail["_has_attachments"] = bool(mail.get("attachments"))
        # Keyword search (main / exclude) is subject + body only — sender and
        # recipient have their own dedicated fields (_sender_text/_recipient_text).
        mail["_search_text"] = "\n".join(
            [
                mail.get("subject", "").lower(),
                mail.get("body", "").lower(),
            ]
        )
        # Smart Password Detection results. Populated on demand by
        # apply_password_scan() (the manual scan), not at ingest, so newly-fetched
        # mail simply carries no detected password until the next scan. Like the
        # other "_" fields they are stripped before the cache is written.
        mail["_passwords"] = []
        mail["_has_password"] = False
        return mail

    # ----- mutation -----

    def add_mails(self, new_mails):
        """Ingest fetched mails (deduplicated by id). Returns how many were added."""
        with self._lock:
            existing = {m["id"] for m in self._mails}
            added = 0
            for mail in new_mails:
                if mail["id"] in existing:
                    continue
                self._mails.append(self._with_derived(mail))
                existing.add(mail["id"])
                added += 1
            if added:
                self._rebuild_threads()
                self._sort()
                self._save()
            return added

    def apply_password_scan(self, matches_by_id):
        """Record a password-detection scan's results onto the live mails.

        ``matches_by_id`` maps a mail ``id`` to the list of detected password
        strings for it (ids with no match may be omitted). Sets each mail's
        runtime ``_passwords`` / ``_has_password`` fields under the lock, so a
        concurrent ``/api/mail`` read sees a consistent set, and clears the flag
        on mails absent from the map. Returns how many mails are now flagged.

        These fields are derived (``_``-prefixed): they are never persisted, so a
        scan does not touch the cache file. Re-run any time the patterns/rules
        change to refresh them.
        """
        flagged = 0
        with self._lock:
            for mail in self._mails:
                found = matches_by_id.get(mail["id"], [])
                mail["_passwords"] = found
                mail["_has_password"] = bool(found)
                if found:
                    flagged += 1
        return flagged

    def _rebuild_threads(self):
        # Caller must hold the lock.
        counts = {}
        for mail in self._mails:
            cid = mail["conversation_id"]
            counts[cid] = counts.get(cid, 0) + 1
        for mail in self._mails:
            mail["is_thread"] = counts[mail["conversation_id"]] > 1

    def _sort(self):
        # Caller must hold the lock. Newest first.
        self._mails.sort(key=lambda m: m["_received_dt"], reverse=True)

    # ----- reads -----

    def snapshot(self):
        """A consistent copy of the mail list, safe to iterate without the lock."""
        with self._lock:
            return list(self._mails)

    def thread_for(self, mail_id):
        """All mails sharing ``mail_id``'s conversation, earliest received first.

        Ignores the current search filters — the thread view shows the whole
        conversation. Returns ``[]`` if the id is unknown.
        """
        with self._lock:
            target = next((m for m in self._mails if m["id"] == mail_id), None)
            if target is None:
                return []
            cid = target["conversation_id"]
            members = [m for m in self._mails if m["conversation_id"] == cid]
        members.sort(key=lambda m: m["_received_dt"])
        return members

    def known_ids(self):
        with self._lock:
            return {m["id"] for m in self._mails}

    def latest_received(self):
        with self._lock:
            if not self._mails:
                return None
            return max(m["_received_dt"] for m in self._mails)

    # ----- fetch status -----

    def set_fetching(self):
        with self._lock:
            self._fetch_status = "Fetching..."
            self._fetch_error = ""
            self._fetch_progress = ""

    def set_progress(self, text):
        """Mid-fetch progress line (e.g. "Initial sync: 1,200/8,003 mails (15%)").

        Surfaced through :meth:`status_snapshot` so the UI status box can show a
        long initial sync advancing. Cleared by the terminal status setters.
        """
        with self._lock:
            self._fetch_progress = text

    def set_success(self, fetched_count):
        with self._lock:
            self._last_refresh = datetime.now()
            self._fetch_status = f"Success ({fetched_count} new)"
            self._fetch_error = ""
            self._fetch_progress = ""

    def set_failure(self, error):
        with self._lock:
            self._fetch_status = "Failed"
            self._fetch_error = str(error)
            self._fetch_progress = ""

    def status_snapshot(self):
        with self._lock:
            return {
                "last_refresh": (
                    self._last_refresh.strftime(RECEIVED_FORMAT)
                    if self._last_refresh
                    else "Never"
                ),
                "fetch_status": self._fetch_status,
                "fetch_error": self._fetch_error,
                "fetch_progress": self._fetch_progress,
            }
