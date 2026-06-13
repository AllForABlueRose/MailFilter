"""A small boolean keyword-expression language for the search filters.

Grammar (used by every text search field — main, optional, exclude, sender,
recipient; the datetime range is unaffected):

    expr    := term (op term)*               # evaluated strictly LEFT TO RIGHT
    op      := ',' (OR) | ';' (AND)
    term    := literal | regex | '[' expr ']'   # [ ] groups are evaluated first
    regex   := '<{(' ... ')}>'               # inner text is a raw regex
    literal := any run of characters between operators / brackets / regex

There is **no operator precedence**: `a, b; c` means `(a OR b) AND c`. Use
`[ ]` to group differently: `a; [b, c]` means `a AND (b OR c)`.

Regex blocks are recognized FIRST, so `,`, `;`, `[`, `]` inside a `<{( ... )}>`
block are part of the pattern, never operators. Those four characters are
otherwise reserved; to match one literally, put it in a regex (e.g. `<{(;)}>`).

Matching is case-insensitive: literals are lowercased substring tests and
regexes are compiled with re.IGNORECASE. Both run against the (already
lowercased) derived search text for the field.

Node shapes returned by :func:`parse`:
    ('lit', lowered_text)
    ('re', compiled_pattern, source)
    ('seq', [node, ...], [op, ...])   # len(ops) == len(nodes) - 1; op in OR/AND
"""

import re

_RE_OPEN = "<{("
_RE_CLOSE = ")}>"


class ExprError(ValueError):
    """A search expression that could not be parsed (surfaced to the user)."""


def parse(text):
    """Parse a field's expression into a node, or ``None`` if it is blank.

    Raises :class:`ExprError` on malformed input (bad brackets, dangling
    operators, an unterminated or invalid regex).
    """
    tokens = _tokenize(text or "")
    if not tokens:
        return None
    node, pos = _parse_seq(tokens, 0, top=True)
    if pos != len(tokens):  # a ']' with no matching '['
        raise ExprError("unexpected ']'")
    return node


def evaluate(node, text):
    """True if ``node`` matches ``text`` (the lowercased search blob)."""
    kind = node[0]
    if kind == "lit":
        return node[1] in text
    if kind == "re":
        return node[1].search(text) is not None
    nodes, ops = node[1], node[2]
    value = evaluate(nodes[0], text)
    for op, nxt in zip(ops, nodes[1:]):
        rhs = evaluate(nxt, text)
        value = (value or rhs) if op == "OR" else (value and rhs)
    return value


def operands(node):
    """Every literal/regex leaf under ``node`` (used for highlighting)."""
    if node is None:
        return []
    if node[0] in ("lit", "re"):
        return [node]
    out = []
    for child in node[1]:
        out.extend(operands(child))
    return out


# ----- tokenizer -----
# Tokens: ('lit', lowered) | ('re', compiled, source) | ('op', OR/AND)
#         | ('lb',) | ('rb',)

def _tokenize(text):
    tokens = []
    buf = []

    def flush():
        if buf:
            literal = "".join(buf).strip()
            if literal:
                tokens.append(("lit", literal.lower()))
            buf.clear()

    i, n = 0, len(text)
    while i < n:
        if text.startswith(_RE_OPEN, i):
            flush()
            close = text.find(_RE_CLOSE, i + len(_RE_OPEN))
            if close == -1:
                raise ExprError("unterminated regex (missing ')}>')")
            source = text[i + len(_RE_OPEN):close]
            try:
                compiled = re.compile(source, re.IGNORECASE)
            except re.error as e:
                raise ExprError(f"invalid regex '{source}': {e}") from e
            tokens.append(("re", compiled, source))
            i = close + len(_RE_CLOSE)
        elif text[i] == ",":
            flush()
            tokens.append(("op", "OR"))
            i += 1
        elif text[i] == ";":
            flush()
            tokens.append(("op", "AND"))
            i += 1
        elif text[i] == "[":
            flush()
            tokens.append(("lb",))
            i += 1
        elif text[i] == "]":
            flush()
            tokens.append(("rb",))
            i += 1
        else:
            buf.append(text[i])
            i += 1
    flush()
    return tokens


# ----- recursive-descent parser -----

def _parse_seq(tokens, pos, top):
    nodes, ops = [], []
    expect_operand = True
    while pos < len(tokens):
        kind = tokens[pos][0]
        if kind == "rb":
            if top:
                raise ExprError("unexpected ']'")
            if expect_operand:
                raise ExprError("empty group or dangling operator before ']'")
            return ("seq", nodes, ops), pos + 1
        if expect_operand:
            if kind == "lb":
                node, pos = _parse_seq(tokens, pos + 1, top=False)
                nodes.append(node)
            elif kind == "lit":
                nodes.append(("lit", tokens[pos][1]))
                pos += 1
            elif kind == "re":
                nodes.append(("re", tokens[pos][1], tokens[pos][2]))
                pos += 1
            else:  # an operator where a term was expected
                raise ExprError("operator with no preceding term")
            expect_operand = False
        else:
            if kind == "op":
                ops.append(tokens[pos][1])
                pos += 1
                expect_operand = True
            else:  # two terms with no operator between them
                raise ExprError("missing operator between terms")
    if not top:
        raise ExprError("unterminated group (missing ']')")
    if expect_operand:
        raise ExprError("expression ends with an operator")
    return ("seq", nodes, ops), pos
