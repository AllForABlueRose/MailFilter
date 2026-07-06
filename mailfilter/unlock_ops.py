"""The Unlock Station engine: list today's workspace files and unlock them.

Headless (pure of Flask/COM), so the same operations could later run from an
automation. It imports **no store** — the HTTP route resolves each assigned Key
Vault secret and each file's customer organization (from the folder manifest) and
hands them in; this module only touches the filesystem and the sidecar manifest.

Two entry points:

* :func:`list_workspace_files` — describe every file in
  ``<WORKSPACE_DIR>/<YYYY-MM-DD>/``: its ``kind`` (zip / excel / other), whether it
  is ``encrypted`` (detected live, no password needed), its customer organization
  (from :mod:`mailfilter.workspace_manifest`, blank for user-placed files), and its
  ``source`` (``download`` when recorded in the manifest, else ``external``).
* :func:`unlock_files` — for a batch of ``{filename: assignment}``:
  - **zip** (with or without a key): extract into a transient subfolder (decoding
    legacy Shift-JIS entry names so Japanese file names survive), tag each extracted
    file with the zip's organization (``_<org name>`` on the stem + a manifest
    entry), move it up into the dated folder, then delete the archive and the
    subfolder.
  - **excel** (with a key): decrypt to a temp file, verify it really decrypted,
    then replace the original in place (no stem suffix) and record the org.
  Each produced file **inherits the originating mail's datetime** from the source
  file's manifest entry (falling back to the source file's own mtime for files with
  no mail origin). Each file is processed in its own try/except: a failure is
  collected and the batch moves on to the next file.

Heavy/optional packages (``pyzipper`` for AES zips, ``msoffcrypto`` for Excel,
``openpyxl`` to verify a decrypted workbook) are imported lazily, so the app runs
where they are absent — an unlock that needs a missing package just reports an
error for that file.
"""

import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import config

from . import workspace_manifest, workspace_ops

log = logging.getLogger(__name__)

# Compound File Binary (OLE2) magic: a password-protected .xlsx is an OLE2
# container, not a ZIP, so this is a dependency-free "is it encrypted" sniff.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_EXTS = (".zip",)
_EXCEL_EXTS = (".xlsx", ".xlsm", ".xls")


def today_folder():
    """The dated workspace folder for today (may not exist)."""
    return config.WORKSPACE_DIR / datetime.now().strftime("%Y-%m-%d")


def _kind(name):
    ext = Path(name).suffix.lower()
    if ext in _ZIP_EXTS:
        return "zip"
    if ext in _EXCEL_EXTS:
        return "excel"
    return "other"


def _zip_is_encrypted(path):
    try:
        with zipfile.ZipFile(path) as z:
            # Bit 0 of an entry's general-purpose flag marks it encrypted (both
            # traditional ZipCrypto and WinZip AES set it). Reading the central
            # directory needs no password.
            return any(info.flag_bits & 0x1 for info in z.infolist())
    except Exception:
        return False


def _excel_is_encrypted(path):
    try:
        import msoffcrypto
        with open(path, "rb") as fh:
            return bool(msoffcrypto.OfficeFile(fh).is_encrypted())
    except ImportError:
        # No msoffcrypto: fall back to the OLE2 magic sniff (an encrypted OOXML
        # file is an OLE2 container; a plain .xlsx starts with the ZIP "PK" magic).
        try:
            with open(path, "rb") as fh:
                return fh.read(8) == _OLE2_MAGIC
        except OSError:
            return False
    except Exception:
        return False


def _is_encrypted(path, kind):
    if kind == "zip":
        return _zip_is_encrypted(path)
    if kind == "excel":
        return _excel_is_encrypted(path)
    return False


def list_workspace_files():
    """Describe today's workspace files (see module docstring for the shape).

    Returns ``{"exists", "folder", "files": [{name, kind, encrypted, org_id,
    org_name, source}, ...]}``. ``exists`` is ``False`` when the dated folder is
    absent (the UI shows "Today's workspace does not exist…"). The manifest file
    itself and any subdirectories are omitted.
    """
    folder = today_folder()
    if not folder.is_dir():
        return {"exists": False, "folder": str(folder), "files": []}
    manifest = workspace_manifest.load(str(folder))
    files = []
    for entry in sorted(folder.iterdir()):
        if not entry.is_file() or entry.name == config.WORKSPACE_MANIFEST_NAME:
            continue
        meta = manifest.get(entry.name)
        kind = _kind(entry.name)
        files.append({
            "name": entry.name,
            "kind": kind,
            "encrypted": _is_encrypted(entry, kind),
            "org_id": (meta or {}).get("org_id", ""),
            "org_name": (meta or {}).get("org_name", ""),
            "received": _listed_received(meta, entry),
            "source": "download" if meta is not None else "external",
        })
    return {"exists": True, "folder": str(folder), "files": files}


