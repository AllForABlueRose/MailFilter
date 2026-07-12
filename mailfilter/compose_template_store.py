"""Persistence for the reply templates Composer authors and Press runs.

A reply template is a small dict: a name, the master ``body`` (literal text plus
the ``{{ }}`` / ``{% if %}`` holes the DSL renders -- see ``template_lang.py``),
and ``attachment_expr`` (a DSL expression resolving to the filename to look up on
the file server; blank means "use the row's file name"). The body is rendered
once per spreadsheet row.

Like the other stores, the single JSON list is guarded by an ``RLock``, written
atomically, and encoded at rest through ``crypto`` (via ``persistence``). This
store owns the *definitions* only; ``bulk_compose.py`` renders them.

Dependency direction: compose_template_store -> template_lang (validation),
persistence (-> crypto). It never imports bulk_compose / routes.
"""

import logging
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

import config

from . import persistence, template_lang

log = logging.getLogger(__name__)

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_COLOR = "#0ea5e9"


class ComposeTemplateStore:

    def __init__(self, cache_file):
        self._cache_file = Path(cache_file)
        self._lock = threading.RLock()
        self._items = {}  # id -> template dict

    def load(self):
        raw, _alg = persistence.load_encoded(self._cache_file)
        items = {}
        if isinstance(raw, list):
            for entry in raw:
                coerced = self._coerce(entry)
                if coerced is not None:
                    items[coerced["id"]] = coerced
        with self._lock:
            self._items = items
        log.info("Loaded %d reply template(s)", len(items))

    def snapshot(self):
        """Every template, oldest-first (creation order), as independent copies."""
        with self._lock:
            ordered = sorted(self._items.values(), key=lambda t: t.get("created", ""))
            return [dict(t) for t in ordered]

    def get(self, tid):
        with self._lock:
            current = self._items.get(tid)
            return dict(current) if current is not None else None

    def create(self, raw):
        coerced = self._coerce(raw, new=True)
        with self._lock:
            self._items[coerced["id"]] = coerced
            self._save()
            return dict(coerced)

    def update(self, tid, raw):
        with self._lock:
            current = self._items.get(tid)
            if current is None:
                return None
            merged = self._coerce({**current, **(raw or {}), "id": tid}, base=current)
            self._items[tid] = merged
            self._save()
            return dict(merged)

    def delete(self, tid):
        with self._lock:
            existed = self._items.pop(tid, None) is not None
            if existed:
                self._save()
            return existed

    def _coerce(self, raw, base=None, new=False):
        """Normalize one template: known fields only, typed and bounded.

        Validates the body and attachment expression through ``template_lang`` and
        records the first error in ``error`` (rather than rejecting the save) so a
        half-finished template can still be stored and fixed in the editor; the
        preview/commit path refuses to run a template that carries an error.
        Returns ``None`` for a non-dict (so a corrupt cache entry is dropped).
        """
        if not isinstance(raw, dict):
            return None
        base = base or {}

        if new or not raw.get("id"):
            tid = uuid.uuid4().hex
            created = datetime.now().strftime(config.RECEIVED_FORMAT)
        else:
            tid = str(raw["id"])
            created = raw.get("created") or base.get("created") \
                or datetime.now().strftime(config.RECEIVED_FORMAT)

        color = raw.get("color", base.get("color", DEFAULT_COLOR))
        if not (isinstance(color, str) and _HEX_RE.match(color)):
            color = base.get("color", DEFAULT_COLOR)

        name = str(raw.get("name", base.get("name", "")))[
            :config.COMPOSE_TEMPLATE_NAME_MAX].strip() or "Untitled"
        body = str(raw.get("body", base.get("body", "")))[:config.COMPOSE_TEMPLATE_BODY_MAX]
        attachment_expr = str(raw.get("attachment_expr",
                                      base.get("attachment_expr", "")))[
            :config.COMPOSE_TEMPLATE_EXPR_MAX].strip()

        error = validate(body, attachment_expr)

        return {
            "id": tid,
            "name": name,
            "color": color,
            "body": body,
            "attachment_expr": attachment_expr,
            "error": error,
            "created": created,
        }

    def _save(self):
        # Caller must hold the lock. Persist as a list (creation order).
        ordered = sorted(self._items.values(), key=lambda t: t.get("created", ""))
        persistence.save_encoded(self._cache_file, ordered)


def validate(body, attachment_expr):
    """First template-language error in body/attachment_expr, or "" if both parse.

    Public because Composer previews an *unsaved* template straight from the
    editor: it must be judged by the same rule that will judge it on save."""
    try:
        template_lang.validate(body)
    except template_lang.TemplateError as e:
        return f"body: {e}"
    if attachment_expr:
        try:
            template_lang.validate_expr(attachment_expr)
        except template_lang.TemplateError as e:
            return f"attachment name: {e}"
    return ""
