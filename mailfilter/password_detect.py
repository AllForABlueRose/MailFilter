"""Pure, rules-based password detection — no Flask, no COM, no AI, no network.

The Smart Password Detection feature scans cached mail text for plaintext
passwords customers send. It works in two stages, both here:

1. **Patterns** are authored as *components*, not raw regex (:func:`build_regex`):
   the user writes the literal context they expect — e.g. ``password:`` — and
   drops the ``config.PASSWORD_PLACEHOLDER`` token where the password sits. The
   context is compiled as literal text whose runs of whitespace become ``\\s*``,
   so spacing differences (and a value written on the *next* line) still match;
   the placeholder becomes the single capturing group, optionally constrained by
   a per-pattern value regex (blank → ``config.PASSWORD_GENERIC_VALUE_REGEX``).
   Everything compiles ``IGNORECASE | MULTILINE``.

2. **Rules** (toggles + length sliders) reject candidates that are clearly not
   passwords: Japanese text, links, repeated symbols, filenames, and anything
   outside the configured length band. A candidate is reported only if it passes
   every *enabled* rule.

Everything is stdlib + :mod:`config`; the route compiles once and scans the
mail snapshot, so a request does no Outlook or network work. The settings that
drive it live in :mod:`mailfilter.password_settings_store`.
"""

import re

import config

# Japanese scripts: Hiragana, Katakana, CJK Unified Ideographs (+ Ext-A), and
# halfwidth Katakana. A password "must not contain Japanese characters" (rule 1),
# so a single character in any of these ranges rejects the candidate.
_JAPANESE_RE = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]"
)

# A candidate "is a link" (rule 2) if it carries a URL scheme, starts with
# "www.", or is a bare ``domain.tld/...`` (a trailing slash is required so a
# dotted-but-not-a-URL password like "Pass.word" is not mistaken for one).
_LINK_RE = re.compile(
    r"^(?:(?:https?|ftp|mailto)://|www\.)|^[\w.-]+\.[a-z]{2,}/",
    re.IGNORECASE,
)


# The placeholder token, as a regex: <{( anything )}>. The text inside the
# parens is a human label (its contents do NOT become the value regex — that is
# the separate per-pattern field), so any label is accepted.
_PLACEHOLDER_RE = re.compile(r"<\{\(.*?\)\}>")


class CompiledPattern:
    """One compiled detection pattern: its 1-based ``number`` and ``regex``."""

    __slots__ = ("number", "regex")

    def __init__(self, number, regex):
        self.number = number
        self.regex = regex


def _literal_to_regex(text):
    """Escape ``text`` as a literal, but turn each whitespace run into ``\\s*``.

    This is what makes the context box forgiving: the spaces and newlines the
    user types are matched as "any (or no) whitespace here", so a marker and a
    value separated by a space, several spaces, or a line break all match the
    same component.
    """
    out = []
    for part in re.split(r"(\s+)", text):
        if not part:
            continue
        out.append(r"\s*" if part.isspace() else re.escape(part))
    return "".join(out)


def build_regex(template, value_regex):
    """Compose a component into a regex source, returning ``(source, error)``.

    ``template`` is the literal context box; it must contain exactly one
    ``config.PASSWORD_PLACEHOLDER``. ``value_regex`` is the optional per-pattern
    matcher for the password itself (blank → ``PASSWORD_GENERIC_VALUE_REGEX``).
    The literal text around the placeholder is escaped (whitespace → ``\\s*``)
    and the placeholder becomes the single capturing group. ``error`` is a
    human-readable string (and ``source`` is ``None``) when the template has no
    placeholder, more than one, or the value regex does not compile.
    """
    template = template or ""
    spots = list(_PLACEHOLDER_RE.finditer(template))
    if not spots:
        return None, f"add a {config.PASSWORD_PLACEHOLDER} marker where the password is"
    if len(spots) > 1:
        return None, f"use only one {config.PASSWORD_PLACEHOLDER} marker"
    value = (value_regex or "").strip() or config.PASSWORD_GENERIC_VALUE_REGEX
    try:
        re.compile(value)
    except re.error as e:
        return None, f"password pattern: {e}"
    spot = spots[0]
    source = (_literal_to_regex(template[:spot.start()])
              + "(" + value + ")"
              + _literal_to_regex(template[spot.end():]))
    return source, None