def _listed_received(meta, entry):
    """The datetime shown/sorted for a workspace file (``config.RECEIVED_FORMAT``).

    Prefer the originating mail's datetime from the manifest; fall back to the
    file's own mtime for files with no mail origin (user-placed / external)."""
    received = (meta or {}).get("received") or ""
    if received:
        return received
    try:
        return datetime.fromtimestamp(entry.stat().st_mtime).strftime(config.RECEIVED_FORMAT)
    except OSError:
        return ""


def unlock_files(assignments):
    """Unlock a batch of files. ``assignments`` maps a filename to
    ``{"secret", "org_id", "org_name", "key_kind", "file_kind"}`` (``secret`` and
    ``key_kind`` are ``None`` for an unassigned zip).

    Returns ``{"folder", "unlocked": [{name, outputs, org_id, file_kind,
    key_kind}], "errors": [{name, error}]}``. Per-file failures never abort the
    batch. Files that no longer exist, or whose ``file_kind`` is unhandled, are
    reported as errors.
    """
    folder = today_folder()
    unlocked, errors = [], []
    for name, a in (assignments or {}).items():
        path = folder / name
        try:
            if not path.is_file():
                raise FileNotFoundError("file is no longer in the workspace")
            file_kind = a.get("file_kind") or _kind(name)
            if file_kind == "zip":
                outputs = _unlock_zip(folder, path, a)
            elif file_kind == "excel":
                outputs = _unlock_excel(folder, path, a)
            else:
                raise ValueError(f"cannot unlock a '{file_kind}' file")
            unlocked.append({
                "name": name, "outputs": outputs, "org_id": a.get("org_id", ""),
                "file_kind": file_kind, "key_kind": a.get("key_kind"),
            })
        except Exception as e:
            log.warning("Unlock failed for %s: %s", name, e)
            errors.append({"name": name, "error": str(e)})
    log.info("Unlock Station processed %d file(s): %d ok, %d error(s)",
             len(assignments or {}), len(unlocked), len(errors))
    return {"folder": str(folder), "unlocked": unlocked, "errors": errors}


def _resolve_received_dt(meta, src_path):
    """``(times, received)`` to stamp on files derived from a source file.

    ``times`` is an ``(atime, mtime)`` epoch tuple for :func:`os.utime` (or ``None``
    when no datetime is available). Prefer the originating mail's datetime from the
    manifest ``meta``; fall back to the source file's own mtime for files with no
    mail origin — in which case ``received`` is blank (no mail to attribute)."""
    received = (meta or {}).get("received") or ""
    if received:
        try:
            ts = datetime.strptime(received, config.RECEIVED_FORMAT).timestamp()
            return (ts, ts), received
        except ValueError:
            pass
    try:
        ts = src_path.stat().st_mtime
        return (ts, ts), ""
    except OSError:
        return None, ""


def _unlock_zip(folder, path, a):
    """Extract ``path`` (optionally with a key), tag + move the contents up into
    ``folder``, then delete the archive and the temp subfolder. Returns the list of
    produced file names."""
    secret = a.get("secret")
    org_id, org_name = a.get("org_id", ""), a.get("org_name", "")
    meta = workspace_manifest.lookup(str(folder), path.name) or {}
    mail_id = meta.get("mail_id", "")
    times, received = _resolve_received_dt(meta, path)
    extract_dir = Path(tempfile.mkdtemp(prefix=config.UNLOCK_EXTRACT_DIRNAME,
                                        dir=str(folder)))
    outputs = []
    try:
        _extract_zip(path, extract_dir, secret)
        for src in sorted(p for p in extract_dir.rglob("*") if p.is_file()):
            new_name = src.name
            if org_name:
                new_name = workspace_ops.append_stem(new_name, org_name)
            target = workspace_ops.unique_path(folder, new_name, 0)
            shutil.move(str(src), str(target))
            if times is not None:
                os.utime(target, times)
            workspace_manifest.record(str(folder), target.name, {
                "org_id": org_id, "org_name": org_name, "mail_id": mail_id,
                "received": received})
            outputs.append(target.name)
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
    # Only reached when extraction + moves succeeded; clean up the archive.
    path.unlink()
    workspace_manifest.remove(str(folder), path.name)
    return outputs


