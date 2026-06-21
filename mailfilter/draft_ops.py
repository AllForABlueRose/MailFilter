"""Create reply DRAFTS in the shared mailbox -- the app's only mailbox write.

This is the sole module that mutates a mailbox, and it is deliberately the
narrowest mutation possible: it creates *drafts* and **never sends**. The live
path uses Outlook's ``ReplyAll`` on the matched original (so threading and the
original recipients are correct), sets ``SentOnBehalfOfName`` to the shared
mailbox, ensures the shared mailbox is CC'd, injects the rendered template above
the quoted history, attaches the file from the file server (unless the row is an
FTP row), and calls ``Save()``. A human reviews and sends from Outlook, exactly
as today -- the app preserves that authorization gate.

``create_drafts`` dispatches on ``config.BULK_MOCK_MODE``: the mock branch writes
each draft as a JSON file under ``config.MOCK_DRAFTS_DIR`` (so a run is
inspectable without Outlook); the live branch talks COM. Only the live branch
imports pywin32 (through ``outlook``, lazily).

Adding a real *send* here would be an "Always ask" change (see CLAUDE.md) -- do
not. Dependency direction: draft_ops -> outlook (COM), config.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

import config

from . import outlook, util

log = logging.getLogger(__name__)

OL_CC = 2  # OlMailRecipientType.olCC


def create_drafts(plans):
    """Create a draft for each *ready* plan in ``plans``; never sends.

    Non-ready plans are skipped defensively (the caller already filters). Returns
    a list of result dicts: ``{row_index, status: "created"|"skipped"|"error",
    detail}``.
    """
    ready = [p for p in plans if p.get("status") == "ready"]
    skipped = [{"row_index": p.get("row_index"), "status": "skipped",
                "detail": "not ready"} for p in plans if p.get("status") != "ready"]
    if not ready:
        return skipped
    created = _mock_create(ready) if config.BULK_MOCK_MODE else _com_create(ready)
    return created + skipped


# ----------------------------------------------------------------------------
# Mock backend
# ----------------------------------------------------------------------------

def _mock_create(plans):
    folder = Path(config.MOCK_DRAFTS_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results = []
    for plan in plans:
        draft = _draft_document(plan)
        name = util.safe_filename(f"draft_{stamp}_row{plan['row_index']}.json",
                                  f"draft_row{plan['row_index']}.json")
        target = folder / name
        target.write_text(json.dumps(draft, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        results.append({"row_index": plan["row_index"], "status": "created",
                        "detail": str(target)})
    log.info("Mock: wrote %d draft(s) to %s", len(results), folder)
    return results


def _draft_document(plan):
    """The inspectable representation of the draft a live run would create."""
    attachment = plan.get("attachment")
    return {
        "from": config.SHARED_MAILBOX_ADDRESS,
        "to": plan.get("to", []),
        "cc": plan.get("cc", []),
        "subject": plan.get("subject", ""),
        "body": plan.get("body", ""),
        "in_reply_to": plan.get("mail_id", ""),
        "uses_ftp": plan.get("uses_ftp", False),
        "ftp_link": plan.get("ftp_link", ""),
        "attachment": None if plan.get("uses_ftp") or not attachment else {
            "name": attachment.get("name", ""),
            "path": attachment.get("path", ""),
        },
        "note": "MOCK draft (not created in Outlook); never sent.",
    }


# ----------------------------------------------------------------------------
# Live COM backend (verified on the Outlook host before BULK_MOCK_MODE is off)
# ----------------------------------------------------------------------------

def _com_create(plans):
    pythoncom, pywintypes, win32com = outlook._import_pywin32()
    pythoncom.CoInitialize()
    try:
        app = outlook._dispatch(win32com, pywintypes)
        namespace = app.GetNamespace("MAPI")
        results = []
        for plan in plans:
            try:
                detail = _com_create_one(namespace, plan)
                results.append({"row_index": plan["row_index"],
                                "status": "created", "detail": detail})
            except Exception as e:
                log.exception("Draft creation failed for row %s", plan.get("row_index"))
                results.append({"row_index": plan["row_index"],
                                "status": "error", "detail": str(e)})
        return results
    finally:
        pythoncom.CoUninitialize()


def _com_create_one(namespace, plan):
    store_id = plan.get("store_id") or None
    if store_id:
        item = namespace.GetItemFromID(plan["mail_id"], store_id)
    else:
        item = namespace.GetItemFromID(plan["mail_id"])

    reply = item.ReplyAll()  # populates To/CC + threading from the original
    try:
        reply.SentOnBehalfOfName = config.SHARED_MAILBOX_ADDRESS
    except Exception:
        log.warning("Could not set SentOnBehalfOfName; draft will be from the "
                    "default account")

    # Ensure the shared mailbox is CC'd (ReplyAll may or may not include it).
    cc = namespace.CreateRecipient(config.SHARED_MAILBOX_ADDRESS)
    cc.Type = OL_CC
    cc.Resolve()
    reply.Recipients.Add(config.SHARED_MAILBOX_ADDRESS).Type = OL_CC
    reply.Recipients.ResolveAll()

    # Inject the rendered template above the quoted history (plain body for now).
    reply.Body = plan.get("body", "") + "\n\n" + str(getattr(reply, "Body", ""))

    attachment = plan.get("attachment")
    if not plan.get("uses_ftp") and attachment and attachment.get("exists") \
            and attachment.get("path"):
        reply.Attachments.Add(attachment["path"])

    reply.Save()  # to Drafts. NEVER Send().
    return "draft saved"
