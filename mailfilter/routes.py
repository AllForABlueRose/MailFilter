"""HTTP layer: thin routes that delegate to store / filters / presenter."""

import logging
import threading
from io import BytesIO

from flask import (
    Blueprint,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
)

from . import outlook
from .filters import MailQuery, filter_mails
from .presenter import to_view_model

log = logging.getLogger(__name__)


def create_blueprint(store, settings):
    bp = Blueprint("mailfilter", __name__)

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
        mails = filter_mails(store.snapshot(), query)
        return jsonify({
            "mails": [to_view_model(m, query.optional) for m in mails],
            **store.status_snapshot(),
        })

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

    return bp


def _find_attachment(store, mail_id, index):
    """Look up a single stored attachment entry, or None if absent."""
    for mail in store.snapshot():
        if mail["id"] == mail_id:
            attachments = mail.get("attachments", [])
            if 0 <= index < len(attachments):
                return attachments[index]
            return None
    return None
