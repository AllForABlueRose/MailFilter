"""Persistence for Smart Password Detection settings (patterns + rules).

A sibling of :class:`mailfilter.settings_store.SettingsStore`: a single JSON
object, guarded by an ``RLock``, written atomically and encoded at rest through
the same ``crypto`` seam as the mail cache. Holds the user's detection
``patterns`` (regex + name + enabled) and the six ``rules`` (four toggles + two
length bounds). Seeded from :mod:`config` defaults on first use so the feature
works out of the box, then edited from the settings popup.

The store only stores and clamps; it does **not** compile or evaluate regex
(that is :mod:`mailfilter.password_detect`, driven by the route), so a malformed
pattern can be saved, surfaced as broken, and fixed without wedging the store.
"""

import copy
import logging
import threading
from pathlib import Path

import config

from . import persistence

log = logging.getLogger(__name__)


def default_settings():
    """A fresh defaults dict (deep-copied so callers can't mutate config)."""
    return {
        "patterns": [
            {"template": p["template"], "value_regex": p.get("value_regex", ""),
             "enabled": True}
            for p in config.PASSWORD_DEFAULT_PATTERNS
        ],
        "rules": dict(config.PASSWORD_RULE_DEFAULTS),
    }


def _clamp_int(value, low, high, fallback):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, n))


def _coerce_patterns(raw):
    """Normalize the component list: known keys only, capped lengths/count.

    Each entry is ``{template, value_regex, enabled}`` — components carry no name
    (they are identified by their 1-based position). Drops non-dicts and
    blank-template entries; keeps order; defaults ``enabled`` to True. The
    template keeps its internal newlines (they are the layout cue and compile to
    flexible whitespace) but is length-capped; the whole list is capped at
    ``PASSWORD_MAX_PATTERNS``. The store never compiles — a template missing its
    placeholder is saved and surfaced as broken by ``password_detect``.
    """
    out = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        template = str(entry.get("template") or "")[:config.PASSWORD_TEMPLATE_MAX]
        if not template.strip():
            continue
        value_regex = str(entry.get("value_regex") or "").strip()[:config.PASSWORD_VALUE_REGEX_MAX]
        enabled = bool(entry.get("enabled", True))
        out.append({"template": template, "value_regex": value_regex, "enabled": enabled})
        if len(out) >= config.PASSWORD_MAX_PATTERNS:
            break
    return out


def _coerce_rules(raw, base):
    """Apply known rule fields from ``raw`` over ``base`` with clamping.

    Booleans coerce with ``bool``; the two length bounds clamp into
    ``[PASSWORD_LENGTH_FLOOR, PASSWORD_LENGTH_CEIL]`` and are then ordered so
    ``min_length <= max_length`` (a swapped pair from a slider is repaired).
    """
    rules = dict(base)
    if isinstance(raw, dict):
        for key in ("no_japanese", "no_link", "no_repeating", "no_file"):
            if key in raw and raw[key] is not None:
                rules[key] = bool(raw[key])
        rules["min_length"] = _clamp_int(
            raw.get("min_length", rules["min_length"]),
            config.PASSWORD_LENGTH_FLOOR, config.PASSWORD_LENGTH_CEIL, rules["min_length"])
        rules["max_length"] = _clamp_int(
            raw.get("max_length", rules["max_length"]),
            config.PASSWORD_LENGTH_FLOOR, config.PASSWORD_LENGTH_CEIL, rules["max_length"])
    if rules["min_length"] > rules["max_length"]:
        rules["min_length"], rules["max_length"] = rules["max_length"], rules["min_length"]
    return rules


def coerce(raw, base=None):
    """Return a clean settings dict: known patterns + rules from ``raw`` over ``base``.

    ``base`` defaults to :func:`default_settings`. Unknown top-level keys are
    dropped; ``patterns`` is replaced wholesale when present (the editor always
    sends the full list); ``rules`` is merged field-by-field.
    """
    base = copy.deepcopy(default_settings()) if base is None else copy.deepcopy(base)
    if not isinstance(raw, dict):
        return base
    if "patterns" in raw and raw["patterns"] is not None:
        base["patterns"] = _coerce_patterns(raw["patterns"])
    base["rules"] = _coerce_rules(raw.get("rules"), base["rules"])
    return base


class PasswordSettingsStore:

    def __init__(self, cache_file):
        self._cache_file = Path(cache_file)
        self._lock = threading.RLock()
        self._settings = default_settings()

    def load(self):
        raw, _alg = persistence.load_encoded(self._cache_file)
        if isinstance(raw, dict):
            with self._lock:
                self._settings = coerce(raw, default_settings())
            log.info("Loaded password-detection settings from cache")

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._settings)

    def update(self, raw):
        """Merge known fields from ``raw`` over the current settings; persist."""
        with self._lock:
            self._settings = coerce(raw, self._settings)
            self._save()
            return copy.deepcopy(self._settings)

    def _save(self):
        # Caller must hold the lock.
        persistence.save_encoded(self._cache_file, self._settings)