def _decode_zip_name(info):
    """Best-effort correct Unicode name for a zip entry.

    Python's zipfile decodes an entry name as UTF-8 only when general-purpose bit 11
    (``0x800``) is set, otherwise as CP437. Windows/Japanese tooling stores names as
    Shift-JIS (cp932) bytes without that flag, so recover the original bytes from the
    CP437 mis-decode and re-decode them; fall back to the raw name if that fails."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name  # already decoded as UTF-8
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for enc in (config.UNLOCK_ZIP_LEGACY_ENCODING, "shift_jis"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return name


def _safe_extract_dest(extract_dir, name):
    """Resolve ``name`` under ``extract_dir``, rejecting zip-slip (``..``) escapes."""
    base = extract_dir.resolve()
    dest = (base / name).resolve()
    if base != dest and base not in dest.parents:
        raise ValueError(f"unsafe zip entry path: {name!r}")
    return dest


def _extract_members(z, extract_dir):
    """Extract every member of an open zip, decoding legacy (Shift-JIS) names and
    guarding against path traversal — a name-aware replacement for ``extractall``."""
    for info in z.infolist():
        dest = _safe_extract_dest(extract_dir, _decode_zip_name(info))
        if info.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with z.open(info) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)


def _extract_zip(path, extract_dir, secret):
    # Only reach for pyzipper when the archive is actually encrypted — a plain zip
    # that happens to get a key assigned (Smart Unlock assigns to every zip) still
    # extracts with the stdlib, so pyzipper isn't needed unless there's real crypto.
    if secret and _zip_is_encrypted(path):
        # pyzipper reads WinZip AES (and, via its zipfile base, traditional
        # ZipCrypto); stdlib zipfile cannot do AES.
        import pyzipper
        with pyzipper.AESZipFile(str(path)) as z:
            z.setpassword(secret.encode("utf-8"))
            _extract_members(z, extract_dir)
    else:
        with zipfile.ZipFile(str(path)) as z:
            _extract_members(z, extract_dir)


def _unlock_excel(folder, path, a):
    """Decrypt ``path`` with its key, verify, then replace it in place. Returns the
    (unchanged) file name in a list."""
    secret = a.get("secret")
    if not secret:
        raise ValueError("no key assigned for this Excel file")
    org_id, org_name = a.get("org_id", ""), a.get("org_name", "")
    meta = workspace_manifest.lookup(str(folder), path.name) or {}
    mail_id = meta.get("mail_id", "")
    times, received = _resolve_received_dt(meta, path)

    import msoffcrypto
    fd, tmp_name = tempfile.mkstemp(prefix="._unlock_xlsx", dir=str(folder))
    tmp = Path(tmp_name)
    try:
        with path.open("rb") as fh, os.fdopen(fd, "wb") as out:
            office = msoffcrypto.OfficeFile(fh)
            office.load_key(password=secret)
            office.decrypt(out)
        _verify_decrypted_excel(tmp)
        path.unlink()
        os.replace(str(tmp), str(path))          # temp -> original file name
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    # Re-apply the originating datetime: os.replace reset the file's mtime to now.
    if times is not None:
        os.utime(path, times)
    workspace_manifest.record(str(folder), path.name, {
        "org_id": org_id, "org_name": org_name, "mail_id": mail_id,
        "received": received})
    return [path.name]


def _verify_decrypted_excel(path):
    """Raise unless ``path`` is now a real, decrypted workbook.

    A decrypted .xlsx is a ZIP (starts with "PK"), never an OLE2 container. When
    openpyxl is available we also open it, the strongest signal it decrypted.
    """
    with open(path, "rb") as fh:
        head = fh.read(8)
    if head.startswith(_OLE2_MAGIC) or not zipfile.is_zipfile(str(path)):
        raise ValueError("decryption did not produce a valid Excel file (wrong key?)")
    try:
        import openpyxl
    except ImportError:
        return
    # Load through a file handle, not the path: the decrypted output is a temp file
    # with no .xlsx extension, and openpyxl rejects an unknown extension when given a
    # path — but reads a file-like object as a zip regardless of name.
    with open(path, "rb") as fh:
        wb = openpyxl.load_workbook(fh, read_only=True)
        wb.close()
