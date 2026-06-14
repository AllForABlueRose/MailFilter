"""HTTP layer: thin routes that delegate to store / filters / presenter."""

import csv
import io
import logging
import threading
from datetime import datetime
from io import BytesIO
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
)

import config

from . import outlook, util
from .filters import MailQuery, filter_mails
from .presenter import to_view_model

log = logging.getLogger(__name__)


def create_blueprint(store, settings, tag_store):
    bp = Blueprint("mailfilter", __name__)

    def view_model(mail, query):
        view = to_view_model(mail, query.main, query.optional,
                             query.attachment_blacklist, query.links_blacklist)
        view["tags"] = tag_store.tags_for(mail["id"])
        return view

    @bp.get("/")
    def index():
        return render_template("index.html")

    @bp.get("/api/settings")
    def get_settings():
        return jsonify(settings.snapshot())

    @bp.post("/api/settings")
    def save_settings():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        return jsonify(settings.update(data))

    @bp.post("/refresh")
    def refresh_now():
        threading.Thread(
            target=outlook.refresh,
            args=(store,),
            daemon=True,
        ).start()
        return jsonify({"status": "started"})

    @bp.get("/api/mail")
    def api_mail():
        query = MailQuery.from_args(request.args)
        status = store.status_snapshot()
        if query.errors:
            # A malformed expression: show nothing and report why, rather than
            # filter on a half-understood query.
            return jsonify({"mails": [], "query_error": " | ".join(query.errors), **status})
        mails = filter_mails(store.snapshot(), query)
        return jsonify({
            "mails": [view_model(m, query) for m in mails],
            "query_error": "",
            **status,
        })

    @bp.get("/api/thread")
    def api_thread():
        # Every mail in the conversation, earliest-first. Highlight with the
        # active search (main/optional) so matches stand out here too; a
        # malformed expression simply highlights nothing.
        query = MailQuery.from_args(request.args)
        mails = store.thread_for(request.args.get("id", ""))
        return jsonify({"mails": [view_model(m, query) for m in mails]})

    @bp.get("/attachments/<mail_id>/<int:index>")
    def download_attachment(mail_id, index):
        # Validate against the cache first: gives a clean 404 for unknown
        # mail/index and a filename fallback if Outlook reports none.
        att = _find_attachment(store, mail_id, index)
        if att is None:
            abort(404)
        try:
            # Bytes are pulled from Outlook on demand — nothing is pre-saved.
            filename, data = outlook.fetch_attachment(mail_id, index)
        except outlook.OutlookUnavailableError as e:
            log.warning("Attachment download unavailable: %s", e)
            abort(503, description=str(e))
        except LookupError as e:
            log.info("Attachment no longer available (%s/%d): %s", mail_id, index, e)
            abort(404)
        return send_file(
            BytesIO(data),
            as_attachment=True,
            download_name=filename or att["filename"],
        )

    @bp.post("/api/download")
    def api_download():
        """Save a batch of attachments to a dated folder on the server.

        Body: ``{"items": [{"id": <mail id>, "index": <int>}, ...]}``. Files go
        into ``<WORKSPACE_DIR>/<YYYY-MM-DD>/`` (created if absent), one at a
        time. Returns the folder and the saved filenames; per-item failures are
        collected in ``errors`` rather than aborting the whole batch.
        """
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        items = data.get("items") or []

        folder = config.WORKSPACE_DIR / datetime.now().strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)

        saved, errors = [], []
        for item in items:
            mail_id = (item or {}).get("id")
            index = (item or {}).get("index")
            att = _find_attachment(store, mail_id, index) if isinstance(index, int) else None
            if att is None:
                errors.append(f"{mail_id}#{index}: unknown attachment")
                continue
            try:
                filename, blob = outlook.fetch_attachment(mail_id, index)
            except outlook.OutlookUnavailableError as e:
                errors.append(str(e))
                continue
            except LookupError as e:
                errors.append(f"{mail_id}#{index}: {e}")
                continue
            target = _unique_path(folder, filename or att["filename"], index)
            target.write_bytes(blob)
            saved.append({"id": mail_id, "index": index, "name": target.name})

        for mid in {s["id"] for s in saved}:
            tag_store.record(mid, "downloaded")

        log.info("Saved %d attachment(s) to %s (%d error(s))", len(saved), folder, len(errors))
        return jsonify({"folder": str(folder), "saved": saved, "errors": errors})

    @bp.post("/api/tags")
    def api_tags():
        # Record (or, with op="remove", clear) a workspace action performed
        # client-side — e.g. links opened, or a mail marked/unmarked.
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        action = data.get("action")
        apply = tag_store.remove if data.get("op") == "remove" else tag_store.record
        for mail_id in data.get("ids") or []:
            apply(mail_id, action)  # ignores unknown actions/ids
        return jsonify({"status": "ok"})

    @bp.post("/api/report")
    def api_report():
        """Export a CSV report of the given mails into the dated workspace folder.

        Body: ``{"ids": [<mail id>, ...]}`` in the order to write. Columns, left
        to right: ``Datetime, subject, recipient, sender``. The filename embeds
        the creation date. Unknown ids are skipped. Returns the folder, the file
        name, and the row count.
        """
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")

        by_id = {m["id"]: m for m in store.snapshot()}
        rows = []
        for mail_id in data.get("ids") or []:
            mail = by_id.get(mail_id)
            if mail is None:
                continue
            rows.append([
                mail.get("received", ""),
                mail.get("subject", ""),
                _people_text(mail.get("recipient_names", []), mail.get("recipient_emails", [])),
                _person_text(mail.get("sender", ""), mail.get("sender_email", "")),
            ])

        now = datetime.now()
        folder = config.WORKSPACE_DIR / now.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        target = _unique_path(folder, f"report_{now.strftime('%Y-%m-%d')}.csv", 0)

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Datetime", "subject", "recipient", "sender"])
        writer.writerows(rows)
        # utf-8-sig so Excel opens the file with the right encoding.
        target.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")

        log.info("Exported report of %d mail(s) to %s", len(rows), target)
        return jsonify({"folder": str(folder), "name": target.name, "count": len(rows)})

    return bp


def _person_text(name, email):
    """Format one person as ``Name <email>`` (falling back to whichever exists)."""
    name = (name or "").strip()
    email = (email or "").strip()
    if name and email:
        return f"{name} <{email}>"
    return name or email


def _people_text(names, emails):
    """Join paired name/email lists into ``Name <email>; ...`` for one CSV cell."""
    people = []
    for i in range(max(len(names), len(emails))):
        text = _person_text(
            names[i] if i < len(names) else "",
            emails[i] if i < len(emails) else "",
        )
        if text:
            people.append(text)
    return "; ".join(people)


def _unique_path(folder, filename, index):
    """A non-colliding path inside ``folder`` for a sanitized ``filename``."""
    safe = util.safe_filename(filename, f"attachment_{index}")
    candidate = folder / safe
    if not candidate.exists():
        return candidate
    stem, suffix = Path(safe).stem, Path(safe).suffix
    n = 1
    while (folder / f"{stem}_{n}{suffix}").exists():
        n += 1
    return folder / f"{stem}_{n}{suffix}"


def _find_attachment(store, mail_id, index):
    """Look up a single stored attachment entry, or None if absent."""
    for mail in store.snapshot():
        if mail["id"] == mail_id:
            attachments = mail.get("attachments", [])
            if 0 <= index < len(attachments):
                return attachments[index]
            return None
    return None
