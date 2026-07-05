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

import config

from . import (
    automation,
    bulk_compose,
    customers,
    dedup,
    draft_ops,
    outlook,
    password_detect,
    shared_mailbox,
    spreadsheet,
    unlock_ops,
    util,
    workspace_manifest,
    workspace_ops,
)
from .filters import MailQuery, filter_mails
from .presenter import extra_link_views, to_view_model

log = logging.getLogger(__name__)


# ----- Smart Password Detection scan + Key Vault capture (module-level so the
# scheduler / refresh path can reuse them, not just the route closures) -----

def resolve_org_id(customer_store, email):
    """The org id a sender resolves to (representative-of preferred), or None."""
    res = customers.resolve(email, customer_store.snapshot())
    return res.get("rep_org_id") or res.get("member_org_id")


def capture_scanned_passwords(store, vault_store, customer_store):
    """Record the latest scan's detected passwords into the unlocked vault (sender's
    org, or the Unassigned bucket). Idempotent; returns how many **genuinely new**
    keys were added (a re-detected password that dedups onto an existing key does not
    count).

    A captured key is stamped with the source mail's received datetime (not the scan
    time), so its ``scan_dt`` reflects when the password actually arrived."""
    captured = 0
    for mail in store.snapshot():
        secrets = mail.get("_passwords") or []
        if not secrets:
            continue
        email = mail.get("sender_email") or ""
        org_id = resolve_org_id(customer_store, email) or config.VAULT_UNASSIGNED_ORG_ID
        received = mail.get("received")
        for secret in secrets:
            _entry, created = vault_store.capture_scan(
                org_id, secret, scan_dt=received, source_email=email)
            if created:
                captured += 1
    return captured


def run_password_scan(store, password_settings, vault_store, customer_store,
                      experimental_store):
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
    captured = capture_scanned_passwords(store, vault_store, customer_store) if unlocked else 0
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
                      experimental_store):
    """Refresh callback for the scheduler and POST /refresh: fetch + sync mail from
    Outlook, then run an SDS scan so badges/captures reflect newly-arrived mail. The
    scan is read-only against the mailbox and only writes the vault when unlocked."""
    outlook.refresh(store)
    run_password_scan(store, password_settings, vault_store, customer_store,
                      experimental_store)


