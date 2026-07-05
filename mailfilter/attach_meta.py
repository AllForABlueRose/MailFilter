"""Embed a mail's resolved customer organization into a downloaded file's own bytes.

Today org identity reaches a saved attachment only through the file *name*
(``report_Acme Corp.pdf``) — lossy, collision-prone, and carrying no id. This
module writes the org into the file's **native metadata** so it travels with the
file regardless of later renames. A single namespaced key, :data:`META_KEY`,
holds a small JSON blob ``{"org_id", "org_name", "mail_id"}``.

:func:`embed_org_metadata` dispatches on the file extension to a per-format writer
and is **best-effort**: an unknown extension, a malformed/encrypted/signed file, or
any writer that raises returns the **original bytes unchanged** (never a hard fail —
the download still succeeds, just without embedded metadata).

:func:`read_org_metadata` is the inverse — it pulls the JSON blob back out of a
file's bytes (``None`` when the marker is absent/unreadable). Because
``workspace_ops`` embeds the marker on **every** download (org fields blank when no
org resolved), its presence doubles as a "downloaded by this app" stamp:
:func:`has_org_metadata` is what "Cleanup Local Workspace" uses to tell app files
apart from incidental ones before deleting.

Format coverage (per ``back_office/reference.txt``):

* PDF  — :mod:`pikepdf` (lazy import; app runs without it, PDF embedding then
  simply passes through). Writes a ``/MailAnalyzerOrg`` DocInfo key.
* OOXML (docx/xlsx/pptx) — stdlib :mod:`zipfile`: a ``docProps/custom.xml`` custom
  property, wired into ``[Content_Types].xml`` and ``_rels/.rels`` so the package
  stays valid and Office still opens it (macros untouched).
* ZIP — stdlib :mod:`zipfile`: the archive's end-of-central-directory comment.
* PNG — stdlib: an ``iTXt`` chunk (UTF-8) inserted before ``IEND``, reusing the
  chunk machinery in :mod:`mailfilter.imgcodec`.

This module is pure of Flask/COM and imports no store; ``workspace_ops`` calls it.
"""

import io
import json
import logging
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from .imgcodec import _PNG_SIGNATURE, _chunk

log = logging.getLogger(__name__)

# One namespaced key across every format.
META_KEY = "MailAnalyzerOrg"


def embed_org_metadata(filename, blob, meta):
    """Return ``blob`` with ``meta`` embedded, or ``blob`` unchanged (best-effort).

    ``meta`` is a small dict (``{"org_id", "org_name", "mail_id"}``); it is
    serialized to JSON and written under :data:`META_KEY`. Dispatch is by the
    lowercased extension of ``filename``; an unhandled extension or any failure
    returns the input bytes verbatim.
    """
    ext = Path(filename or "").suffix.lower()
    writer = _WRITERS.get(ext)
    if writer is None:
        return blob
    payload = json.dumps(meta, ensure_ascii=False, sort_keys=True)
    try:
        return writer(blob, payload)
    except Exception:
        log.debug("attach_meta: embed failed for %s; passing through", ext, exc_info=True)
        return blob


def _embed_pdf(blob, payload):
    import pikepdf  # lazy: app (and tests without the wheel) run without it
    with pikepdf.open(io.BytesIO(blob)) as pdf:
        pdf.docinfo[pikepdf.Name("/" + META_KEY)] = payload
        out = io.BytesIO()
        pdf.save(out)
        return out.getvalue()


_CUSTOM_PART = "docProps/custom.xml"
_CT_OVERRIDE = (
    '<Override PartName="/docProps/custom.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>'
)
_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
)


def _embed_ooxml(blob, payload):
    with zipfile.ZipFile(io.BytesIO(blob)) as zin:
        names = zin.namelist()
        # Don't clobber a document that already carries custom properties, and bail
        # on anything that isn't a well-formed OPC package (→ passthrough).
        if _CUSTOM_PART in names:
            return blob
        if "[Content_Types].xml" not in names or "_rels/.rels" not in names:
            return blob
        entries = {name: zin.read(name) for name in names}

    content_types = entries["[Content_Types].xml"].decode("utf-8")
    rels = entries["_rels/.rels"].decode("utf-8")
    if "</Types>" not in content_types or "</Relationships>" not in rels:
        return blob

    if "/docProps/custom.xml" not in content_types:
        content_types = content_types.replace("</Types>", _CT_OVERRIDE + "</Types>")
    rel = (f'<Relationship Id="{_unique_rel_id(rels)}" Type="{_REL_TYPE}" '
           'Target="docProps/custom.xml"/>')
    rels = rels.replace("</Relationships>", rel + "</Relationships>")

    entries["[Content_Types].xml"] = content_types.encode("utf-8")
    entries["_rels/.rels"] = rels.encode("utf-8")
    entries[_CUSTOM_PART] = _custom_props_xml(payload).encode("utf-8")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            zout.writestr(name, data)
    return out.getvalue()


