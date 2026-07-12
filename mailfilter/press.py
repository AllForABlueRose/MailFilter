"""The read-side of Press: turn a worklist of mail items into computed reply plans.

Press is a **worklist**, not a pipeline. The user loads mail items out of the cache
(``mail_picker``), assigns a template to them -- one for all, or a different one
dragged onto individual items -- and fills in the data each template needs. Every
item then computes to one of three states, which is the whole point of the view:

* ``empty``  -- no template assigned yet (grey)
* ``failed`` -- a template is assigned but it cannot produce a draft (red), with the
  reasons: a blank cell for a variable it reads, a missing file on the file server,
  a template that does not parse
* ``ok``     -- it renders (green); hovering shows exactly what would be drafted

Only ``ok`` items can become drafts, and the server recomputes them at commit time
rather than trusting anything the client sends.

The data a template needs is ``row.*`` -- values that exist nowhere on the mail (a
reference number, a due date). Press gets them two ways, and both flow through the
same columns: ``form_columns`` builds an Excel form of exactly the variables the
chosen template reads, and the same list drives the editable cells in the table.
``template_lang.variables`` is the single source of that list, so the form, the
table, and the "missing row.ref" failure can never disagree.

Pure: stdlib + config + bulk_compose + template_lang. No Flask, no COM, no store --
snapshots are passed in, the same no-cycle shape as ``composer.py``.
"""

import config

from . import bulk_compose

# The Excel form's fixed left-hand columns. The first five mirror
# workspace_ops.write_report (§3.7) so the form reads like the report the user
# already knows; ENTRY_ID_COLUMN binds a filled row straight back to its mail.
ENTRY_ID_COLUMN = "entry id"
REPORT_COLUMNS = ["datetime", "subject", "recipient", "sender", "customer organization"]

STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
STATUS_OK = "ok"


# ----------------------------------------------------------------------------
# What a template needs, and what the mail can already answer
# ----------------------------------------------------------------------------

# The one list of row.* names a template reads (bulk_compose owns it -- the same list
# decides which cells are missing at compute time).
template_variables = bulk_compose.template_variables


def union_variables(templates):
    """Every ``row.*`` name any of ``templates`` reads, in first-seen order.

    Press's table shows one editable column per name here: with different templates
    dragged onto different items, the column set is the union, and a cell whose own
    row's template does not read it is shown greyed and ignored.
    """
    names = []
    for template in templates:
        for name in template_variables(template):
            if name not in names:
                names.append(name)
    return names


def row_defaults(mail, variables):
    """The starting row for a mail item: what the mail itself can answer, blank rest.

    ``bulk_compose.row_for_mail`` fills the columns derivable from the mail (subject,
    datetime, sender, and ``file_name`` from its first attachment). Anything else the
    template reads starts blank -- and a blank the template needs is exactly what makes
    the item compute red until the user types it or uploads it.
    """
    row = bulk_compose.row_for_mail(mail)
    for name in variables:
        row.setdefault(name, "")
    return row


# ----------------------------------------------------------------------------
# Compute
# ----------------------------------------------------------------------------

def compute_item(mail, template, row, orgs, cc_address="", internal=None):
    """Compute one worklist item -> ``{mail_id, status, plan, reasons, variables}``.

    No template -> ``empty``. A template that fails to parse, or whose row has a hole
    in it, or whose file is missing -> ``failed`` with the reasons. Otherwise ``ok``
    and ``plan`` is exactly what ``draft_ops`` would create.

    ``variables`` is the ``row.*`` names THIS item's own template reads. The frontend
    greys the other cells with it rather than re-deriving the list itself -- only the
    DSL's own parser knows which reads are real (a ``row.x`` inside a string literal
    is not one).
    """
    mail_id = mail.get("id", "")
    variables = template_variables(template)
    if not template:
        return {"mail_id": mail_id, "status": STATUS_EMPTY, "plan": None,
                "reasons": [], "variables": variables}

    error = template.get("error") or ""
    if error:
        plan = bulk_compose.invalid_template_plan(0, row, error)
        return {"mail_id": mail_id, "status": STATUS_FAILED, "plan": plan,
                "reasons": list(plan["warnings"]), "variables": variables}

    plan = bulk_compose.plan_for_mail(0, row, mail, template, orgs, cc_address,
                                      internal=internal)
    status = STATUS_OK if plan["status"] == "ready" else STATUS_FAILED
    return {"mail_id": mail_id, "status": status, "plan": plan,
            "reasons": [] if status == STATUS_OK else list(plan["warnings"]),
            "variables": variables}