def compile_patterns(patterns):
    """Compile enabled component patterns, returning ``(compiled, errors)``.

    ``patterns`` is the list of ``{template, value_regex, enabled}`` dicts from
    the settings store. Components have no name — they are identified by their
    **1-based position** in the list. Disabled and blank-template patterns are
    skipped; a component that can't be built/compiled is dropped and recorded in
    ``errors`` as ``(number, message)`` so the UI can flag which one is broken
    without failing the whole scan. Compiles with IGNORECASE | MULTILINE.
    """
    compiled, errors = [], []
    for number, entry in enumerate(patterns or [], start=1):
        if not isinstance(entry, dict) or not entry.get("enabled", True):
            continue
        template = entry.get("template") or ""
        if not template.strip():
            continue
        source, error = build_regex(template, entry.get("value_regex"))
        if error:
            errors.append((number, error))
            continue
        try:
            regex = re.compile(source, re.IGNORECASE | re.MULTILINE)
        except re.error as e:
            errors.append((number, str(e)))
            continue
        compiled.append(CompiledPattern(number, regex))
    return compiled, errors


def _candidate(match):
    """The captured password from a regex match: group 1, else the whole match."""
    if match.re.groups:
        return match.group(1) or ""
    return match.group(0) or ""


def _is_repeating(value, max_period):
    """True if ``value`` is a single unit of length 1..``max_period`` repeated.

    Catches "aaaaaaaa" (period 1), "abababab" (period 2), "--------", etc. Only
    exact whole-string repetitions count, so a password that merely *contains* a
    run is unaffected.
    """
    n = len(value)
    for period in range(1, min(max_period, n) + 1):
        if n % period == 0 and value == value[:period] * (n // period):
            return True
    return False


def _is_symbol_run(value, max_distinct):
    """True if ``value`` is a styling run: all non-alphanumeric symbols, with at
    most ``max_distinct`` distinct characters.

    Catches the dividers customers paste into mail bodies — ``==============``,
    ``--------``, ``==----==----``, ``=-~=-~`` — of *any* length or period, which
    the fixed-period :func:`_is_repeating` check misses once they exceed period
    ``PASSWORD_REPEAT_MAX_PERIOD``. A candidate with any letter or digit, or a
    varied all-symbol string (more than ``max_distinct`` distinct characters), is
    left alone.
    """
    if not value or any(ch.isalnum() for ch in value):
        return False
    return len(set(value)) <= max_distinct


def _is_file(value, extensions):
    """True if ``value`` ends in ``.<ext>`` for one of ``extensions`` (any case)."""
    dot = value.rfind(".")
    if dot < 0 or dot == len(value) - 1:
        return False
    return value[dot + 1:].lower() in extensions


def candidate_ok(candidate, rules):
    """True if ``candidate`` passes every *enabled* rule in ``rules``.

    ``rules`` is the coerced rules dict (``no_japanese``/``no_link``/
    ``no_repeating``/``no_file`` booleans + ``min_length``/``max_length`` ints).
    The length band is always applied; the four boolean rules are applied only
    when toggled on.
    """
    if not candidate:
        return False
    length = len(candidate)
    if length < rules.get("min_length", config.PASSWORD_RULE_DEFAULTS["min_length"]):
        return False
    if length > rules.get("max_length", config.PASSWORD_RULE_DEFAULTS["max_length"]):
        return False
    if rules.get("no_japanese") and _JAPANESE_RE.search(candidate):
        return False
    if rules.get("no_link") and _LINK_RE.search(candidate):
        return False
    if rules.get("no_repeating") and (
            _is_repeating(candidate, config.PASSWORD_REPEAT_MAX_PERIOD)
            or _is_symbol_run(candidate, config.PASSWORD_STYLING_MAX_DISTINCT)):
        return False
    if rules.get("no_file") and _is_file(candidate, config.PASSWORD_FILE_EXTENSIONS):
        return False
    return True


def scan_text(text, compiled, rules, cap=None):
    """Unique candidate passwords in ``text`` that pass the rules, first-seen order.

    ``compiled`` is the output of :func:`compile_patterns`; ``rules`` the coerced
    rules dict. Deduplicates case-sensitively (two casings are two passwords) and
    stops once ``cap`` matches are collected (``None`` = no cap).
    """
    if not text or not compiled:
        return []
    seen = set()
    found = []
    for pattern in compiled:
        for match in pattern.regex.finditer(text):
            candidate = _candidate(match).strip()
            if candidate in seen:
                continue
            if candidate_ok(candidate, rules):
                seen.add(candidate)
                found.append(candidate)
                if cap is not None and len(found) >= cap:
                    return found
    return found