def create_blueprint(store, settings, tag_store, template_store, automation_store,
                     customer_store, compose_template_store, password_settings,
                     experimental_store, customer_match_store, vault_store):
    bp = Blueprint("mailfilter", __name__)

    def view_model(mail, query, resolve_labels=None):
        view = to_view_model(mail, query.main, query.optional,
                             query.attachment_blacklist, query.links_blacklist)
        view["tags"] = tag_store.tags_for(mail["id"])
        # The sender's resolved customer-organization label(s), when a resolver is
        # supplied (built once per request). Derived from the customer store, which
        # the presenter must not import, so it's attached here like `tags`.
        view["org_labels"] = resolve_labels(mail.get("sender_email", "")) if resolve_labels else []
        return view

    def _resolve_org_id(email):
        return resolve_org_id(customer_store, email)

    def _capture_scanned_to_vault():
        return capture_scanned_passwords(store, vault_store, customer_store)

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
                                 experimental_store)

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
                  experimental_store),
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
        resolve_labels = customers.label_resolver(customer_store.snapshot())
        out = []
        for m in mails:
            if m["id"] in hidden:
                continue
            view = view_model(m, query, resolve_labels)
            urls = twin_links.get(m["id"])
            if urls:
                view["links"] = view["links"] + extra_link_views(
                    urls, query.main, query.optional, [l["url"] for l in view["links"]])
            out.append(view)
        return jsonify({"mails": out, "query_error": "", **status})

    @bp.get("/api/thread")
    def api_thread():
        # Every mail in the conversation, earliest-first. Highlight with the
        # active search (main/optional) so matches stand out here too; a
        # malformed expression simply highlights nothing.
        query = MailQuery.from_args(request.args)
        mails = store.thread_for(request.args.get("id", ""))
        resolve_labels = customers.label_resolver(customer_store.snapshot())
        return jsonify({"mails": [view_model(m, query, resolve_labels) for m in mails]})

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
        ``<WORKSPACE_DIR>/<YYYY-MM-DD>/`` (created if absent), one at a time.
        With ``append_customer_name`` on (the experimental toggle), a file whose
        sender resolves to an organization gets ``_<org name>`` appended to its
        stem. Returns the folder and the saved filenames; per-item failures are
        collected in ``errors`` rather than aborting the whole batch.
        """
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")

        append = data.get("append_customer_name") in (True, "1", "true", "on")
        resolve = data.get("resolve_customer_name") in (True, "1", "true", "on")
        # Orgs are needed for the sender resolver (append) and the keyword->org
        # lookup (resolve/Brute Force), so fetch them when either toggle is on.
        orgs = customer_store.snapshot() if (append or resolve) else None
        customer_mappings = customer_match_store.mappings() if resolve else None
        folder, saved, errors = workspace_ops.save_attachments(
            store, data.get("items") or [], append_org_name=append, orgs=orgs,
            resolve_customer=resolve, customer_mappings=customer_mappings)
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
        The last column is resolved from the "Brute Force Resolve Customer Name"
        keyword->org mappings. The filename embeds the creation date. Unknown ids
        are skipped. Returns the folder, the file name, and the row count.
        """
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="expected a JSON object")

        folder, name, count = workspace_ops.write_report(
            store, data.get("ids") or [],
            mappings=customer_match_store.mappings(), orgs=customer_store.snapshot())
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

        Body ``{records: [{org_id, file_kind, key_kind}]}`` (from the client's last
        unlock). ``key_kind`` (managed/temporary) becomes the stored selector. No
        secrets involved, so no unlock gate. Returns the org ids updated.
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
        return jsonify({"recorded": updated})

    @bp.post("/api/workspace/smart-unlock")
    def api_workspace_smart_unlock():
        """Auto-assign keys from each org's recorded pattern, then unlock.

        For every workspace file whose org has a recorded pattern for that file
        kind, pick the concrete key (``managed`` = the org's first managed key;
        ``recent_temporary`` = its newest captured temporary key) and run the same
        engine as a manual unlock. Requires the vault unlocked (**423**).
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

        def resolve_entry_id(f):
            org = orgs.get(f["org_id"])
            if not org:
                return None
            pattern = next((k for k in org.get("key_assignments", [])
                            if k.get("file_kind") == f["kind"]), None)
            if not pattern:
                return None
            want = "managed" if pattern["selector"] == "managed" else "temporary"
            # entries_by_org orders managed-first then temporary newest-first, so the
            # first match of the wanted kind is exactly the recorded selector.
            match = next((e for e in by_org.get(f["org_id"], []) if e.get("kind") == want), None)
            return match["id"] if match else None

        assignments = _build_unlock_assignments(listing["files"], resolve_entry_id, entry_idx)
        return jsonify(unlock_ops.unlock_files(assignments))

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


    # ----- Bulk Compose (reply-draft generation) -----

    @bp.get("/api/compose-templates")
    def list_compose_templates():
        return jsonify({"templates": compose_template_store.snapshot(),
                        "shared_mailbox": config.SHARED_MAILBOX_ADDRESS,
                        "mock_mode": config.BULK_MOCK_MODE})

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

    def _plan_from_request():
        """Shared by preview and commit: parse the uploaded sheet, load the chosen
        template, read the shared inbox, and plan every row. Aborts (4xx/5xx) on a
        bad upload / unknown template / unreachable Outlook. Returns
        ``(rows, plan_result, dropped)``."""
        upload = request.files.get("file")
        if upload is None:
            abort(400, description="no spreadsheet uploaded")
        template = compose_template_store.get(request.form.get("template_id"))
        if template is None:
            abort(400, description="unknown or missing template")
        try:
            _headers, rows, dropped = spreadsheet.parse_xlsx(upload.read())
        except spreadsheet.SpreadsheetError as e:
            abort(400, description=str(e))
        try:
            shared = shared_mailbox.read_inbox()
        except outlook.OutlookUnavailableError as e:
            abort(503, description=str(e))
        orgs = customer_store.snapshot()
        result = bulk_compose.plan_all(rows, shared, template, orgs)
        return rows, result, dropped

    @bp.post("/api/bulk/preview")
    def bulk_preview():
        """Dry-run: generate planned drafts from the sheet and return them.

        Multipart form: ``file`` (the .xlsx) + ``template_id``. Writes NOTHING --
        no drafts, no audit log. The response drives the review table."""
        _rows, result, dropped = _plan_from_request()
        return jsonify({**result, "dropped": dropped})

    @bp.post("/api/bulk/create-drafts")
    def bulk_create_drafts():
        """Commit: recompute plans server-side and create the selected drafts.

        Multipart form: ``file`` + ``template_id`` + optional ``indices`` (a JSON
        array of row indices to include; absent = every ready row). Plans are
        recomputed here rather than trusted from the client, so the server alone
        decides what is created. Draft-only -- never sends. Writes a CSV audit log
        of what was created to the dated workspace folder."""
        _rows, result, _dropped = _plan_from_request()

        selected = _selected_indices(request.form.get("indices"))
        to_create = [p for p in result["plans"]
                     if p["status"] == "ready"
                     and (selected is None or p["row_index"] in selected)]

        results = draft_ops.create_drafts(to_create)
        audit = _write_bulk_audit(to_create, results)
        created = sum(1 for r in results if r["status"] == "created")
        return jsonify({"results": results, "created": created,
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


def _selected_indices(raw):
    """Parse the optional ``indices`` form field into a set, or None for 'all'."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, list):
        return None
    return {int(i) for i in value if isinstance(i, (int, float))}


def _write_bulk_audit(plans, results):
    """Write a CSV record of a commit into the dated workspace folder.

    Columns: row, status, subject, to, cc, attachment/ftp, detail. Returns the
    file path, or "" if there was nothing to record."""
    if not plans:
        return ""
    by_index = {p["row_index"]: p for p in plans}
    now = datetime.now()
    folder = config.WORKSPACE_DIR / now.strftime("%Y-%m-%d")
    folder.mkdir(parents=True, exist_ok=True)
    target = workspace_ops.unique_path(
        folder, f"bulk_drafts_{now.strftime('%Y-%m-%d_%H%M%S')}.csv", 0)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["row", "status", "subject", "to", "cc", "attachment/ftp", "detail"])
    for r in results:
        p = by_index.get(r["row_index"], {})
        att = p.get("ftp_link") if p.get("uses_ftp") else (
            (p.get("attachment") or {}).get("name", ""))
        writer.writerow([
            r["row_index"], r["status"], p.get("subject", ""),
            "; ".join(p.get("to", [])), "; ".join(p.get("cc", [])),
            att or "", r.get("detail", ""),
        ])
    target.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")
    log.info("Bulk Compose: wrote audit log %s", target)
    return str(target)
