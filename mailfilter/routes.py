"""HTTP layer: thin routes that delegate to store / filters / presenter."""

import csv
import io
import json
import logging
import threading
from datetime import datetime, timedelta
from io import BytesIO

from flask import (
    Blueprint,
    abort,
    jsonify,
    render_template,
    request,
    send_file,
)
from werkzeug.exceptions import HTTPException

import config

from . import (
    automation,
    bulk_compose,
    calendar_ops,
    composer,
    customers,
    dedup,
    draft_ops,
    mail_picker,
    outlook,
    password_detect,
    press,
    spreadsheet,
    unlock_ops,
    util,
    workspace_manifest,
    workspace_ops,
)
from . import compose_template_store as compose_template_store_mod
from .filters import MailQuery, filter_mails
from .presenter import extra_link_views, to_view_model

log = logging.getLogger(__name__)


# ----- Smart Password Detection scan + Key Vault capture (module-level so the
# scheduler / refresh path can reuse them, not just the route closures) -----

def resolve_org_id(customer_store, email):
    """The org id a sender resolves to (representative-of preferred), or None."""
    res = customers.resolve(email, customer_store.snapshot())
    return res.get("rep_org_id") or res.get("member_org_id")


def capture_scanned_passwords(store, vault_store, customer_store,
                              experimental_store, customer_match_store):
    """Record the latest scan's detected passwords into the unlocked vault, filed under
    each mail's resolved customer org (or the Unassigned bucket). Idempotent; returns
    how many **genuinely new** keys were added (a re-detected password that dedups onto
    an existing key does not count).

    Org resolution uses the same shared resolver as the mail-list pill / download / CSV
    (``customers.mail_org_resolver``): the Brute Force keyword tier is honoured when the
    ``resolve_customer_name`` experimental feature is on, otherwise sender resolution
    (representative > member). A captured key is stamped with the source mail's received
    datetime (not the scan time), so its ``scan_dt`` reflects when the password arrived."""
    mappings = (customer_match_store.mappings()
                if experimental_store.snapshot().get("resolve_customer_name") else None)
    resolve_org = customers.mail_org_resolver(customer_store.snapshot(), mappings)
    captured = 0
    for mail in store.snapshot():
        secrets = mail.get("_passwords") or []
        if not secrets:
            continue
        email = mail.get("sender_email") or ""
        org = resolve_org(mail)
        org_id = (org.get("id") if org else None) or config.VAULT_UNASSIGNED_ORG_ID
        received = mail.get("received")
        for secret in secrets:
            _entry, created = vault_store.capture_scan(
                org_id, secret, scan_dt=received, source_email=email)
            if created:
                captured += 1
    return captured


def run_password_scan(store, password_settings, vault_store, customer_store,
                      experimental_store, customer_match_store):
    """Compile patterns, scan recent cached mail, record hits, and (while the vault is
    unlocked) auto-capture them. Shared by POST /api/passwords/scan, the on-unlock
    auto-scan, and the post-refresh hook. Returns the JSON-able result dict.

    No-op when the Smart Password Detection experimental feature is off (the scan must
    not run at all then), and only scans mail received within
    ``config.PASSWORD_SCAN_MAX_AGE_DAYS`` — older mail is left unscanned/uncaptured."""
    if not experimental_store.snapshot().get("passwords"):
        return {
            "scanned": 0, "flagged": 0, "pattern_errors": [],
            "vault_captured": 0, "vault_pending": 0,
            "vault_locked": not vault_store.is_unlocked(), "skipped": True,
        }
    snap = password_settings.snapshot()
    compiled, errors = password_detect.compile_patterns(snap["patterns"])
    rules = snap["rules"]
    cutoff = datetime.now() - timedelta(days=config.PASSWORD_SCAN_MAX_AGE_DAYS)
    mails = store.snapshot()
    matches = {}
    scanned = 0
    for mail in mails:
        received = mail.get("_received_dt")
        if received is not None and received < cutoff:
            continue                                  # older than the scan window
        scanned += 1
        text = "\n".join([mail.get("subject", ""), mail.get("body", "")])
        found = password_detect.scan_text(
            text, compiled, rules, config.PASSWORD_MAX_MATCHES_PER_MAIL)
        if found:
            matches[mail["id"]] = found
    flagged = store.apply_password_scan(matches)
    unlocked = vault_store.is_unlocked()
    captured = (capture_scanned_passwords(
        store, vault_store, customer_store, experimental_store, customer_match_store)
        if unlocked else 0)
    pending = 0 if unlocked else sum(len(v) for v in matches.values())
    if unlocked:
        log.info("SDS scan: %d mail(s) scanned, %d flagged, %d new key(s) captured",
                 scanned, flagged, captured)
    else:
        log.info("SDS scan: %d mail(s) scanned, %d flagged, %d key(s) pending "
                 "(vault locked)", scanned, flagged, pending)
    return {
        "scanned": scanned,
        "flagged": flagged,
        "pattern_errors": [{"component": n, "error": e} for n, e in errors],
        "vault_captured": captured,
        "vault_pending": pending,
        "vault_locked": not unlocked,
    }


def refresh_then_scan(store, password_settings, vault_store, customer_store,
                      experimental_store, customer_match_store):
    """Refresh callback for the scheduler and POST /refresh: fetch + sync mail from
    Outlook, then run an SDS scan so badges/captures reflect newly-arrived mail. The
    scan is read-only against the mailbox and only writes the vault when unlocked."""
    outlook.refresh(store)
    run_password_scan(store, password_settings, vault_store, customer_store,
                      experimental_store, customer_match_store)


