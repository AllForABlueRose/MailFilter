"""A small, sandboxed template language for reply-draft bodies.

Press renders one master template per row. The template is mostly literal
text with two kinds of holes:

    {{ expr }}                      inline expression -> inserted as text
    {% if cond %} ... {% elif c %} ... {% else %} ... {% endif %}
                                    conditional segments (nestable)

The expression layer is a hand-written evaluator (the same discipline as
``expr.py``) -- deliberately **not** Python ``eval``. It can only:

  * read variables from the supplied context, by dotted name, where every hop is
    a plain dict lookup (``row.file_name``, ``sender.is_internal``). No attribute
    access reaches real Python objects, so ``x.__class__`` resolves to "" (a
    missing key), never to a type.
  * call functions from a fixed registry (:data:`FUNCTIONS`). No other name is
    callable.
  * use a few operators (comparison / boolean / ``+`` / ``-``).

There is no indexing, no imports, no I/O, no loops -- it always terminates and
cannot touch anything outside the context it is handed. That is what makes the
"pseudocode block" idea safe to expose to template authors.

Pure: stdlib only (imports ``config`` only for ftp_link's base URL).
"""

import re
from datetime import datetime

import config

RECEIVED_FORMAT = config.RECEIVED_FORMAT


class TemplateError(ValueError):
    """A template (or an expression inside it) that could not be parsed/evaluated."""


# ----------------------------------------------------------------------------
# Values & truthiness
# ----------------------------------------------------------------------------
# Spreadsheet cells arrive as strings, so "FALSE"/"No"/"0"/"" must read as false
# in a condition while any other text reads as true. Numbers and real bools pass
# through Python's own truthiness.
_FALSEY_STRINGS = {"", "0", "false", "no", "n", "f", "none", "off"}


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() not in _FALSEY_STRINGS
    return bool(value)


def truthy(value):
    """Public alias of the language's truthiness rule (used by bulk_compose to
    read a spreadsheet FTP flag with the same semantics as ``{% if %}``)."""
    return _truthy(value)


def stringify(value):
    """Public alias for how a value renders to text (used by bulk_compose to turn
    an attachment-name expression result into a filename)."""
    return _stringify(value)


def _stringify(value):
    """How a value renders inside ``{{ }}`` / the final body."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ----------------------------------------------------------------------------
# Built-in functions (the only callables the language exposes)
# ----------------------------------------------------------------------------

def _fn_if(cond, a, b=""):
    return a if _truthy(cond) else b


def _fn_default(value, fallback):
    return value if _truthy(value) else fallback


def _fn_date(value, fmt=RECEIVED_FORMAT):
    """Reformat a datetime (or a RECEIVED_FORMAT / ISO string) with ``fmt``."""
    if isinstance(value, datetime):
        dt = value
    else:
        text = _stringify(value).strip()
        if not text:
            return ""
        dt = None
        for parse in (lambda t: datetime.strptime(t, RECEIVED_FORMAT),
                      datetime.fromisoformat):
            try:
                dt = parse(text)
                break
            except ValueError:
                continue
        if dt is None:
            return text  # not a recognizable date: leave it as-is
    try:
        return dt.strftime(_stringify(fmt))
    except (ValueError, TypeError) as e:
        raise TemplateError(f"date(): bad format {fmt!r}: {e}") from e


def _fn_ftp_link(name):
    return config.FTP_LINK_BASE + _stringify(name)


FUNCTIONS = {
    "if": _fn_if,
    "default": _fn_default,
    "upper": lambda s: _stringify(s).upper(),
    "lower": lambda s: _stringify(s).lower(),
    "title": lambda s: _stringify(s).title(),
    "strip": lambda s: _stringify(s).strip(),
    "trim": lambda s: _stringify(s).strip(),
    "date": _fn_date,
    "ftp_link": _fn_ftp_link,
    "contains": lambda h, n: _stringify(n).lower() in _stringify(h).lower(),
    "replace": lambda s, a, b: _stringify(s).replace(_stringify(a), _stringify(b)),
    "concat": lambda *parts: "".join(_stringify(p) for p in parts),
}


# ----------------------------------------------------------------------------
# Expression layer: tokenizer
# ----------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"""
    \s*(?:
        (?P<num>\d+(?:\.\d+)?)
      | (?P<str>"(?:[^"\\]|\\.)*" | '(?:[^'\\]|\\.)*')
      | (?P<op><=|>=|==|!=|&&|\|\||[<>()+\-*/,.])
      | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    )
