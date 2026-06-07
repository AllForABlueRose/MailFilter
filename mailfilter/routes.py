"""HTTP layer: thin routes that delegate to store / filters / presenter."""

import threading

from flask import Blueprint, jsonify, render_template, request

from . import outlook
from .filters import MailQuery, filter_mails
from .presenter import to_view_model


def create_blueprint(store):
    bp = Blueprint("mailfilter", __name__)

    @bp.get("/")
    def index():
        return render_template("index.html")

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

    return bp