def compute(items, mails_by_id, templates_by_id, orgs, cc_address="", internal=None):
    """Compute every worklist item. ``items`` is ``[{mail_id, template_id, row}]``.

    An item naming a mail that has left the cache is dropped (the cache is the truth);
    an item naming a template that no longer exists computes as ``empty``.
    """
    out = []
    for item in items:
        mail = mails_by_id.get(item.get("mail_id"))
        if mail is None:
            continue
        template = templates_by_id.get(item.get("template_id"))
        row = dict(item.get("row") or {})
        out.append(compute_item(mail, template, row, orgs, cc_address, internal))
    return out


# ----------------------------------------------------------------------------
# The Excel form: download pre-filled, fill in, upload back
# ----------------------------------------------------------------------------

def form_columns(template, with_entry_id=True):
    """The form's header row: [entry id] + report columns + the template's variables.

    ``with_entry_id`` is False when the form is downloaded with no mail loaded -- the
    user could not fill in an Outlook EntryID even if they tried, so the column is
    omitted and the upload falls back to a best-effort match instead.
    """
    columns = [ENTRY_ID_COLUMN] if with_entry_id else []
    columns += list(REPORT_COLUMNS)
    for name in template_variables(template):
        if name not in columns:
            columns.append(name)
    return columns


def form_rows(mails, template, columns, rows_by_id=None):
    """One pre-filled row per loaded mail, in ``columns`` order.

    Everything the mail already answers is filled in; the template's own variables are
    filled from the current table values (``rows_by_id``) when there are any, so a
    downloaded form round-trips what the user has already typed rather than blanking it.
    """
    rows_by_id = rows_by_id or {}
    variables = template_variables(template)
    out = []
    for mail in mails:
        mail_id = mail.get("id", "")
        current = rows_by_id.get(mail_id) or {}
        base = row_defaults(mail, variables)
        base.update({k: v for k, v in current.items() if str(v).strip()})
        values = {
            ENTRY_ID_COLUMN: mail_id,
            "datetime": str(mail.get("received", "")),
            "subject": str(mail.get("subject", "")),
            "recipient": "; ".join(str(e) for e in (mail.get("recipient_emails") or [])),
            "sender": str(mail.get("sender_email", "")) or str(mail.get("sender", "")),
            "customer organization": str(mail.get("_org_name", "")),
        }
        values.update({name: str(base.get(name, "")) for name in variables})
        out.append([values.get(column, "") for column in columns])
    return out


def bind_upload(rows, mails):
    """Bind each uploaded sheet row to one mail item.

    An ``entry id`` column binds exactly. Without one -- a form downloaded before any
    mail was loaded -- fall back to ``bulk_compose.match_row_to_mails`` (normalized
    subject + datetime +/- tolerance + lenient sender) against the **loaded items**.

    Returns ``(bound, unbound)`` where ``bound`` is ``{mail_id: row}`` and ``unbound``
    is ``[{row_index, reason}]`` -- a row matching nothing, or ambiguously matching
    several, is reported rather than guessed.
    """
    by_id = {m.get("id", ""): m for m in mails}
    bound, unbound = {}, []
    for i, row in enumerate(rows):
        entry_id = str(row.get(ENTRY_ID_COLUMN, "")).strip()
        if entry_id:
            if entry_id in by_id:
                bound[entry_id] = dict(row)
            else:
                unbound.append({"row_index": i,
                                "reason": "this row's mail is not loaded in the table"})
            continue

        matched = bulk_compose.match_row_to_mails(row, mails)
        if len(matched) == 1:
            bound[matched[0].get("id", "")] = dict(row)
        elif not matched:
            unbound.append({"row_index": i,
                            "reason": "no loaded mail matches this row's "
                                      "subject/datetime/sender"})
        else:
            unbound.append({"row_index": i,
                            "reason": f"{len(matched)} loaded mails match this row; "
                                      "add an Entry ID column to bind it exactly"})
    return bound, unbound


def form_filename():
    """The form's name in the dated workspace folder. A same-day re-download
    overwrites in place, matching ``workspace_ops.write_report``."""
    return config.PRESS_FORM_NAME
