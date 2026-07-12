"""Create reply DRAFTS in Outlook -- the app's only mailbox write.

This is the sole module that mutates a mailbox, and it is deliberately the narrowest
mutation possible: it creates *drafts* and **never sends**. For each computed plan it
runs Outlook's ``ReplyAll`` on the original mail (so threading and the original
recipients are correct), sets ``SentOnBehalfOfName`` to the mailbox Press is drafting
from, optionally CC's that mailbox, injects the rendered template above the quoted
history, attaches the file from the file server (unless the item is an FTP item), and
calls ``Save()``. A human reviews and sends from Outlook -- the app preserves that
authorization gate.

The mail replied to is the ordinary cached mail: its Outlook ``EntryID`` is the cache's
``id``, and ``GetItemFromID(entry_id)`` re-opens it, the same call ``fetch_attachment``
makes. ``store_id`` is honoured when a plan carries one but is not required.

There is **no mock**. Drafting requires classic Outlook over COM, and the mailbox
drafted from must have been proved first (``mailbox_store`` + ``outlook.profile_address``
/ ``check_mailbox_access``). Tests drive this module with stubbed COM objects.

Adding a real *send* here would be an "Always ask" change (see CLAUDE.md) -- do not.
Dependency direction: draft_ops -> outlook (COM), config.
"""

import logging

from . import outlook

log = logging.getLogger(__name__)

OL_CC = 2  # OlMailRecipientType.olCC


def create_drafts(plans, sender_address, cc_address=""):
    """Create a draft for each *ready* plan; never sends.

    ``sender_address`` is the (already verified) mailbox the drafts are sent on behalf
    of. ``cc_address`` is added to the reply's CC when non-empty -- Press passes the
    same mailbox, or "" when the user has turned the CC toggle off.

    Non-ready plans are skipped defensively (the caller already filters, and the
    route recomputes them server-side). Returns a list of
    ``{mail_id, row_index, status: "created"|"skipped"|"error", detail}``.
    """
    ready = [p for p in plans if p.get("status") == "ready"]
    skipped = [{"mail_id": p.get("mail_id", ""), "row_index": p.get("row_index"),
                "status": "skipped", "detail": "not ready"}
               for p in plans if p.get("status") != "ready"]
    if not ready:
        return skipped
    if not (sender_address or "").strip():
        # Belt and braces: the route refuses this already. A draft must never be
        # created from an unproved mailbox.
        return [{"mail_id": p.get("mail_id", ""), "row_index": p.get("row_index"),
                 "status": "error", "detail": "no verified mailbox selected"}
                for p in ready] + skipped
    return _com_create(ready, sender_address, cc_address) + skipped


def _com_create(plans, sender_address, cc_address):
    pythoncom, pywintypes, win32com = outlook._import_pywin32()
    pythoncom.CoInitialize()
    try:
        app = outlook._dispatch(win32com, pywintypes)
        namespace = app.GetNamespace("MAPI")
        results = []
        for plan in plans:
            try:
                detail = _com_create_one(namespace, plan, sender_address, cc_address)
                results.append({"mail_id": plan.get("mail_id", ""),
                                "row_index": plan.get("row_index"),
                                "status": "created", "detail": detail})
            except Exception as e:
                log.exception("Draft creation failed for mail %s", plan.get("mail_id"))
                results.append({"mail_id": plan.get("mail_id", ""),
                                "row_index": plan.get("row_index"),
                                "status": "error", "detail": str(e)})
        return results
    finally:
        pythoncom.CoUninitialize()


def _com_create_one(namespace, plan, sender_address, cc_address):
    store_id = plan.get("store_id") or None
    if store_id:
        item = namespace.GetItemFromID(plan["mail_id"], store_id)
    else:
        item = namespace.GetItemFromID(plan["mail_id"])

    reply = item.ReplyAll()  # populates To/CC + threading from the original
    try:
        reply.SentOnBehalfOfName = sender_address
    except Exception:
        log.warning("Could not set SentOnBehalfOfName; draft will be from the "
                    "default account")

    # CC the drafting mailbox, unless the user turned the CC toggle off.
    cc_address = (cc_address or "").strip()
    if cc_address:
        reply.Recipients.Add(cc_address).Type = OL_CC
        reply.Recipients.ResolveAll()

    # Inject the rendered template above the quoted history (plain body).
    reply.Body = plan.get("body", "") + "\n\n" + str(getattr(reply, "Body", ""))

    attachment = plan.get("attachment")
    if not plan.get("uses_ftp") and attachment and attachment.get("exists") \
            and attachment.get("path"):
        reply.Attachments.Add(attachment["path"])

    reply.Save()  # to Drafts. NEVER Send().
    return "draft saved"
