"""Brute Force Mail Deduplication (experimental) — pure, no Flask/HTML/COM.

Some mailbox items are **Zendesk ticket notifications**: after a ticket is created,
a mail carrying the ticket link arrives a short while later, echoing the original
mail's subject and body. This module pairs each such notification with its **twin**
— the original mail within ``±config.DEDUP_WINDOW_MINUTES`` whose subject *and* body
both appear inside the notification's body — so the caller can append the
notification's link to the twin and hide the now-redundant notification.

The work is a **read-only view transform**: it returns which mail ids to hide and
which extra links to graft onto which twins; it never mutates the cache. It reads
the raw ``subject``/``body`` plus the derived ``_received_dt``/``_links`` and ``id``
— no store, mirroring the other pure read-side modules.

Cost: for each notification it scans the mail set for the ±window, i.e.
O(notifications × mails). It only runs when the experimental toggle is on and the
mail set is the bounded cache, so a straightforward scan is fine.
"""

from datetime import timedelta

import config


def dedupe(mails, subject):
    """Return ``(hidden_ids, twin_links)`` for the given ``subject``.

    ``hidden_ids`` is the set of notification mail ids that matched a twin (to hide);
    ``twin_links`` maps **every** matched twin mail id to the de-duplicated list of
    URLs to append to it — a twin whose notification carried no link maps to ``[]``
    (so callers can still tag every twin that was processed). A blank ``subject`` (or
    no matches) yields ``(set(), {})``.

    A mail is a **notification** when its subject *exactly* equals ``subject``
    (case-insensitive, trimmed). A candidate is a **twin** of notification ``N`` when
    it is within the window of ``N`` and its (non-empty) subject and body both appear
    — case-insensitively, as substrings — inside ``N``'s body.
    """
    target = (subject or "").strip().lower()
    if not target:
        return set(), {}

    notifications = [m for m in mails
                     if (m.get("subject", "") or "").strip().lower() == target]
    if not notifications:
        return set(), {}

    window = timedelta(minutes=config.DEDUP_WINDOW_MINUTES)
    hidden, twin_links = set(), {}
    for note in notifications:
        note_dt = note.get("_received_dt")
        if note_dt is None:
            continue
        note_body = (note.get("body", "") or "").lower()
        note_links = note.get("_links", []) or []
        note_id = note.get("id")
        for cand in mails:
            if cand.get("id") == note_id:
                continue
            cand_dt = cand.get("_received_dt")
            if cand_dt is None or abs(cand_dt - note_dt) > window:
                continue
            cand_subject = (cand.get("subject", "") or "").strip()
            cand_body = (cand.get("body", "") or "").strip()
            if not cand_subject or not cand_body:
                continue
            if cand_subject.lower() in note_body and cand_body.lower() in note_body:
                hidden.add(note_id)
                # Record the twin unconditionally (so link-less twins can be tagged);
                # append the notification's links when it had any.
                bucket = twin_links.setdefault(cand.get("id"), [])
                for url in note_links:
                    if url not in bucket:
                        bucket.append(url)
    return hidden, twin_links
