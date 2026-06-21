"""Headless execution of automations.

An automation is a saved MailFilter search plus an ordered set of action steps
(see ``config.AUTOMATION_STEPS``). Running one filters the cached mail by the
saved query and performs each selected step on the matches, reusing the very
same operations the UI exposes:

    mark      -> apply the local "marked" tag (the one emoji label we have today)
    download  -> save matched attachments into the dated workspace folder
    report    -> export a CSV report of the matches

It is read-only against Outlook (mark/report touch only local state; download
copies attachment bytes, which the app already does on demand). Definitions and
persistence live in ``automation_store.py``; this module only *runs* them.

Dependency direction: automation -> workspace_ops, filters (-> expr); the store
and tag store are passed in. Nothing here imports automation_store, so there is
no cycle.
"""

import logging
import threading
from datetime import datetime

import config

from . import workspace_ops
from .filters import MailQuery, filter_mails

log = logging.getLogger(__name__)

# Guards against a manual "run now" and a scheduler tick executing the same
# automation at once (the second caller is simply skipped).
_running = set()
_running_lock = threading.Lock()


def _query_args(query):
    """Adapt a stored query dict (settings-schema shape) to MailQuery.from_args.

    ``resources`` is a bool in the stored schema but ``from_args`` reads it as the
    string the query-string carries, so translate it.
    """
    args = dict(query or {})
    args["resources"] = "1" if (query or {}).get("resources") else ""
    return args


def run_automation(automation, store, tag_store):
    """Run one automation now; return a short human-readable status string.

    Returns ``None`` if the automation is already running (so the caller leaves
    the previous ``last_run``/``last_status`` untouched).
    """
    aid = automation.get("id")
    with _running_lock:
        if aid in _running:
            return None
        _running.add(aid)
    try:
        return _execute(automation, store, tag_store)
    except Exception:
        log.exception("Automation %s failed", aid)
        return "error (see server log)"
    finally:
        with _running_lock:
            _running.discard(aid)


def _execute(automation, store, tag_store):
    query = MailQuery.from_args(_query_args(automation.get("query")))
    if query.errors:
        return "query error: " + "; ".join(query.errors)

    mails = filter_mails(store.snapshot(), query)
    steps = [s for s in config.AUTOMATION_STEPS if s in (automation.get("steps") or [])]
    parts = [f"{len(mails)} matched"]
    if not mails or not steps:
        return "; ".join(parts) if steps else f"{len(mails)} matched (no steps)"

    ids = [m["id"] for m in mails]

    if "mark" in steps:
        for mid in ids:
            tag_store.record(mid, "marked")
        parts.append(f"marked {len(ids)}")

    if "download" in steps:
        items = [
            {"id": m["id"], "index": i}
            for m in mails
            for i in range(len(m.get("attachments", [])))
        ]
        _folder, saved, errors = workspace_ops.save_attachments(store, items)
        for mid in {s["id"] for s in saved}:
            tag_store.record(mid, "downloaded")
        parts.append(f"saved {len(saved)} attachment(s)")
        if errors:
            parts.append(f"{len(errors)} download error(s)")

    if "report" in steps:
        _folder, name, count = workspace_ops.write_report(store, ids)
        parts.append(f"report {name} ({count} rows)")

    return "; ".join(parts)


def run_due_automations(automation_store, store, tag_store):
    """Scheduler tick: run every enabled automation whose interval has elapsed.

    Called periodically (every ``config.AUTOMATION_TICK_SECONDS``). An automation
    with no recorded ``last_run`` is due immediately. Runs are sequential within
    the tick; a slow one simply delays the rest until the next tick.
    """
    now = datetime.now()
    for automation in automation_store.snapshot():
        if not automation.get("enabled"):
            continue
        if not _is_due(automation, now):
            continue
        status = run_automation(automation, store, tag_store)
        if status is not None:
            automation_store.mark_run(automation["id"], status)


def _is_due(automation, now):
    last = automation.get("last_run")
    if not last:
        return True
    try:
        last_dt = datetime.strptime(last, config.RECEIVED_FORMAT)
    except ValueError:
        return True
    interval = automation.get("interval_seconds") or config.AUTOMATION_DEFAULT_INTERVAL_SECONDS
    return (now - last_dt).total_seconds() >= interval
