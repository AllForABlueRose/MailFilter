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

from . import automation, customers, outlook, util, workspace_ops
from .filters import MailQuery, filter_mails
from .presenter import to_view_model

log = logging.getLogger(__name__)


def create_blueprint(store, settings, tag_store, template_store, automation_store,
                     customer_store):
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

    @bp.get("/api/templates")
    def list_templates():
        return jsonify(template_store.snapshot())

    @bp.post("/api/templates")
    def save_template():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        try:
            return jsonify(template_store.save(data.get("name"), data.get("settings")))
        except ValueError as e:
            abort(400, description=str(e))

    @bp.delete("/api/templates/<name>")
    def delete_template(name):
        return jsonify(template_store.delete(name))

    @bp.post("/api/templates/export")
    def export_template():
        """Export one template as a PNG image file (its bytes packed into the
        pixels). Body: ``{"name": <template name>}``. Returns an ``image/png``
        download — not JSON, not encrypted, not human-legible — that
        ``/api/templates/import`` round-trips. See mailfilter/imgcodec.py."""
        data = request.get_json(silent=True) or {}
        name = data.get("name")
        png = template_store.export_image(name)
        if png is None:
            abort(404, description="unknown template")
        download = util.safe_filename(f"{name}.png", "template.png")
        return send_file(
            BytesIO(png),
            mimetype="image/png",
            as_attachment=True,
            download_name=download,
        )

    @bp.post("/api/templates/import")
    def import_template():
        """Import a template from an uploaded PNG produced by the export route.

        Multipart form field ``file``. The image is decoded back to a template and
        saved (overwriting a same-named one). Returns the snapshot plus
        ``imported`` (the template's name)."""
        upload = request.files.get("file")
        if upload is None:
            abort(400, description="no file uploaded")
        try:
            name, snapshot = template_store.import_image(upload.read())
        except (ValueError, KeyError, TypeError):
            abort(400, description="not a valid template image")
        return jsonify({"imported": name, **snapshot})

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
        att = workspace_ops.find_attachment(store, mail_id, index)
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

        folder, saved, errors = workspace_ops.save_attachments(store, data.get("items") or [])
        for mid in {s["id"] for s in saved}:
            tag_store.record(mid, "downloaded")
        return jsonify({"folder": folder, "saved": saved, "errors": errors})

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

        folder, name, count = workspace_ops.write_report(store, data.get("ids") or [])
        return jsonify({"folder": folder, "name": name, "count": count})

    # ----- Automations -----

    @bp.get("/api/automations")
    def list_automations():
        return jsonify({"automations": automation_store.snapshot()})

    @bp.post("/api/automations")
    def create_automation():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        return jsonify(automation_store.create(data))

    @bp.put("/api/automations/<aid>")
    def update_automation(aid):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        updated = automation_store.update(aid, data)
        if updated is None:
            abort(404)
        return jsonify(updated)

    @bp.delete("/api/automations/<aid>")
    def delete_automation(aid):
        automation_store.delete(aid)
        return jsonify({"automations": automation_store.snapshot()})

    @bp.post("/api/automations/<aid>/toggle")
    def toggle_automation(aid):
        data = request.get_json(silent=True) or {}
        updated = automation_store.set_enabled(aid, bool(data.get("enabled")))
        if updated is None:
            abort(404)
        return jsonify(updated)

    @bp.post("/api/automations/<aid>/run")
    def run_automation_now(aid):
        by_id = {a["id"]: a for a in automation_store.snapshot()}
        auto = by_id.get(aid)
        if auto is None:
            abort(404)

        def _go():
            status = automation.run_automation(auto, store, tag_store)
            if status is not None:
                automation_store.mark_run(aid, status)

        threading.Thread(target=_go, daemon=True).start()
        return jsonify({"status": "started"})

    # ----- Customer Management -----

    @bp.get("/api/organizations")
    def list_organizations():
        return jsonify({"organizations": customer_store.snapshot()})

    @bp.post("/api/organizations")
    def create_organization():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        return jsonify(customer_store.create(data))

    @bp.put("/api/organizations/<oid>")
    def update_organization(oid):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        updated = customer_store.update(oid, data)
        if updated is None:
            abort(404)
        return jsonify(updated)

    @bp.delete("/api/organizations/<oid>")
    def delete_organization(oid):
        customer_store.delete(oid)
        return jsonify({"organizations": customer_store.snapshot()})

    @bp.post("/api/organizations/<oid>/domains")
    def add_organization_domain(oid):
        # Drag-a-domain onto an org: map the whole domain to it (default "member"),
        # so everyone on that domain resolves to the org. Moves the domain off any
        # other org first (set_domain).
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        org = customer_store.set_domain(oid, data.get("domain"), data.get("role", "member"))
        if org is None:
            abort(404, description="unknown organization or blank domain")
        return jsonify(org)

    @bp.get("/api/contacts")
    def list_contacts():
        # The contact directory is derived live from the mail cache and resolved
        # against the org definitions. Both snapshots are copies; take them
        # back-to-back so a concurrent assign can't split the view.
        directory = customers.build_directory(store.snapshot(), customer_store.snapshot())
        return jsonify({"contacts": directory})

    @bp.post("/api/contacts/assign")
    def assign_contact():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        # A representative must have a base organization first: you record who a
        # contact works for (Member) before who they front for (Representative).
        if data.get("role") == "representative" \
                and not customer_store.has_member_base(data.get("email")):
            abort(409, description="set the contact's base organization (a Member "
                                   "assignment) before assigning them as a Representative")
        org = customer_store.assign(data.get("email"), data.get("org_id"), data.get("role"))
        if org is None:
            abort(404, description="unknown organization or blank email")
        return jsonify(org)

    @bp.post("/api/contacts/unassign")
    def unassign_contact():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        return jsonify({"removed": customer_store.unassign(data.get("email"))})

    return bp
