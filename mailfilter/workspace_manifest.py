"""The workspace org-metadata sidecar: one manifest per dated workspace folder.

Records which customer organization each file this app downloaded into a
``<WORKSPACE_DIR>/<YYYY-MM-DD>/`` folder belongs to, as a small JSON map
``{filename: {"org_id", "org_name", "mail_id"}}`` stored INSIDE that folder under
``config.WORKSPACE_MANIFEST_NAME`` and encoded at rest through the ordinary
``persistence``/``crypto`` seam (it carries no secrets).

This replaces the former ``attach_meta`` approach of embedding the org marker into
each file's own bytes. Embedding could not mark an **encrypted** ``.xlsx`` (an
OLE2 container, not a ZIP) and failed on an **encrypted** ``.zip`` (rewriting the
archive needs the password), so a locked file's org was unknowable before
unlocking it — exactly what the Unlock Station needs up front. A folder-local
sidecar sidesteps the file format entirely: org identity is recorded at download
time and read straight back regardless of whether the file is later encrypted.

Two derived signals fall out of "is this filename in the manifest?":

* **App vs. incidental** — a file listed here was downloaded by this app; one on
  disk but absent is user-placed. ``is_app_file`` is what "Cleanup Local
  Workspace" uses to tell them apart (the role the embedded marker used to play),
  and ``external_files`` is what the Unlock Station uses to show user-added files
  with no organization.

Pure of Flask/COM and imports no store; depends only on ``persistence``/``config``
(a leaf, like ``attach_meta`` was). ``workspace_ops`` and ``unlock_ops`` call it.
"""

import logging
from pathlib import Path

import config

from . import persistence

log = logging.getLogger(__name__)

_FIELDS = ("org_id", "org_name", "mail_id")


def _path(folder):
    return Path(folder) / config.WORKSPACE_MANIFEST_NAME


def _coerce(meta):
    """Keep only the known string fields (missing → "")."""
    meta = meta or {}
    return {k: str(meta.get(k) or "") for k in _FIELDS}


def load(folder):
    """The folder's ``{filename: meta}`` map (``{}`` if absent/unreadable)."""
    obj, _alg = persistence.load_encoded(_path(folder))
    return obj if isinstance(obj, dict) else {}


def _save(folder, data):
    path = _path(folder)
    if data:
        persistence.save_encoded(path, data)
    elif path.exists():
        # An emptied manifest is removed rather than left as an empty file, so a
        # folder with only user-placed files carries no sidecar at all.
        try:
            path.unlink()
        except OSError as e:
            log.warning("workspace_manifest: cannot remove empty manifest %s: %s", path, e)


def record(folder, filename, meta):
    """Record ``filename``'s org metadata in ``folder``'s manifest (upsert)."""
    data = load(folder)
    data[filename] = _coerce(meta)
    _save(folder, data)


def remove(folder, filename):
    """Drop ``filename`` from the manifest, if present."""
    data = load(folder)
    if filename in data:
        del data[filename]
        _save(folder, data)


def rename(folder, old_name, new_name):
    """Move the manifest entry from ``old_name`` to ``new_name`` (if any)."""
    data = load(folder)
    if old_name in data:
        data[new_name] = data.pop(old_name)
        _save(folder, data)


def lookup(folder, filename):
    """The ``{org_id, org_name, mail_id}`` for ``filename``, or ``None``."""
    return load(folder).get(filename)


def is_app_file(folder, filename):
    """Whether ``filename`` is recorded in the manifest (i.e. we downloaded it)."""
    return filename in load(folder)


def external_files(folder, names):
    """The subset of ``names`` not in the manifest — files a user placed here.

    The manifest file itself is never reported (it is not a workspace file).
    """
    data = load(folder)
    manifest_name = config.WORKSPACE_MANIFEST_NAME
    return [n for n in names if n != manifest_name and n not in data]