def create_blueprint(store, settings, tag_store, template_store, automation_store,
                     customer_store, compose_template_store, password_settings,
                     experimental_store, customer_match_store, vault_store,
                     calendar_store, mailbox_store, category_store):
    bp = Blueprint("mailfilter", __name__)

    @bp.app_errorhandler(HTTPException)
    def _api_error_as_json(e):
        """An aborted /api/ call answers with JSON, not Flask's HTML error page.

        The frontend reads ``description`` to tell the user *why* (an unverified
        mailbox, an unreachable Outlook, a bad upload). Without this it would parse an
        HTML page as JSON and surface a syntax error instead of the reason.
        """
        if not request.path.startswith("/api/"):
            return e
        return jsonify({"error": e.name, "description": e.description}), e.code

    def view_model(mail, query, resolve_org=None, hide_safe_links=False):
        view = to_view_model(mail, query.main, query.optional,
                             query.attachment_blacklist, query.links_blacklist,
                             hide_safe_links=hide_safe_links)
        view["tags"] = tag_store.tags_for(mail["id"])
        # The mail's single resolved customer organization, when a resolver is
        # supplied (built once per request). One shared source (Brute Force > rep >
        # sender member) drives this pill, the download name, and the CSV column; the
        # pill uses the display name. Attached here like `tags` — the presenter must
        # not import the customer store. A blank list when unresolved.
        org = resolve_org(mail) if resolve_org else None
        view["org_labels"] = [customers.org_label(org)] if org else []
        return view

    def _internal_domains():
        """The domains ``sender.is_internal`` is true for, built once per request.

        The user's own domain comes from the mailbox they **verified** against Outlook
        (an unverified one proves nothing about who you are, so it is not used); the
        rest come from the Partner organizations in Customer Management. See
        ``customers.internal_domains``."""
        personal = mailbox_store.get("personal") or {}
        own = personal["address"] if personal.get("status") == "verified" else ""
        return customers.internal_domains(customer_store.snapshot(), own)

    def _mail_org_resolver():
        """The per-request mail->org resolver: the Brute Force keyword tier is active
        only when its experimental feature is enabled (the pill agrees with the
        download/CSV single source)."""
        mappings = (customer_match_store.mappings()
                    if experimental_store.snapshot().get("resolve_customer_name") else None)
        return customers.mail_org_resolver(customer_store.snapshot(), mappings)

    def _resolve_org_id(email):
        return resolve_org_id(customer_store, email)

    def _capture_scanned_to_vault():
        return capture_scanned_passwords(
            store, vault_store, customer_store, experimental_store, customer_match_store)

    def _flush_vault_captures():
        """Re-home parked captures, then record pending scan hits. No-op while
        locked — called after a Customer Management assignment so the captures
        queued by a locked-vault scan land in the right org once it's mapped."""
        if not vault_store.is_unlocked():
            return
        vault_store.rehome_unassigned(_resolve_org_id)
        _capture_scanned_to_vault()

    def _vault_org_names():
        """`{org_id: searchable name text}` for vault search, plus the Unassigned
        bucket label — built here so `vault_store` needs no customer-store import. The
        text combines the org's official `name` and its `display_name` so a key search
        matches either (the official name stays findable even when a display name is
        set)."""
        names = {config.VAULT_UNASSIGNED_ORG_ID: "Unassigned"}
        for org in customer_store.snapshot():
            parts = [org.get("name", ""), org.get("display_name", "")]
            names[org["id"]] = " ".join(p.strip() for p in parts if p and p.strip())
        return names

    def _run_password_scan():
        return run_password_scan(store, password_settings, vault_store, customer_store,
                                 experimental_store, customer_match_store)

    def _on_vault_unlocked():
        """After a successful create/unlock: re-home parked captures, then auto-scan
        so detection + capture run immediately (the 'invisible queue' — logging back
        in records everything outstanding without a manual scan)."""
        if not vault_store.is_unlocked():
            return
        vault_store.rehome_unassigned(_resolve_org_id)
        _run_password_scan()

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
        # Fetch + sync, then run the SDS scan so badges/captures reflect new mail.
        threading.Thread(
            target=refresh_then_scan,
            args=(store, password_settings, vault_store, customer_store,
                  experimental_store, customer_match_store),
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
        snap = store.snapshot()
        mails = filter_mails(snap, query)
        # Brute Force Mail Deduplication (experimental, view-only): computed over the
        # full snapshot (a twin/notification may fall outside the current search),
        # then projected onto the results — hide matched notifications, graft their
        # link(s) onto the twin's view model.
        hidden, twin_links = (set(), {})
        if query.dedupe and query.dedupe_subject.strip():
            hidden, twin_links = dedup.dedupe(snap, query.dedupe_subject)
            # Tag each processed twin (record-once, so the 🧬 label ages from first
            # processing rather than being re-stamped on every poll). Done before the
            # view-model loop so tags_for reflects it on this same response.
            for twin_id in twin_links:
                tag_store.record_once(twin_id, "deduped")
        resolve_org = _mail_org_resolver()
        hide_safe = bool(experimental_store.snapshot().get("hide_safe_links"))
        out = []
        for m in mails:
            if m["id"] in hidden:
                continue
            view = view_model(m, query, resolve_org, hide_safe_links=hide_safe)
            urls = twin_links.get(m["id"])
            if urls:
                view["links"] = view["links"] + extra_link_views(
                    urls, query.main, query.optional, [l["url"] for l in view["links"]],
                    blacklist=query.links_blacklist, hide_safe_links=hide_safe)
            out.append(view)
        return jsonify({"mails": out, "query_error": "", **status})

    @bp.get("/api/thread")
    def api_thread():
        # Every mail in the conversation, earliest-first. Highlight with the
        # active search (main/optional) so matches stand out here too; a
        # malformed expression simply highlights nothing.
        query = MailQuery.from_args(request.args)
        mails = store.thread_for(request.args.get("id", ""))
        resolve_org = _mail_org_resolver()
        hide_safe = bool(experimental_store.snapshot().get("hide_safe_links"))
        return jsonify({"mails": [view_model(m, query, resolve_org, hide_safe_links=hide_safe)
                                  for m in mails]})

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

        Body: ``{"items": [{"id": <mail id>, "index": <int>}, ...],
        "append_customer_name": <bool>}``. Files go into
        ``<WORKSPACE_DIR>/<YYYY-MM-DD>/`` (created if absent), one at a time. The
        mail's org comes from the shared single source; its **Brute Force keyword
        tier is governed by that experimental feature's enablement** (same as the
        pill and CSV — no per-request toggle), so the download can't disagree with
        them. ``append_customer_name`` is a per-request action gated by both the
        request AND its experimental feature: when on it appends ``_<org name>`` to a
        saved file's stem. Returns the folder and the saved filenames; per-item
        failures are collected in ``errors`` rather than aborting the batch.
        """
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")

        flags = experimental_store.snapshot()
        append = (bool(flags.get("append_customer_name"))
                  and data.get("append_customer_name") in (True, "1", "true", "on"))
        # Always resolve the mail's org for the manifest (the shared single source);
        # the Brute Force keyword tier follows its experimental enablement, `append`
        # only controls the filename suffix.
        orgs = customer_store.snapshot()
        customer_mappings = (customer_match_store.mappings()
                             if flags.get("resolve_customer_name") else None)
        folder, saved, errors = workspace_ops.save_attachments(
            store, data.get("items") or [], append_org_name=append, orgs=orgs,
            resolve_customer=bool(customer_mappings), customer_mappings=customer_mappings)
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
        to right: ``Datetime, subject, recipient, sender, customer organization``.
        The last column is the mail's org from the shared single source (Brute Force
        keyword > representative > sender member); the Brute Force keyword tier is
        included only when its experimental feature is enabled (scoped server-side).
        The filename embeds the creation date. Unknown ids are skipped. Returns the
        folder, the file name, and the row count.
        """
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")

        mappings = (customer_match_store.mappings()
                    if experimental_store.snapshot().get("resolve_customer_name") else None)
        folder, name, count = workspace_ops.write_report(
            store, data.get("ids") or [],
            mappings=mappings, orgs=customer_store.snapshot())
        return jsonify({"folder": folder, "name": name, "count": count})

    @bp.post("/api/workspace/cleanup")
    def api_workspace_cleanup():
        """Delete this app's downloaded files from today's workspace folder.

        No body. Only files recorded in the folder's sidecar manifest
        (:func:`workspace_manifest.is_app_file`) are removed; incidental files a
        user placed in the folder are left in place. Returns the folder, the
        deleted file names, and how many were kept.
        """
        folder, deleted, kept = workspace_ops.cleanup_workspace()
        return jsonify({"folder": folder, "deleted": deleted, "kept_count": len(kept)})

    @bp.post("/api/workspace/bring-last")
    def api_workspace_bring_last():
        """Rename the most recent past dated workspace folder to today's date.

        No body. Used by Workbench Processing's "Bring Last Workspace to Today":
        turns the latest earlier ``YYYY-MM-DD`` folder into today's workspace. No
        secrets, so no vault gate. Returns ``{ok, folder/source/error}``; **409**
        when today's folder already exists or there is nothing to carry forward.
        """
        result = workspace_ops.bring_last_workspace_to_today()
        return jsonify(result), (200 if result.get("ok") else 409)

    @bp.post("/api/workspace/file-org")
    def api_workspace_file_org():
        """Set or clear a today's-workspace file's customer organization.

        Body ``{filename, org_id}``. Used by Workbench Processing's "Stamp Customer
        Organization": writes the file's org into the folder manifest (making a
        user-placed file app-managed). A blank ``org_id`` clears the org. No secrets,
        so no vault gate. **400** for an unknown org, **404** when the file isn't in
        today's workspace.
        """
        body = request.get_json(silent=True) or {}
        filename = body.get("filename") or ""
        org_id = str(body.get("org_id") or "")
        org_name = ""
        if org_id:
            org = next((o for o in customer_store.snapshot() if o["id"] == org_id), None)
            if org is None:
                return jsonify({"ok": False, "error": "unknown organization"}), 400
            org_name = org.get("name", "")
        result = workspace_ops.stamp_file_org(filename, org_id, org_name)
        if not result.get("ok"):
            return jsonify(result), 404
        return jsonify(result)

    # ----- Unlock Station (Key Vault ⇄ workspace files) -----

    def _vault_entry_index():
        """`{entry_id: redacted_entry}` across every org (requires unlocked)."""
        idx = {}
        for items in vault_store.entries_by_org().values():
            for e in items:
                idx[e["id"]] = e
        return idx

    def _build_unlock_assignments(files, resolve_entry_id, entry_idx):
        """Turn the workspace file list into the engine's ``{filename: assignment}``.

        ``resolve_entry_id(file) -> entry_id|None`` decides which key (if any) a
        file gets — the client's drag map for a manual unlock, or a recorded org
        pattern for the smart pass. Only zips (with or without a key) and Excel
        files that actually got a key are included; other files are left alone.
        """
        assignments = {}
        for f in files:
            entry_id = resolve_entry_id(f)
            secret, key_kind = None, None
            if entry_id and entry_id in entry_idx:
                secret = vault_store.reveal(entry_id)
                key_kind = entry_idx[entry_id].get("kind")
            if f["kind"] == "zip":
                pass  # zips are always processed, key or not
            elif f["kind"] == "excel" and secret is not None:
                pass  # Excel only when a key was assigned
            else:
                continue
            assignments[f["name"]] = {
                "secret": secret, "org_id": f["org_id"], "org_name": f["org_name"],
                "key_kind": key_kind, "file_kind": f["kind"],
            }
        return assignments

    @bp.get("/api/workspace/files")
    def api_workspace_files():
        """List today's workspace files for the Unlock Station (see unlock_ops)."""
        return jsonify(unlock_ops.list_workspace_files())

    @bp.post("/api/workspace/unlock")
    def api_workspace_unlock():
        """Unlock the workspace files using the client's key assignments.

        Body ``{assignments: {filename: entry_id|null}}``. Requires the vault
        unlocked (**423** otherwise) since it reads secrets. Zips are unzipped with
        or without a key; Excel files are decrypted only when a key was dropped on
        them. Returns the engine result (``unlocked`` + per-file ``errors``).
        """
        if not vault_store.is_unlocked():
            return jsonify({"error": "vault locked"}), 423
        body = request.get_json(silent=True) or {}
        client = body.get("assignments") or {}
        listing = unlock_ops.list_workspace_files()
        if not listing["exists"]:
            return jsonify({"error": "no workspace folder for today",
                            "unlocked": [], "errors": []}), 404
        entry_idx = _vault_entry_index()
        assignments = _build_unlock_assignments(
            listing["files"], lambda f: client.get(f["name"]), entry_idx)
        return jsonify(unlock_ops.unlock_files(assignments))

    def _selector_for_key_kind(key_kind):
        """Map a used key's kind to the recorded selector, or None if unrecordable."""
        if key_kind == "managed":
            return "managed"
        if key_kind == "temporary":
            return "recent_temporary"
        return None

    @bp.post("/api/workspace/record-assignment")
    def api_workspace_record_assignment():
        """Record each successful unlock as a per-org key-assignment pattern.

        Body ``{records: [{org_id, file_kind, key_kind}], all_files: [org_id, ...]}``
        (from the client's last unlock). Per-kind ``key_kind`` (managed/temporary)
        becomes the stored selector; ``all_files`` lists orgs where one managed key
        unlocked files across multiple kinds, recorded as the cross-kind "same key for
        all files" habit. No secrets involved, so no unlock gate. Returns the org ids
        updated.
        """
        body = request.get_json(silent=True) or {}
        updated = []
        for r in body.get("records") or []:
            if not isinstance(r, dict):
                continue
            selector = _selector_for_key_kind(r.get("key_kind"))
            if selector is None:
                continue
            org = customer_store.record_key_assignment(
                r.get("org_id"), r.get("file_kind"), selector)
            if org is not None:
                updated.append(org["id"])
        for org_id in body.get("all_files") or []:
            org = customer_store.record_all_files_key(org_id)
            if org is not None:
                updated.append(org["id"])
        return jsonify({"recorded": updated})

    @bp.post("/api/workspace/smart-unlock")
    def api_workspace_smart_unlock():
        """Auto-assign keys from each org's recorded pattern, then unlock.

        For every workspace file whose org has a recorded pattern for that file
        kind, pick the concrete key kind (``managed`` = a managed key;
        ``recent_temporary`` = a temporary key) and run the same engine as a manual
        unlock. Requires the vault unlocked (**423**).

        When an org has **more than one file of a kind**, keys are paired
        newest->oldest instead of every file sharing one key: the files are sorted by
        their originating datetime (newest first) and matched positionally against
        that kind's keys (also newest first, per ``vault_store._ordered``). If there
        are fewer keys than files, the oldest leftover files are left unassigned
        (unkeyed zips still extract; encrypted excels without a key are skipped). A
        single-file org keeps the standard behavior (its newest key of the kind).
        """
        if not vault_store.is_unlocked():
            return jsonify({"error": "vault locked"}), 423
        listing = unlock_ops.list_workspace_files()
        if not listing["exists"]:
            return jsonify({"error": "no workspace folder for today",
                            "unlocked": [], "errors": []}), 404
        by_org = vault_store.entries_by_org()
        entry_idx = {e["id"]: e for items in by_org.values() for e in items}
        orgs = {o["id"]: o for o in customer_store.snapshot()}

        assigned = {}  # filename -> entry_id

        # Cross-kind habit takes precedence: an org flagged "same key for all files"
        # gets its newest managed key broadcast to every one of its files, regardless
        # of file kind, instead of the per-kind pairing below.
        broadcast_orgs = set()
        for f in listing["files"]:
            org = orgs.get(f["org_id"])
            if not org or not org.get("all_files_key"):
                continue
            managed = [e for e in by_org.get(f["org_id"], []) if e.get("kind") == "managed"]
            if not managed:
                continue
            assigned[f["name"]] = managed[0]["id"]   # newest managed key (by_org is ordered)
            broadcast_orgs.add(f["org_id"])

        # Group the remaining files by (org, file kind) among orgs with a recorded
        # pattern for that kind, then pair keys to files newest->oldest within each group.
        groups = {}
        for f in listing["files"]:
            if f["org_id"] in broadcast_orgs:
                continue
            org = orgs.get(f["org_id"])
            if not org:
                continue
            pattern = next((k for k in org.get("key_assignments", [])
                            if k.get("file_kind") == f["kind"]), None)
            if not pattern:
                continue
            groups.setdefault((f["org_id"], f["kind"]), (pattern["selector"], []))[1].append(f)

        for (org_id, _kind), (selector, files) in groups.items():
            want = "managed" if selector == "managed" else "temporary"
            keys = [e for e in by_org.get(org_id, []) if e.get("kind") == want]
            if not keys:
                continue
            # Newest datetime first, so file[i] pairs with the i-th newest key.
            ordered = sorted(files, key=lambda f: f.get("received", ""), reverse=True)
            if len(ordered) <= 1:
                assigned[ordered[0]["name"]] = keys[0]["id"]
                continue
            for i, f in enumerate(ordered):
                if i < len(keys):
                    assigned[f["name"]] = keys[i]["id"]
                # else: leftover oldest file left unassigned

        assignments = _build_unlock_assignments(
            listing["files"], lambda f: assigned.get(f["name"]), entry_idx)
        return jsonify(unlock_ops.unlock_files(assignments))

    # ----- Workshop → Calendar (file pins) -----

    @bp.get("/api/calendar/pins")
    def api_calendar_pins():
        """Every calendar file pin, so the calendar can tag each day (see calendar_store)."""
        return jsonify({"pins": calendar_store.snapshot()})

    @bp.post("/api/calendar/pins")
    def api_calendar_pin():
        """Pin a today's-workspace file to a calendar day.

        Body ``{date: "YYYY-MM-DD", filename, description?}``. Copies the file into
        the limbo holding folder and records the pin (with the file's org metadata
        from the source manifest). **404** when today's folder / the file is absent
        or the date is malformed. No secrets, so no unlock gate.
        """
        body = request.get_json(silent=True) or {}
        result = calendar_ops.pin_file(
            calendar_store,
            str(body.get("date") or ""),
            str(body.get("filename") or ""),
            str(body.get("description") or ""))
        if not result.get("ok"):
            return jsonify({"error": result.get("error", "could not pin file")}), 404
        return jsonify(result["pin"])

    @bp.delete("/api/calendar/pins/<pid>")
    def api_calendar_unpin(pid):
        """Remove a pin and delete its (unconsumed) limbo copy."""
        removed = calendar_ops.remove_pin(calendar_store, pid)
        if not removed:
            return jsonify({"error": "unknown pin"}), 404
        return jsonify({"removed": pid})

    @bp.post("/api/calendar/create-workspace")
    def api_calendar_create_workspace():
        """Create today's dated workspace folder so the calendar's bottom half is usable.

        The "ask for workspace to be created" action shown when today has no
        workspace folder yet. Idempotent (``exist_ok``). Returns the folder path.
        """
        folder = calendar_ops.today_folder()
        folder.mkdir(parents=True, exist_ok=True)
        return jsonify({"folder": str(folder), "exists": True})

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
        # Attach each org's non-secret vault summary (count/kinds/last scan) so the
        # card can show a read-only "has keys" line without unlocking the vault.
        orgs = customer_store.snapshot()
        index = vault_store.index()
        for org in orgs:
            org["vault"] = index.get(org["id"], {})
        return jsonify({"organizations": orgs})

    @bp.post("/api/organizations")
    def create_organization():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        org = customer_store.create(data)
        category_store.add(org.get("category", ""))   # a typed category creates itself
        return jsonify(org)

    @bp.put("/api/organizations/<oid>")
    def update_organization(oid):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        updated = customer_store.update(oid, data)
        if updated is None:
            abort(404)
        # Typing a category that does not exist is all it takes to create it: the org is
        # saved with it, and it joins the selectable list from now on.
        category_store.add(updated.get("category", ""))
        return jsonify(updated)

    # ----- Organization categories (the picker behind the Category field) -----

    @bp.get("/api/categories")
    def list_categories():
        return jsonify({"categories": category_store.snapshot(),
                        "partner": config.ORG_PARTNER_CATEGORY})

    @bp.post("/api/categories")
    def add_category():
        """Create one category. Also reachable implicitly by typing it on an org."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        created = category_store.add(data.get("name", ""))
        return jsonify({"categories": category_store.snapshot(), "created": created})

    @bp.put("/api/categories")
    def replace_categories():
        """Replace the whole list (reorder / prune)."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        return jsonify({"categories": category_store.update(data.get("categories"))})

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
        # A newly-mapped domain may resolve parked captures to this org.
        _flush_vault_captures()
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
        # Assigning the sender may resolve parked captures to this org.
        _flush_vault_captures()
        return jsonify(org)

    @bp.post("/api/contacts/unassign")
    def unassign_contact():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        return jsonify({"removed": customer_store.unassign(data.get("email"))})

    # ----- Key Vault (Workshop view) -----
    # Secrets are sealed under a master passphrase (AES-256-GCM, scrypt). The
    # routes never return secrets in bulk: list/entries are redacted; a single
    # secret is returned only by the explicit /reveal, and the passphrase is used
    # transiently to derive the key (never stored, never echoed back).

    @bp.get("/api/vault/status")
    def vault_status():
        return jsonify(vault_store.status())

    @bp.post("/api/vault/init")
    def vault_init():
        data = request.get_json(silent=True) or {}
        if not vault_store.is_available():
            abort(503, description="vault cipher unavailable on this machine")
        if not vault_store.init(data.get("passphrase") or ""):
            abort(400, description="vault already exists or passphrase too short")
        # A brand-new (unlocked) vault scans + captures any already-detected mail.
        _on_vault_unlocked()
        return jsonify(vault_store.status())

    @bp.post("/api/vault/unlock")
    def vault_unlock():
        data = request.get_json(silent=True) or {}
        ok = (vault_store.unlock_with_dpapi() if data.get("dpapi")
              else vault_store.unlock(data.get("passphrase") or ""))
        if not ok:
            abort(401, description="could not unlock the vault")
        # Logging back in immediately re-homes parked captures and auto-scans, so
        # the queued passwords are recorded without a manual scan.
        _on_vault_unlocked()
        return jsonify(vault_store.status())

    @bp.post("/api/vault/lock")
    def vault_lock():
        vault_store.lock()
        return jsonify(vault_store.status())

    @bp.post("/api/vault/remember")
    def vault_remember():
        data = request.get_json(silent=True) or {}
        if not vault_store.remember_on_machine(bool(data.get("enable"))):
            abort(400, description="cannot change remember setting (locked or no DPAPI)")
        return jsonify(vault_store.status())

    @bp.get("/api/vault/entries")
    def vault_entries():
        if not vault_store.is_unlocked():
            abort(423, description="vault is locked")
        return jsonify({"entries": vault_store.entries_by_org()})

    @bp.post("/api/vault/entries")
    def vault_add_entry():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        if not vault_store.is_unlocked():
            abort(423, description="vault is locked")
        entry = vault_store.add_entry(data.get("org_id"), data)
        if entry is None:
            abort(400, description="could not add the key (unknown org or limit reached)")
        return jsonify(entry)

    @bp.put("/api/vault/entries/<entry_id>")
    def vault_update_entry(entry_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        if not vault_store.is_unlocked():
            abort(423, description="vault is locked")
        entry = vault_store.update_entry(entry_id, data)
        if entry is None:
            abort(404)
        return jsonify(entry)

    @bp.delete("/api/vault/entries/<entry_id>")
    def vault_delete_entry(entry_id):
        if not vault_store.is_unlocked():
            abort(423, description="vault is locked")
        return jsonify({"removed": vault_store.delete_entry(entry_id)})

    @bp.post("/api/vault/entries/<entry_id>/reveal")
    def vault_reveal_entry(entry_id):
        if not vault_store.is_unlocked():
            abort(423, description="vault is locked")
        secret = vault_store.reveal(entry_id)
        if secret is None:
            abort(404)
        return jsonify({"secret": secret})

    @bp.post("/api/vault/reveal-all")
    def vault_reveal_all():
        """Every entry's secret as `{entry_id: secret}` for the hold-Z "reveal all"
        affordance. The one sanctioned **bulk** secret read — gated on unlocked
        (**423** otherwise), nothing persisted or logged."""
        if not vault_store.is_unlocked():
            abort(423, description="vault is locked")
        return jsonify({"secrets": vault_store.reveal_all()})

    @bp.post("/api/vault/search")
    def vault_search():
        """Entries matching `{query}` across secret value / org name / datetime (and
        label/username/url), grouped by org, **secrets redacted**. The value match
        runs server-side so secrets are never shipped to filter them. **423** when
        locked."""
        data = request.get_json(silent=True) or {}
        if not vault_store.is_unlocked():
            abort(423, description="vault is locked")
        return jsonify({"entries": vault_store.search(data.get("query", ""), _vault_org_names())})


    # ----- Composer (template workbench; READ-ONLY, writes nothing) -----

    @bp.get("/api/composer/blocks")
    def composer_blocks():
        """The draggable function palette.

        Every block carries its demo **cases** -- the same snippet against different
        inputs, each rendered live through the DSL (so the palette cannot advertise an
        output the DSL wouldn't produce). The frontend cycles through them slowly."""
        return jsonify({"blocks": composer.render_blocks(),
                        "demo_context": composer.DEMO_CONTEXT,
                        "cycle_ms": config.COMPOSER_BLOCK_CYCLE_MS})

    @bp.get("/api/composer/samples")
    def composer_samples():
        """The 10 example mails and the sheet row assigned to each."""
        return jsonify({"samples": composer.SAMPLES, "filters": mail_picker.FILTERS})

    @bp.get("/api/composer/mails")
    def composer_mails():
        """One page of cache mail for the picker (the app's only paged list).

        Shared by Composer's left column (pick ONE mail to preview against) and
        Press's worklist loader (load MANY). ``?filter=`` is one of
        ``mail_picker.FILTER_IDS``; ``?offset=``/``?limit=`` page through the filtered
        set newest-first. Returns slim picker cards -- raw strings the frontend inserts
        as DOM text, not presenter view models."""
        filter_id = request.args.get("filter", "all")
        if filter_id not in mail_picker.FILTER_IDS:
            filter_id = "all"
        offset = _int_arg("offset", 0)
        limit = min(_int_arg("limit", config.COMPOSER_PAGE_SIZE),
                    config.COMPOSER_PAGE_SIZE_MAX)

        resolve_org = _mail_org_resolver()
        result = mail_picker.page(store.snapshot(), filter_id, offset, limit,
                                  resolve_org, tag_store.tags_for)
        cards = []
        for mail in result["mails"]:
            org = resolve_org(mail)
            cards.append(mail_picker.card(
                mail,
                org_label=customers.org_label(org) if org else None,
                tags=tag_store.tags_for(mail.get("id", "")),
            ))
        return jsonify({"mails": cards, "total": result["total"],
                        "has_more": result["has_more"], "offset": offset,
                        "filters": mail_picker.FILTERS})

    @bp.post("/api/composer/preview")
    def composer_preview():
        """Render a template against ONE picked mail. A pure dry-run: no draft, no
        audit log, no cache mutation -- it is Press's preview minus the sheet
        and the shared mailbox.

        JSON: ``{source: "sample"|"mail", ref: <sample id | mail id>}`` plus either
        ``template_id`` (a stored template) or a literal ``{body, attachment_expr}``
        (so the preview follows the editor's unsaved text, not just what's stored)."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")

        tid = data.get("template_id")
        if tid:
            template = compose_template_store.get(tid)
            if template is None:
                abort(404, description="unknown template")
        else:
            # An unsaved draft straight from the editor: judged by the same validator
            # that will judge it on save, so a half-typed template reports its error
            # rather than blowing up mid-render.
            body = str(data.get("body", ""))[:config.COMPOSE_TEMPLATE_BODY_MAX]
            expr_text = str(data.get("attachment_expr", ""))[
                :config.COMPOSE_TEMPLATE_EXPR_MAX].strip()
            template = {"body": body, "attachment_expr": expr_text,
                        "error": compose_template_store_mod.validate(body, expr_text)}

        source = data.get("source")
        ref = str(data.get("ref", ""))
        if source == "sample":
            item = composer.sample(ref)
            if item is None:
                abort(404, description="unknown sample")
            mail, row = item["mail"], item["row"]
        elif source == "mail":
            mail = next((m for m in store.snapshot() if m.get("id") == ref), None)
            if mail is None:
                abort(404, description="unknown mail")
            row = bulk_compose.row_for_mail(mail)
        else:
            abort(400, description="source must be 'sample' or 'mail'")

        plan = composer.preview(template, mail, row, customer_store.snapshot(),
                                internal=_internal_domains())
        return jsonify({"plan": plan, "row": row, "template_error": template.get("error", "")})

    def _int_arg(name, default):
        try:
            return max(0, int(request.args.get(name, default)))
        except (TypeError, ValueError):
            return default

    # ----- Reply templates (authored in Composer, run by Press) -----

    @bp.get("/api/compose-templates")
    def list_compose_templates():
        return jsonify({"templates": compose_template_store.snapshot()})

    @bp.post("/api/compose-templates")
    def create_compose_template():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        return jsonify(compose_template_store.create(data))

    @bp.put("/api/compose-templates/<tid>")
    def update_compose_template(tid):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        updated = compose_template_store.update(tid, data)
        if updated is None:
            abort(404)
        return jsonify(updated)

    @bp.delete("/api/compose-templates/<tid>")
    def delete_compose_template(tid):
        compose_template_store.delete(tid)
        return jsonify({"templates": compose_template_store.snapshot()})

    # ----- Press (the reply-draft worklist; the app's only mailbox WRITE) -----

    def _press_context():
        """The snapshots a compute needs: mail by id, templates by id, orgs, CC, and
        the internal-domain set sender.is_internal is decided by."""
        mails_by_id = {m["id"]: m for m in store.snapshot()}
        templates_by_id = {t["id"]: t for t in compose_template_store.snapshot()}
        orgs = customer_store.snapshot()
        state = mailbox_store.snapshot()
        cc = mailbox_store.selected_address() if state["cc_enabled"] else ""
        return mails_by_id, templates_by_id, orgs, cc, _internal_domains()

    def _press_items(data):
        """The worklist from a request body, capped. ``[{mail_id, template_id, row}]``."""
        items = data.get("items")
        if not isinstance(items, list):
            abort(400, description="expected an 'items' array")
        if len(items) > config.PRESS_MAX_ITEMS:
            abort(400, description=f"at most {config.PRESS_MAX_ITEMS} mail items at a time")
        return [i for i in items if isinstance(i, dict)]

    @bp.get("/api/press/state")
    def press_state():
        """Everything Press needs on entry: the mailboxes and whether they are proved,
        whether Outlook is reachable at all, the templates, and the picker's filters.

        A `pending` mailbox (named while Outlook was down) is re-checked here, so simply
        opening Press once Outlook is running completes the deferred verification."""
        available = outlook.is_available()
        if available:
            for kind in mailbox_store.pending_kinds():
                box = mailbox_store.get(kind)
                _verify_mailbox(kind, box["address"])
        return jsonify({
            "mailbox": mailbox_store.snapshot(),
            "outlook_available": available,
            "ready": mailbox_store.is_ready(),
            "templates": compose_template_store.snapshot(),
            "filters": mail_picker.FILTERS,
        })

    def _verify_mailbox(kind, address):
        """Prove one mailbox and record the verdict. Never raises.

        personal -> it must BE the logged-in Outlook profile's address (owning a
                    mailbox is not the same as being able to open one).
        shared   -> the profile must be able to OPEN it.
        Outlook unreachable -> `pending`: the check is deferred, not failed, and
        Press keeps its draft controls locked until it completes.
        """
        try:
            if kind == "personal":
                mine = outlook.profile_address()
                if not mine:
                    return mailbox_store.set_address(
                        kind, address, "pending",
                        "Outlook did not report an address for the current profile")
                if mine.strip().lower() != address.strip().lower():
                    # Rejected: drop the address so the user is asked again.
                    return mailbox_store.set_address(
                        kind, "", "unset",
                        f"that is not this Outlook profile's mailbox (it is {mine})")
                return mailbox_store.set_address(kind, address, "verified")
            outlook.check_mailbox_access(address)
            return mailbox_store.set_address(kind, address, "verified")
        except outlook.OutlookUnavailableError as e:
            # Cannot reach Outlook -> defer. Cannot access the mailbox -> reject.
            if outlook.is_available():
                return mailbox_store.set_address(kind, "", "unset", str(e))
            return mailbox_store.set_address(kind, address, "pending", str(e))

    @bp.post("/api/press/mailbox")
    def press_mailbox():
        """Record + verify a mailbox. ``{kind, address}`` -> the resulting mailbox."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        kind = str(data.get("kind", ""))
        if kind not in config.MAILBOX_KINDS:
            abort(400, description="kind must be 'personal' or 'shared'")
        address = str(data.get("address", "")).strip()
        if not address:
            box = mailbox_store.set_address(kind, "", "unset")
        else:
            box = _verify_mailbox(kind, address)
        return jsonify({"mailbox": box, "state": mailbox_store.snapshot(),
                        "ready": mailbox_store.is_ready()})

    @bp.put("/api/press/settings")
    def press_settings():
        """Which mailbox is selected, and whether it is CC'd. Cannot set an address."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        state = mailbox_store.update(data)
        return jsonify({"state": state, "ready": mailbox_store.is_ready()})

    @bp.post("/api/press/compute")
    def press_compute():
        """Compute every worklist item -> empty / failed(+reasons) / ok(+plan).

        A pure dry-run: writes no draft, no audit log, no cache change. This is what
        paints the table's status dots and their hover previews."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        items = _press_items(data)
        mails_by_id, templates_by_id, orgs, cc, internal = _press_context()
        results = press.compute(items, mails_by_id, templates_by_id, orgs, cc,
                                internal)
        templates = [templates_by_id[i["template_id"]] for i in items
                     if i.get("template_id") in templates_by_id]
        return jsonify({
            "results": results,
            "columns": press.union_variables(templates),
            "ready": mailbox_store.is_ready(),
        })

    @bp.post("/api/press/form")
    def press_form():
        """Write the fill-in Excel form into today's workspace folder.

        Columns: [Entry ID] + the report's columns + exactly the ``row.*`` variables the
        chosen template reads. Pre-filled with the loaded mail items (and whatever the
        user has already typed), so the user only completes the blanks and uploads it
        back. With no mail loaded the Entry ID column is omitted -- the user could not
        supply one -- and the upload falls back to a best-effort match."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        template = compose_template_store.get(data.get("template_id"))
        mail_ids = [str(i) for i in (data.get("mail_ids") or [])]
        by_id = {m["id"]: m for m in store.snapshot()}
        mails = [by_id[i] for i in mail_ids if i in by_id]

        resolve_org = _mail_org_resolver()
        for mail in mails:
            org = resolve_org(mail)
            mail["_org_name"] = org["name"] if org else ""

        columns = press.form_columns(template, with_entry_id=bool(mails))
        rows_by_id = {str(k): v for k, v in (data.get("rows") or {}).items()
                      if isinstance(v, dict)}
        rows = press.form_rows(mails, template, columns, rows_by_id)

        folder = config.WORKSPACE_DIR / datetime.now().strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / press.form_filename()
        try:
            spreadsheet.write_xlsx(target, columns, rows)
        except spreadsheet.SpreadsheetError as e:
            abort(503, description=str(e))
        return jsonify({"folder": str(folder), "name": target.name,
                        "columns": columns, "rows": len(rows)})

    @bp.post("/api/press/upload")
    def press_upload():
        """Read a filled-in form back and bind each row to a loaded mail item.

        Multipart: ``file`` (.xlsx) + ``mail_ids`` (JSON array of the loaded items).
        Returns the row data per mail id, plus the rows that could not be bound (and
        why) rather than guessing at them."""
        upload = request.files.get("file")
        if upload is None:
            abort(400, description="no spreadsheet uploaded")
        try:
            _headers, rows, dropped = spreadsheet.parse_xlsx(upload.read())
        except spreadsheet.SpreadsheetError as e:
            abort(400, description=str(e))

        mail_ids = _json_id_list(request.form.get("mail_ids"))
        by_id = {m["id"]: m for m in store.snapshot()}
        mails = [by_id[str(i)] for i in mail_ids if str(i) in by_id]

        bound, unbound = press.bind_upload(rows, mails)
        return jsonify({"bound": bound, "unbound": unbound,
                        "dropped": dropped, "rows": len(rows)})

    @bp.post("/api/press/create-drafts")
    def press_create_drafts():
        """Commit: recompute every item server-side and draft the selected ones.

        The client sends what it *wants* drafted; the server decides what *is*. Plans
        are recomputed here from the cache + the stored template, so the client can
        never inject draft content. Refused outright unless the selected mailbox has
        been proved against Outlook. Draft-only -- it never sends. Writes a CSV audit
        log of what was created into the dated workspace folder."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        if not mailbox_store.is_ready():
            abort(409, description="the selected mailbox has not been verified against "
                                   "Outlook yet")

        items = _press_items(data)
        mails_by_id, templates_by_id, orgs, cc, internal = _press_context()
        results = press.compute(items, mails_by_id, templates_by_id, orgs, cc,
                                internal)

        # Only items the SERVER computed as ok, and only those the user ticked.
        wanted = {str(i) for i in (data.get("selected") or [])}
        to_create = [r["plan"] for r in results
                     if r["status"] == press.STATUS_OK and r["mail_id"] in wanted]

        sender = mailbox_store.selected_address()
        try:
            created = draft_ops.create_drafts(to_create, sender, cc)
        except outlook.OutlookUnavailableError as e:
            abort(503, description=str(e))

        audit = _write_press_audit(to_create, created, sender)
        ok = sum(1 for r in created if r["status"] == "created")
        return jsonify({"results": created, "created": ok,
                        "requested": len(to_create), "audit": audit})

    # ----- Smart Password Detection -----

    @bp.get("/api/password-settings")
    def get_password_settings():
        return jsonify(password_settings.snapshot())

    @bp.post("/api/password-settings")
    def save_password_settings():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        return jsonify(password_settings.update(data))

    @bp.post("/api/passwords/scan")
    def scan_passwords():
        """Run the password detector over every cached mail and record the hits.

        Reads-only against the mailbox: compiles the saved patterns, scans each
        mail's subject+body (the full cached body, not the card excerpt), and
        stashes the per-mail matches on the store for the badge + the ``passwords``
        sidebar filter. Also **auto-captures** detected passwords into the Key Vault
        (sender's org, or the Unassigned bucket) when it is unlocked; when locked the
        hits stay on the store and the next unlock auto-scans + records them. Returns
        the scanned/flagged counts, any patterns that failed to compile, and the
        vault capture/queue status. The scan also runs automatically on vault
        unlock, so search need not re-trigger it."""
        return jsonify(_run_password_scan())

    # ----- Experimental Features (which feature controls are mounted) -----

    @bp.get("/api/experimental")
    def get_experimental():
        return jsonify(experimental_store.snapshot())

    @bp.post("/api/experimental")
    def save_experimental():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        return jsonify(experimental_store.update(data))

    # ----- Suspected Customers List (Resolve Customer Name To Downloads) -----

    @bp.get("/api/customer-match")
    def get_customer_match():
        return jsonify(customer_match_store.snapshot())

    @bp.post("/api/customer-match")
    def save_customer_match():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")
        return jsonify(customer_match_store.update(data))

    return bp


