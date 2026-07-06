"""The Workshop → Calendar engine: pin a workspace file to a day, and (on server
startup) materialize the pins whose day has arrived.

Headless (pure of Flask/COM), mirroring :mod:`mailfilter.unlock_ops`. It imports
**no store** — the caller passes the :class:`~mailfilter.calendar_store.CalendarStore`
in (the same way :mod:`mailfilter.workspace_ops` takes the ``MailStore``), so there
is no store-import cycle. Only touches the filesystem and the folder manifest.

Two entry points:

* :func:`pin_file` — copy a file from **today's** workspace folder into the limbo
  holding folder (``WORKSPACE_DIR/<WORKSPACE_LIMBO_DIRNAME>/``) under a
  non-colliding name and record a pin for a chosen calendar ``date``, carrying the
  file's customer-organization metadata over from the source folder's manifest.
* :func:`materialize_due` — for each non-materialized pin dated **today**: (re)create
  today's dated folder, move the limbo copy in, recreate its manifest org record,
  and flag the pin materialized. Idempotent, so repeated startups on the same day
  never re-copy. Called from the app entry point ("whenever the server is started").
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path

import config

from . import workspace_manifest, workspace_ops

log = logging.getLogger(__name__)


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def today_folder():
    """The dated workspace folder for today (may not exist)."""
    return config.WORKSPACE_DIR / today_str()


def limbo_folder():
    """The limbo holding folder — a sibling of the dated workspace folders."""
    return config.WORKSPACE_DIR / config.WORKSPACE_LIMBO_DIRNAME


def _valid_date(date):
    try:
        datetime.strptime(date, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def pin_file(cal_store, date, filename, description=""):
    """Pin a file from today's workspace folder to calendar ``date``.

    Copies the file's bytes into the limbo folder under a non-colliding name and
    records a pin (with the file's org metadata from the source manifest, plus an
    optional ``description``). Returns ``{"ok": True, "pin": ...}`` or
    ``{"ok": False, "error": ...}`` — the file must exist in today's folder and the
    date must be ``YYYY-MM-DD``.
    """
    if not _valid_date(date):
        return {"ok": False, "error": "invalid date"}
    # Bare filename only — never let a path separator escape today's folder.
    name = Path(str(filename)).name
    if not name:
        return {"ok": False, "error": "invalid filename"}
    src_folder = today_folder()
    src = src_folder / name
    if not src.is_file():
        return {"ok": False, "error": "file not in today's workspace"}

    meta = workspace_manifest.lookup(str(src_folder), name) or {}
    limbo = limbo_folder()
    limbo.mkdir(parents=True, exist_ok=True)
    dest = workspace_ops.unique_path(limbo, name, 0)
    try:
        shutil.copy2(src, dest)
    except OSError as e:
        log.warning("calendar_ops: cannot copy %s to limbo: %s", src, e)
        return {"ok": False, "error": "could not copy file to limbo"}

    pin = cal_store.add({
        "date": date,
        "filename": name,
        "limbo_name": dest.name,
        "description": description,
        "org_id": meta.get("org_id", ""),
        "org_name": meta.get("org_name", ""),
        "mail_id": meta.get("mail_id", ""),
    })
    log.info("Pinned %s to %s (limbo: %s)", name, date, dest.name)
    return {"ok": True, "pin": pin}


def remove_pin(cal_store, pid):
    """Delete a pin and its limbo copy (if the pin has not yet materialized).

    Returns whether a pin was removed. A materialized pin's file already lives in
    its dated folder, so only the (already consumed) limbo copy — if any — is
    cleaned; the record is dropped either way.
    """
    pin = cal_store.get(pid)
    if pin is None:
        return False
    limbo_name = pin.get("limbo_name")
    if limbo_name:
        copy = limbo_folder() / limbo_name
        try:
            if copy.is_file():
                copy.unlink()
        except OSError as e:
            log.warning("calendar_ops: cannot remove limbo copy %s: %s", copy, e)
    return cal_store.remove(pid)


def materialize_due(cal_store):
    """Materialize every non-materialized pin dated today. Returns file names moved.

    For each such pin: (re)create today's dated folder, move the limbo copy in
    under a non-colliding name, recreate the file's org metadata in the folder
    manifest, and flag the pin materialized (so a later restart is a no-op). A pin
    whose limbo copy has vanished is flagged materialized without a move so it does
    not retry forever.
    """
    today = today_str()
    folder = today_folder()
    moved = []
    for pin in cal_store.snapshot():
        if pin.get("materialized") or pin.get("date") != today:
            continue
        limbo = limbo_folder() / pin.get("limbo_name", "")
        if not pin.get("limbo_name") or not limbo.is_file():
            log.warning("calendar_ops: limbo copy for pin %s is missing (%s)",
                        pin["id"], limbo)
            cal_store.mark_materialized(pin["id"], "")
            continue
        folder.mkdir(parents=True, exist_ok=True)
        target = workspace_ops.unique_path(folder, pin.get("filename") or limbo.name, 0)
        try:
            shutil.move(str(limbo), str(target))
        except OSError as e:
            log.warning("calendar_ops: cannot materialize pin %s: %s", pin["id"], e)
            continue
        workspace_manifest.record(str(folder), target.name, {
            "org_id": pin.get("org_id", ""),
            "org_name": pin.get("org_name", ""),
            "mail_id": pin.get("mail_id", ""),
        })
        cal_store.mark_materialized(pin["id"], str(folder))
        moved.append(target.name)
        log.info("Materialized pinned file %s into %s", target.name, folder)
    if moved:
        log.info("Calendar: materialized %d pinned file(s) for %s", len(moved), today)
    return moved