""", re.VERBOSE)

# Word operators / keyword constants recognized in the name position.
_KEYWORDS = {"and": "&&", "or": "||", "not": "!", "true": True, "false": False}


def _tokenize_expr(text):
    tokens = []
    pos, n = 0, len(text)
    while pos < n:
        if text[pos].isspace():
            pos += 1
            continue
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            raise TemplateError(f"unexpected character {text[pos]!r} in expression")
        pos = m.end()
        if m.group("num") is not None:
            num = m.group("num")
            tokens.append(("num", float(num) if "." in num else int(num)))
        elif m.group("str") is not None:
            tokens.append(("str", _unquote(m.group("str"))))
        elif m.group("op") is not None:
            tokens.append(("op", m.group("op")))
        else:
            word = m.group("name")
            low = word.lower()
            if low in _KEYWORDS:
                kw = _KEYWORDS[low]
                tokens.append(("op", kw) if isinstance(kw, str) else ("const", kw))
            else:
                tokens.append(("name", word))
    return tokens


def _unquote(literal):
    body = literal[1:-1]
    return re.sub(r"\\(.)", r"\1", body)


# ----------------------------------------------------------------------------
# Expression layer: recursive-descent parser -> AST tuples
# ----------------------------------------------------------------------------
# AST node shapes:
#   ('lit', value)
#   ('var', [part, ...])            dotted name
#   ('call', fname, [argnode, ...])
#   ('not', node)
#   ('bin', op, left, right)

class _Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def _next(self):
        tok = self._peek()
        self.i += 1
        return tok

    def _expect_op(self, op):
        kind, val = self._next()
        if kind != "op" or val != op:
            raise TemplateError(f"expected {op!r} in expression")

    def parse(self):
        node = self._parse_or()
        if self.i != len(self.toks):
            raise TemplateError("trailing tokens in expression")
        return node

    def _parse_or(self):
        node = self._parse_and()
        while self._peek() == ("op", "||"):
            self._next()
            node = ("bin", "||", node, self._parse_and())
        return node

    def _parse_and(self):
        node = self._parse_not()
        while self._peek() == ("op", "&&"):
            self._next()
            node = ("bin", "&&", node, self._parse_not())
        return node

    def _parse_not(self):
        if self._peek() == ("op", "!"):
            self._next()
            return ("not", self._parse_not())
        return self._parse_compare()

    def _parse_compare(self):
        node = self._parse_add()
        kind, val = self._peek()
        if kind == "op" and val in ("==", "!=", "<", ">", "<=", ">="):
            self._next()
            return ("bin", val, node, self._parse_add())
        return node

    def _parse_add(self):
        node = self._parse_mul()
        while True:
            kind, val = self._peek()
            if kind == "op" and val in ("+", "-"):
                self._next()
                node = ("bin", val, node, self._parse_mul())
            else:
                return node

    def _parse_mul(self):
        node = self._parse_atom()
        while True:
            kind, val = self._peek()
            if kind == "op" and val in ("*", "/"):
                self._next()
                node = ("bin", val, node, self._parse_atom())
            else:
                return node

    def _parse_atom(self):
        kind, val = self._next()
        if kind == "num" or kind == "str" or kind == "const":
            return ("lit", val)
        if kind == "op" and val == "(":
            node = self._parse_or()
            self._expect_op(")")
            return node
        if kind == "op" and val == "-":
            return ("bin", "-", ("lit", 0), self._parse_atom())
        if kind == "name":
            if self._peek() == ("op", "("):
                return self._parse_call(val)
            return self._parse_var(val)
        raise TemplateError("unexpected end of expression" if kind is None
                            else f"unexpected token {val!r} in expression")

    def _parse_var(self, first):
        parts = [first]
        while self._peek() == ("op", "."):
            self._next()
            kind, val = self._next()
            if kind != "name":
                raise TemplateError("expected a name after '.'")
            parts.append(val)
        return ("var", parts)

    def _parse_call(self, fname):
        self._expect_op("(")
        args = []
        if self._peek() != ("op", ")"):
            args.append(self._parse_or())
            while self._peek() == ("op", ","):
                self._next()
                args.append(self._parse_or())
        self._expect_op(")")
        if fname not in FUNCTIONS:
            raise TemplateError(f"unknown function {fname!r}")
        return ("call", fname, args)


def _parse_expr(text):
    tokens = _tokenize_expr(text)
    if not tokens:
        raise TemplateError("empty expression")
    return _Parser(tokens).parse()


# ----------------------------------------------------------------------------
# Expression layer: evaluator
# ----------------------------------------------------------------------------

def _resolve_var(parts, context):
    """Walk a dotted name through nested dicts only. A missing hop -> ""."""
    value = context
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return ""  # never reach into a real object's attributes
        if value is None:
            return ""
    return value


def _eval(node, context):
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "var":
        return _resolve_var(node[1], context)
    if kind == "not":
        return not _truthy(_eval(node[1], context))
    if kind == "call":
        fn = FUNCTIONS[node[1]]
        args = [_eval(a, context) for a in node[2]]
        try:
            return fn(*args)
        except TemplateError:
            raise
        except TypeError as e:
            raise TemplateError(f"{node[1]}(): {e}") from e
    # binary
    op = node[1]
    if op == "&&":
        return _truthy(_eval(node[2], context)) and _truthy(_eval(node[3], context))
    if op == "||":
        return _truthy(_eval(node[2], context)) or _truthy(_eval(node[3], context))
    left = _eval(node[2], context)
    right = _eval(node[3], context)
    return _eval_binop(op, left, right)


def _eval_binop(op, left, right):
    if op == "==":
        return _eq(left, right)
    if op == "!=":
        return not _eq(left, right)
    if op in ("<", ">", "<=", ">="):
        return _compare(op, left, right)
    if op == "+":
        if isinstance(left, (int, float)) and isinstance(right, (int, float)) \
                and not isinstance(left, bool) and not isinstance(right, bool):
            return left + right
        return _stringify(left) + _stringify(right)  # string concatenation
    if op in ("-", "*", "/"):
        return _arith(op, left, right)
    raise TemplateError(f"unknown operator {op!r}")


def _eq(left, right):
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return _stringify(left) == _stringify(right)


def _compare(op, left, right):
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        a, b = left, right
    else:
        a, b = _stringify(left), _stringify(right)
    if op == "<":
        return a < b
    if op == ">":
        return a > b
    if op == "<=":
        return a <= b
    return a >= b


def _arith(op, left, right):
    try:
        a, b = float(left), float(right)
    except (TypeError, ValueError) as e:
        raise TemplateError(f"non-numeric operand for {op!r}") from e
    if op == "-":
        result = a - b
    elif op == "*":
        result = a * b
    else:
        if b == 0:
            raise TemplateError("division by zero")
        result = a / b
    return int(result) if result == int(result) else result


def eval_expr(text, context):
    """Parse and evaluate one expression against ``context`` (the public entry)."""
    return _eval(_parse_expr(text), context)


# ----------------------------------------------------------------------------
# Template layer: lexer -> chunks
# ----------------------------------------------------------------------------
# A template is a flat sequence of chunks; the parser below turns the {% %} tags
# into a tree.

_CHUNK_RE = re.compile(r"\{\{(.*?)\}\}|\{%(.*?)%\}", re.DOTALL)


def _lex_template(text):
    chunks = []
    last = 0
    for m in _CHUNK_RE.finditer(text):
        if m.start() > last:
            chunks.append(("text", text[last:m.start()]))
        if m.group(1) is not None:                  # {{ expr }}
            chunks.append(("expr", m.group(1).strip()))
        else:                                       # {% tag %}
            chunks.append(("tag", m.group(2).strip()))
        last = m.end()
    if last < len(text):
        chunks.append(("text", text[last:]))
    return chunks


# ----------------------------------------------------------------------------
# Template layer: parser -> node tree
# ----------------------------------------------------------------------------
# Node shapes:
#   ('text', str)
#   ('expr', exprAST)
#   ('if', [(condAST_or_None, [node, ...]), ...])   last branch cond None == else

def _parse_template(text):
    chunks = _lex_template(text)
    nodes, pos = _parse_block(chunks, 0, stop=())
    if pos != len(chunks):
        raise TemplateError("unexpected {% endif %} / {% else %} without a matching {% if %}")
    return nodes


def _tag_keyword(tag):
    return tag.split(None, 1)[0] if tag else ""


def _parse_block(chunks, pos, stop):
    """Parse chunks into a node list until a tag whose keyword is in ``stop``.

    Returns ``(nodes, pos)`` with ``pos`` pointing AT the stopping tag (or the end).
    """
    nodes = []
    while pos < len(chunks):
        kind, payload = chunks[pos]
        if kind == "text":
            nodes.append(("text", payload))
            pos += 1
        elif kind == "expr":
            nodes.append(("expr", _parse_expr(payload)))
            pos += 1
        else:  # tag
            keyword = _tag_keyword(payload)
            if keyword in stop:
                return nodes, pos
            if keyword == "if":
                node, pos = _parse_if(chunks, pos)
                nodes.append(node)
            elif keyword in ("elif", "else", "endif"):
                raise TemplateError(f"unexpected {{% {keyword} %}} without a matching {{% if %}}")
            else:
                raise TemplateError(f"unknown tag {{% {payload} %}}")
    return nodes, pos


def _parse_if(chunks, pos):
    branches = []
    keyword = "if"
    while True:
        _kind, payload = chunks[pos]
        if keyword in ("if", "elif"):
            cond_src = payload.split(None, 1)
            if len(cond_src) < 2 or not cond_src[1].strip():
                raise TemplateError(f"{{% {keyword} %}} needs a condition")
            cond = _parse_expr(cond_src[1])
        else:  # else
            cond = None
        body, pos = _parse_block(chunks, pos + 1, stop=("elif", "else", "endif"))
        branches.append((cond, body))
        if pos >= len(chunks):
            raise TemplateError("missing {% endif %}")
        keyword = _tag_keyword(chunks[pos][1])
        if keyword == "endif":
            return ("if", branches), pos + 1
        if cond is None:
            raise TemplateError("{% else %} must be the last branch before {% endif %}")


# ----------------------------------------------------------------------------
# Template layer: renderer
# ----------------------------------------------------------------------------

def _render_nodes(nodes, context, out):
    for node in nodes:
        kind = node[0]
        if kind == "text":
            out.append(node[1])
        elif kind == "expr":
            out.append(_stringify(_eval(node[1], context)))
        else:  # if
            for cond, body in node[1]:
                if cond is None or _truthy(_eval(cond, context)):
                    _render_nodes(body, context, out)
                    break


def render(template_text, context):
    """Render ``template_text`` against ``context``; raise TemplateError on any
    parse/evaluation problem. ``context`` is a dict of namespaces (``row``,
    ``sender``, ``mail``)."""
    nodes = _parse_template(template_text or "")
    out = []
    _render_nodes(nodes, context, out)
    return "".join(out)


def validate(template_text):
    """Parse-check a template without rendering. Raises TemplateError if invalid."""
    _parse_template(template_text or "")


def validate_expr(text):
    """Parse-check a single expression without evaluating. Raises TemplateError."""
    _parse_expr(text)


# ----------------------------------------------------------------------------
# Introspection: which variables does a template read?
# ----------------------------------------------------------------------------

def variables(text, namespace, is_expression=False, conditions=True):
    """The distinct names ``text`` reads under ``namespace``, in first-seen order.

    ``variables("Ref {{ upper(row.ref) }} for {{ row.qty }}", "row")`` ->
    ``["ref", "qty"]``.

    This walks the parsed tree rather than regex-ing the source, so it sees only
    genuine reads: a name inside a string literal, or a different namespace, is not
    reported. An unparseable template yields ``[]`` rather than raising -- a
    half-typed template still needs its columns drawn.

    ``conditions=False`` skips names read *only* to choose a branch
    (``{% if row.uses_ftp %}``) and keeps those whose value is **printed** into the
    output (``{{ row.ref }}``). That distinction is what separates a variable Press
    must have from one it merely may have: a blank ``row.ref`` renders a hole in the
    draft, while a blank ``row.uses_ftp`` simply means "no". Press uses the full list
    for its columns and the printed-only list to decide what is missing.
    """
    try:
        nodes = ([("expr", _parse_expr(text))] if is_expression
                 else _parse_template(text or ""))
    except TemplateError:
        return []
    found = []
    _collect_nodes(nodes, namespace, found, conditions)
    return found


def _collect_nodes(nodes, namespace, found, conditions):
    for node in nodes:
        kind = node[0]
        if kind == "expr":
            _collect_expr(node[1], namespace, found)
        elif kind == "if":
            for cond, body in node[1]:
                if cond is not None and conditions:
                    _collect_expr(cond, namespace, found)
                _collect_nodes(body, namespace, found, conditions)
        # "text" reads nothing


def _collect_expr(node, namespace, found):
    kind = node[0]
    if kind == "var":
        parts = node[1]
        # Only a dotted read INTO the namespace names a variable: `row.ref` yes,
        # a bare `row` (or `sender.org`) no.
        if len(parts) == 2 and parts[0] == namespace and parts[1] not in found:
            found.append(parts[1])
    elif kind == "call":
        for arg in node[2]:
            _collect_expr(arg, namespace, found)
    elif kind == "bin":
        _collect_expr(node[2], namespace, found)
        _collect_expr(node[3], namespace, found)
    elif kind == "not":
        _collect_expr(node[1], namespace, found)
    # "lit" reads nothing