def _json_id_list(raw):
    """Parse a JSON array of mail ids (Outlook EntryIDs -- strings, never ints)."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(value, list):
        return []
    return [str(i) for i in value if isinstance(i, str)]


def _write_press_audit(plans, results, sender):
    """Write a CSV record of a Press commit into the dated workspace folder.

    Columns: mail id, status, subject, from, to, cc, attachment/ftp, detail. Returns
    the file path, or "" if there was nothing to record."""
    if not plans:
        return ""
    by_id = {p["mail_id"]: p for p in plans}
    now = datetime.now()
    folder = config.WORKSPACE_DIR / now.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    target = workspace_ops.unique_path(
        folder, f"press_drafts_{now.strftime('%Y-%m-%d_%H%M%S')}.csv", 0)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["mail id", "status", "subject", "from", "to", "cc",
                     "attachment/ftp", "detail"])
    for r in results:
        p = by_id.get(r.get("mail_id"), {})
        att = p.get("ftp_link") if p.get("uses_ftp") else (
            (p.get("attachment") or {}).get("name", ""))
        writer.writerow([
            r.get("mail_id", ""), r["status"], p.get("subject", ""), sender,
            "; ".join(p.get("to", [])), "; ".join(p.get("cc", [])),
            att or "", r.get("detail", ""),
        ])
    target.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")
    log.info("Press: wrote audit log %s", target)
    return str(target)