def _unique_rel_id(rels):
    base = "rIdMailAnalyzer"
    rid, n = base, 1
    while f'Id="{rid}"' in rels:
        n += 1
        rid = f"{base}{n}"
    return rid


def _custom_props_xml(payload):
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"'
        ' xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="'
        + META_KEY + '"><vt:lpwstr>' + escape(payload) + "</vt:lpwstr></property></Properties>"
    )


def _embed_zip(blob, payload):
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(blob)) as zin:
        with zipfile.ZipFile(out, "w") as zout:
            for info in zin.infolist():
                zout.writestr(info, zin.read(info.filename))
            zout.comment = payload.encode("utf-8")
    return out.getvalue()


def _embed_png(blob, payload):
    if not blob.startswith(_PNG_SIGNATURE):
        raise ValueError("not a PNG file")
    # iTXt (UTF-8): keyword\0 compFlag compMethod langTag\0 transKeyword\0 text.
    body = (META_KEY.encode("latin-1") + b"\x00"   # keyword + separator
            + b"\x00\x00"                           # compression flag + method (both 0)
            + b"\x00\x00"                           # empty language tag + translated keyword
            + payload.encode("utf-8"))
    chunk = _chunk(b"iTXt", body)
    # Insert immediately before the IEND chunk's 4-byte length field.
    iend = blob.rfind(b"IEND")
    if iend < 4:
        raise ValueError("PNG has no IEND chunk")
    insert_at = iend - 4
    return blob[:insert_at] + chunk + blob[insert_at:]


_WRITERS = {
    ".pdf": _embed_pdf,
    ".docx": _embed_ooxml,
    ".xlsx": _embed_ooxml,
    ".pptx": _embed_ooxml,
    ".zip": _embed_zip,
    ".png": _embed_png,
}


# ----- reading the marker back out (the inverse of the writers) -----

def read_org_metadata(filename, blob):
    """Return the embedded ``{org_id, org_name, mail_id}`` dict, or ``None``.

    Dispatch is by the lowercased extension of ``filename``; an unhandled
    extension, an absent marker, or any parse failure yields ``None`` (never
    raises). Each per-format reader returns the raw JSON payload string, which is
    parsed here.
    """
    ext = Path(filename or "").suffix.lower()
    reader = _READERS.get(ext)
    if reader is None:
        return None
    try:
        payload = reader(blob)
        if payload is None:
            return None
        data = json.loads(payload)
        return data if isinstance(data, dict) else None
    except Exception:
        log.debug("attach_meta: read failed for %s", ext, exc_info=True)
        return None


def has_org_metadata(filename, blob):
    """Whether ``blob`` carries this app's org marker (i.e. we downloaded it)."""
    return read_org_metadata(filename, blob) is not None


def _read_pdf(blob):
    import pikepdf  # lazy: without the wheel PDFs simply can't be identified
    with pikepdf.open(io.BytesIO(blob)) as pdf:
        try:
            return str(pdf.docinfo[pikepdf.Name("/" + META_KEY)])
        except KeyError:
            return None


def _read_ooxml(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        if _CUSTOM_PART not in z.namelist():
            return None
        xml = z.read(_CUSTOM_PART)
    root = ET.fromstring(xml)
    for prop in root:
        if prop.tag.rsplit("}", 1)[-1] == "property" and prop.get("name") == META_KEY:
            for child in prop:
                if child.tag.rsplit("}", 1)[-1] == "lpwstr":
                    return child.text
    return None


def _read_zip(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        comment = z.comment
    return comment.decode("utf-8") if comment else None


def _read_png(blob):
    if not blob.startswith(_PNG_SIGNATURE):
        return None
    keyword = META_KEY.encode("latin-1")
    pos = len(_PNG_SIGNATURE)
    while pos + 8 <= len(blob):
        (length,) = struct.unpack(">I", blob[pos:pos + 4])
        ctype = blob[pos + 4:pos + 8]
        body = blob[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctype == b"iTXt" and body.startswith(keyword + b"\x00"):
            # keyword\0 compFlag(1) compMethod(1) lang\0 transKeyword\0 text
            rest = body[len(keyword) + 1 + 2:]      # drop keyword\0 + the two flags
            rest = rest[rest.find(b"\x00") + 1:]    # drop language tag + \0
            rest = rest[rest.find(b"\x00") + 1:]    # drop translated keyword + \0
            return rest.decode("utf-8")
        if ctype == b"IEND":
            break
    return None


_READERS = {
    ".pdf": _read_pdf,
    ".docx": _read_ooxml,
    ".xlsx": _read_ooxml,
    ".pptx": _read_ooxml,
    ".zip": _read_zip,
    ".png": _read_png,
}
