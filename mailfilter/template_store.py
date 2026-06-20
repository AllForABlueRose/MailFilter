"""Named search templates (presets), stored as a directory of PNG image files.

Each template is one PNG in :data:`config.TEMPLATES_DIR`, holding the same
image-packed payload the export/import feature uses (see :mod:`mailfilter.imgcodec`):
the directory *is* the storage and the interchange format at once, so there is no
separate JSON cache. Listing scans the folder; saving writes a file; exporting
serves one; importing drops one in.

Each body is passed through :func:`mailfilter.settings_store.coerce`, so a
template can only ever hold the known search fields — the same schema the sidebar
persists. An in-memory index (name -> settings, name -> file path), rebuilt by
:meth:`load` and maintained on every write, backs the fast read paths; it is
guarded by an ``RLock``.

Unlike the mail cache and last-used settings (DPAPI/base64 at rest, see
``crypto``), template files are only obfuscated by the image packing, never
encrypted: a template is meant to move between machines, so it carries no
machine binding. Search presets (keywords/filters) are far less sensitive than
mail content. There is no persisted "active template" pointer — the UI derives
the active selection by matching the live search against the saved templates.
"""

import json
import logging
import os
import threading
from pathlib import Path

from . import imgcodec, util
from .settings_store import DEFAULTS, coerce

log = logging.getLogger(__name__)

MAX_NAME_LEN = 60
MAX_TEMPLATES = 100  # bounds the folder; a generous ceiling for hand-curated presets

# Stamped into each image payload so a future format change is detectable.
_PAYLOAD_VERSION = 1


class TemplateStore:

    def __init__(self, directory):
        self._dir = Path(directory)
        self._lock = threading.RLock()
        self._templates = {}   # name -> coerced settings dict
        self._paths = {}       # name -> Path of its .png file

    def load(self):
        if not self._dir.is_dir():
            return
        templates, paths = {}, {}
        for path in sorted(self._dir.glob("*.png")):
            try:
                name, settings = _from_png(path.read_bytes())
                name = _clean_name(name)
            except (ValueError, KeyError, TypeError, OSError):
                log.warning("Skipping unreadable template file %s", path.name)
                continue
            templates[name] = coerce(settings, DEFAULTS)
            paths[name] = path
        with self._lock:
            self._templates = templates
            self._paths = paths
        log.info("Loaded %d search template(s) from %s", len(templates), self._dir)

    def snapshot(self):
        """The dropdown's view: sorted names and every body. No active pointer —
        the UI derives the active selection from the live search."""
        with self._lock:
            return {
                "names": sorted(self._templates),
                "templates": {n: dict(b) for n, b in self._templates.items()},
            }

    def get(self, name):
        """One template's settings (a copy), or ``None`` if unknown."""
        with self._lock:
            body = self._templates.get(name)
            return dict(body) if body is not None else None

    def save(self, name, settings):
        """Create or overwrite ``name`` from ``settings``; return the snapshot.

        Raises ``ValueError`` for a blank name or once :data:`MAX_TEMPLATES`
        distinct names already exist. Writes the PNG atomically.
        """
        name = _clean_name(name)
        body = coerce(settings, DEFAULTS)
        with self._lock:
            if name not in self._templates and len(self._templates) >= MAX_TEMPLATES:
                raise ValueError(f"template limit reached ({MAX_TEMPLATES})")
            path = self._paths.get(name) or self._new_path(name)
            self._dir.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, _to_png(name, body))
            self._templates[name] = body
            self._paths[name] = path
            return self.snapshot()

    def delete(self, name):
        """Remove ``name``'s file if present; return the snapshot."""
        with self._lock:
            path = self._paths.pop(name, None)
            if path is not None:
                self._templates.pop(name, None)
                try:
                    os.remove(path)
                except OSError:
                    log.warning("Could not delete template file %s", path)
            return self.snapshot()

    def export_image(self, name):
        """The PNG bytes for ``name`` (re-encoded from its settings), or ``None``."""
        with self._lock:
            body = self._templates.get(name)
            if body is None:
                return None
            return _to_png(name, dict(body))

    def import_image(self, data):
        """Decode a template PNG and save it; return ``(name, snapshot)``.

        Raises (``ValueError``/``KeyError``/``TypeError``, including
        ``imgcodec.TemplateImageError``) if ``data`` is not a valid template image.
        """
        name, settings = _from_png(data)
        return name, self.save(name, settings)

    def _new_path(self, name):
        # A non-colliding .png path; the authoritative name lives in the payload,
        # so the filename is only a convenience and may be suffixed on collision.
        base = util.safe_filename(name, "template")
        taken = set(self._paths.values())
        candidate = self._dir / f"{base}.png"
        n = 1
        while candidate in taken or candidate.exists():
            candidate = self._dir / f"{base}_{n}.png"
            n += 1
        return candidate


def _to_png(name, settings):
    payload = json.dumps(
        {"version": _PAYLOAD_VERSION, "name": name, "settings": settings}
    ).encode("utf-8")
    return imgcodec.encode(payload)


def _from_png(data):
    parsed = json.loads(imgcodec.decode(data))
    if not isinstance(parsed, dict):
        raise ValueError("template payload is not an object")
    return parsed["name"], parsed.get("settings") or {}


def _atomic_write(path, data):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _clean_name(name):
    name = str(name or "").strip()[:MAX_NAME_LEN]
    if not name:
        raise ValueError("template name is required")
    return name
