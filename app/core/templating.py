"""Template engine on the standard library.

Reads the {{ }} / {% %} syntax the app's HTML templates use:
  output        {{ expr }}  (HTML-escaped unless the value is Markup)
  statements    if / elif / else, for (else, loop.*, inline if), set, with, include,
                extends / block, macro, from ... import ..., raw
  expressions   Python-like: literals, lists, dicts, tuples, attribute / item access,
                calls with keyword and **args, maths, comparisons, `in`, `not`, and/or,
                `a if b else c`, `~` (join as text), filters `x|name(args)`, tests `x is none`
  whitespace    {%- -%} {{- -}} trim the text next to them
Each expression is compiled once into a Python function; templates are cached and
reloaded when the file changes.
"""
from __future__ import annotations

import ast
import json
import math
import os
import re
import threading
from collections import namedtuple

from .markup import Markup, escape, soft_str


class TemplateError(Exception):
    pass


class TemplateNotFound(TemplateError):
    pass


class TemplateSyntaxError(TemplateError):
    def __init__(self, message, name=None, lineno=None):
        where = f" ({name}, line {lineno})" if name else ""
        super().__init__(message + where)


# ------------------------------------------------------------- undefined --

class Undefined:
    """A missing value: prints as nothing, is falsy, iterates as empty."""
    __slots__ = ("_name",)

    def __init__(self, name=""):
        self._name = name

    def __str__(self):
        return ""

    def __html__(self):
        return ""

    def __bool__(self):
        return False

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __contains__(self, item):
        return False

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return Undefined(name)

    def __getitem__(self, key):
        return Undefined(str(key))

    def __call__(self, *args, **kwargs):
        return Undefined(self._name)

    def __eq__(self, other):
        return isinstance(other, Undefined)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return 0

    def __repr__(self):
        return "Undefined"


UNDEF = Undefined()


def is_undefined(value) -> bool:
    return isinstance(value, Undefined)


# ------------------------------------------------------------- run-time helpers --

def getattr_(obj, name):
    """obj.name: attribute first, then item (like the original engine)."""
    if isinstance(obj, Undefined):
        return Undefined(name)
    try:
        return getattr(obj, name)
    except AttributeError:
        pass
    except Exception:  # noqa: BLE001 - a property that fails is treated as missing
        return Undefined(name)
    try:
        return obj[name]
    except (TypeError, LookupError, AttributeError):
        return Undefined(name)


def getitem_(obj, key):
    """obj[key]: item first, then attribute for text keys."""
    if isinstance(obj, Undefined):
        return Undefined(str(key))
    try:
        return obj[key]
    except (AttributeError, LookupError, TypeError):
        if isinstance(key, str):
            try:
                return getattr(obj, key)
            except AttributeError:
                pass
    return Undefined(str(key))


def concat_(*parts):
    return "".join(soft_str(p) if not isinstance(p, Undefined) else "" for p in parts)


class LoopContext:
    def __init__(self, items):
        self._items = items
        self.length = len(items)
        self.index0 = -1

    @property
    def index(self):
        return self.index0 + 1

    @property
    def revindex(self):
        return self.length - self.index0

    @property
    def revindex0(self):
        return self.length - self.index0 - 1

    @property
    def first(self):
        return self.index0 == 0

    @property
    def last(self):
        return self.index0 == self.length - 1

    @property
    def previtem(self):
        return self._items[self.index0 - 1] if self.index0 > 0 else UNDEF

    @property
    def nextitem(self):
        return self._items[self.index0 + 1] if self.index0 + 1 < self.length else UNDEF

    def cycle(self, *values):
        return values[self.index0 % len(values)] if values else UNDEF


# ------------------------------------------------------------------ filters --

def _f_default(value, default="", boolean=False):
    if isinstance(value, Undefined) or (boolean and not value):
        return default
    return value


def _f_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _f_int(value, default=0, base=10):
    try:
        if isinstance(value, str):
            return int(value, base)
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError, OverflowError):
            return default


def _f_round(value, precision=0, method="common"):
    if method == "ceil":
        return math.ceil(value * 10 ** precision) / 10 ** precision
    if method == "floor":
        return math.floor(value * 10 ** precision) / 10 ** precision
    return round(value, precision)


def _attr_getter(attribute):
    parts = [int(p) if p.isdigit() else p for p in str(attribute).split(".")]

    def get(item):
        for p in parts:
            item = getitem_(item, p)
        return item
    return get


def _f_sum(iterable, attribute=None, start=0):
    if attribute is not None:
        get = _attr_getter(attribute)
        iterable = (get(i) for i in iterable)
    return sum(iterable, start)


def _f_format(value, *args, **kwargs):
    if args and kwargs:
        raise TemplateError("format accepts either positional or keyword arguments, not both")
    return soft_str(value) % (kwargs or args)


_word_re = re.compile(r"([-\s({\[<]+)")


def _f_title(value):
    return "".join(item[0].upper() + item[1:].lower() for item in _word_re.split(soft_str(value)) if item)


def _f_select(iterable, test_name=None, *args):
    if test_name is None:
        return (i for i in iterable if i)
    test = TESTS[test_name]
    return (i for i in iterable if test(i, *args))


def _f_reject(iterable, test_name=None, *args):
    if test_name is None:
        return (i for i in iterable if not i)
    test = TESTS[test_name]
    return (i for i in iterable if not test(i, *args))


def _f_selectattr(iterable, attribute, test_name=None, *args):
    get = _attr_getter(attribute)
    if test_name is None:
        return (i for i in iterable if get(i))
    test = TESTS[test_name]
    return (i for i in iterable if test(get(i), *args))


def _f_rejectattr(iterable, attribute, test_name=None, *args):
    get = _attr_getter(attribute)
    if test_name is None:
        return (i for i in iterable if not get(i))
    test = TESTS[test_name]
    return (i for i in iterable if not test(get(i), *args))


def _f_map(iterable, attribute=None, default=None):
    get = _attr_getter(attribute)
    for i in iterable:
        v = get(i)
        yield default if (default is not None and isinstance(v, Undefined)) else v


def _f_join(value, d="", attribute=None):
    if attribute is not None:
        value = map(_attr_getter(attribute), value)
    items = list(value)
    if any(hasattr(i, "__html__") for i in items):
        return Markup(d).join(items)
    return soft_str(d).join(soft_str(i) for i in items)


def _f_truncate(s, length=255, killwords=False, end="...", leeway=5):
    s = soft_str(s)
    if len(s) <= length + leeway:
        return s
    if killwords:
        return s[: length - len(end)] + end
    result = s[: length - len(end)].rsplit(" ", 1)[0]
    return result + end


def _f_tojson(value, indent=None):
    from .http import json_default
    text = json.dumps(value, default=json_default, ensure_ascii=True, sort_keys=True, indent=indent)
    return Markup(text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("'", "\\u0027"))


def _f_first(seq):
    for i in seq:
        return i
    return UNDEF


def _f_last(seq):
    try:
        return list(seq)[-1]
    except IndexError:
        return UNDEF


def _f_length(value):
    try:
        return len(value)
    except TypeError:
        return len(list(value))


FILTERS = {
    "abs": abs,
    "capitalize": lambda s: soft_str(s).capitalize(),
    "count": _f_length,
    "d": _f_default,
    "default": _f_default,
    "dictsort": lambda d, case_sensitive=False, by="key": sorted(d.items(), key=lambda kv: kv[0 if by == "key" else 1]),
    "e": escape,
    "escape": escape,
    "first": _f_first,
    "float": _f_float,
    "format": _f_format,
    "int": _f_int,
    "join": _f_join,
    "last": _f_last,
    "length": _f_length,
    "list": lambda v: list(v),
    "lower": lambda s: soft_str(s).lower(),
    "map": _f_map,
    "max": lambda v, **k: max(v),
    "min": lambda v, **k: min(v),
    "reject": _f_reject,
    "rejectattr": _f_rejectattr,
    "replace": lambda s, old, new, count=None: soft_str(s).replace(old, new, -1 if count is None else count),
    "reverse": lambda v: v[::-1] if isinstance(v, str) else list(reversed(list(v))),
    "round": _f_round,
    "safe": lambda v: Markup(v),
    "select": _f_select,
    "selectattr": _f_selectattr,
    "sort": lambda v, reverse=False, attribute=None: sorted(v, key=_attr_getter(attribute) if attribute else None, reverse=reverse),
    "string": soft_str,
    "striptags": lambda s: re.sub(r"<[^>]*>", "", soft_str(s)),
    "sum": _f_sum,
    "title": _f_title,
    "tojson": _f_tojson,
    "trim": lambda s, chars=None: soft_str(s).strip(chars),
    "truncate": _f_truncate,
    "unique": lambda v: list(dict.fromkeys(v)),
    "upper": lambda s: soft_str(s).upper(),
    "urlencode": lambda s: __import__("urllib.parse").parse.quote(soft_str(s)),
    "wordcount": lambda s: len(soft_str(s).split()),
}

TESTS = {
    "boolean": lambda v: v is True or v is False,
    "defined": lambda v: not isinstance(v, Undefined),
    "divisibleby": lambda v, n: v % n == 0,
    "eq": lambda v, o: v == o,
    "equalto": lambda v, o: v == o,
    "escaped": lambda v: hasattr(v, "__html__"),
    "even": lambda v: v % 2 == 0,
    "false": lambda v: v is False,
    "float": lambda v: isinstance(v, float),
    "ge": lambda v, o: v >= o,
    "gt": lambda v, o: v > o,
    "in": lambda v, seq: v in seq,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "iterable": lambda v: hasattr(v, "__iter__"),
    "le": lambda v, o: v <= o,
    "lower": lambda v: soft_str(v).islower(),
    "lt": lambda v, o: v < o,
    "mapping": lambda v: isinstance(v, dict),
    "ne": lambda v, o: v != o,
    "none": lambda v: v is None,
    "number": lambda v: isinstance(v, (int, float, complex)) and not isinstance(v, bool) or type(v).__name__ == "Decimal",
    "odd": lambda v: v % 2 == 1,
    "sameas": lambda v, o: v is o,
    "sequence": lambda v: hasattr(v, "__len__") and hasattr(v, "__getitem__"),
    "string": lambda v: isinstance(v, str),
    "true": lambda v: v is True,
    "undefined": lambda v: isinstance(v, Undefined),
    "upper": lambda v: soft_str(v).isupper(),
}


# ----------------------------------------------------------- expressions --

_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<number>\d[\d_]*\.\d[\d_]*(?:[eE][+-]?\d+)?|\d[\d_]*(?:[eE][+-]?\d+)?)
  | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
  | (?P<op>\*\*|//|==|!=|<=|>=|[-+*/%~|.,:()\[\]{}<>=])
""", re.VERBOSE | re.DOTALL)

Tok = namedtuple("Tok", "kind value")
KEYWORDS_CONST = {"true": "True", "false": "False", "none": "None", "True": "True", "False": "False", "None": "None"}


def tokenize_expr(src: str):
    pos, out = 0, []
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m:
            raise TemplateSyntaxError(f"Unexpected character {src[pos]!r} in expression {src!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        out.append(Tok(kind, m.group(kind)))
    out.append(Tok("end", None))
    return out


class ExprParser:
    """Turns template expression syntax into a Python expression string."""

    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    # -- token helpers
    @property
    def cur(self):
        return self.toks[self.i]

    def peek(self, n=1):
        j = self.i + n
        return self.toks[j] if j < len(self.toks) else Tok("end", None)

    def next(self):
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def at(self, kind, value=None):
        tok = self.cur
        return tok.kind == kind and (value is None or tok.value == value)

    def at_name(self, value):
        return self.cur.kind == "name" and self.cur.value == value

    def expect(self, kind, value=None):
        if not self.at(kind, value):
            raise TemplateSyntaxError(f"Expected {value or kind}, found {self.cur.value!r}")
        return self.next()

    # -- grammar
    def parse_expression(self, with_condexpr=True):
        expr = self.parse_or()
        if with_condexpr:
            while self.at_name("if"):
                self.next()
                cond = self.parse_or()
                if self.at_name("else"):
                    self.next()
                    other = self.parse_expression()
                else:
                    other = "_U"
                expr = f"(({expr}) if ({cond}) else ({other}))"
        return expr

    def parse_or(self):
        left = self.parse_and()
        while self.at_name("or"):
            self.next()
            left = f"({left} or {self.parse_and()})"
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.at_name("and"):
            self.next()
            left = f"({left} and {self.parse_not()})"
        return left

    def parse_not(self):
        if self.at_name("not"):
            self.next()
            return f"(not {self.parse_not()})"
        return self.parse_compare()

    _CMP = {"==", "!=", "<", ">", "<=", ">="}

    def parse_compare(self):
        parts = [self.parse_math1()]
        while True:
            if self.at("op") and self.cur.value in self._CMP:
                op = self.next().value
                parts.append(op)
                parts.append(self.parse_math1())
            elif self.at_name("in"):
                self.next()
                parts.append("in")
                parts.append(self.parse_math1())
            elif self.at_name("not") and self.peek().kind == "name" and self.peek().value == "in":
                self.next()
                self.next()
                parts.append("not in")
                parts.append(self.parse_math1())
            else:
                break
        return parts[0] if len(parts) == 1 else "(" + " ".join(parts) + ")"

    def parse_math1(self):
        left = self.parse_concat()
        while self.at("op") and self.cur.value in ("+", "-"):
            op = self.next().value
            left = f"({left} {op} {self.parse_concat()})"
        return left

    def parse_concat(self):
        parts = [self.parse_math2()]
        while self.at("op", "~"):
            self.next()
            parts.append(self.parse_math2())
        return parts[0] if len(parts) == 1 else f"_cat({', '.join(parts)})"

    def parse_math2(self):
        left = self.parse_pow()
        while self.at("op") and self.cur.value in ("*", "/", "//", "%"):
            op = self.next().value
            left = f"({left} {op} {self.parse_pow()})"
        return left

    def parse_pow(self):
        left = self.parse_unary()
        while self.at("op", "**"):
            self.next()
            left = f"({left} ** {self.parse_unary()})"
        return left

    def parse_unary(self, with_filter=True):
        if self.at("op", "-"):
            self.next()
            node = f"(-{self.parse_unary(False)})"
        elif self.at("op", "+"):
            self.next()
            node = f"(+{self.parse_unary(False)})"
        else:
            node = self.parse_postfix(self.parse_primary())
        if with_filter:
            node = self.parse_filter_expr(node)
        return node

    def parse_primary(self):
        tok = self.cur
        if tok.kind == "name":
            self.next()
            if tok.value in KEYWORDS_CONST:
                return KEYWORDS_CONST[tok.value]
            return f"_s.lookup({tok.value!r})"
        if tok.kind == "string":
            value = ""
            while self.at("string"):
                value += ast.literal_eval(self.next().value)
            return repr(value)
        if tok.kind == "number":
            return self.next().value.replace("_", "")
        if self.at("op", "("):
            self.next()
            if self.at("op", ")"):
                self.next()
                return "()"
            first = self.parse_expression()
            if self.at("op", ","):
                items = [first]
                while self.at("op", ","):
                    self.next()
                    if self.at("op", ")"):
                        break
                    items.append(self.parse_expression())
                self.expect("op", ")")
                return "(" + ", ".join(items) + ",)"
            self.expect("op", ")")
            return f"({first})"
        if self.at("op", "["):
            self.next()
            items = []
            while not self.at("op", "]"):
                items.append(self.parse_expression())
                if not self.at("op", ","):
                    break
                self.next()
            self.expect("op", "]")
            return "[" + ", ".join(items) + "]"
        if self.at("op", "{"):
            self.next()
            items = []
            while not self.at("op", "}"):
                key = self.parse_expression()
                self.expect("op", ":")
                items.append(f"{key}: {self.parse_expression()}")
                if not self.at("op", ","):
                    break
                self.next()
            self.expect("op", "}")
            return "{" + ", ".join(items) + "}"
        raise TemplateSyntaxError(f"Unexpected {tok.value!r} in expression")

    def parse_postfix(self, node):
        while True:
            if self.at("op", "."):
                self.next()
                tok = self.next()
                if tok.kind == "name":
                    node = f"_ga({node}, {tok.value!r})"
                elif tok.kind == "number":
                    node = f"_gi({node}, {tok.value})"
                else:
                    raise TemplateSyntaxError("Expected a name after '.'")
            elif self.at("op", "["):
                self.next()
                node = f"_gi({node}, {self.parse_subscript()})"
                self.expect("op", "]")
            elif self.at("op", "("):
                node = f"{node}({self.parse_call_args()})"
            else:
                return node

    def parse_subscript(self):
        parts = [None, None, None]
        idx = 0
        if not self.at("op", ":"):
            parts[0] = self.parse_expression()
            if not self.at("op", ":"):
                return parts[0]
        while self.at("op", ":") and idx < 2:
            self.next()
            idx += 1
            if not (self.at("op", "]") or self.at("op", ":")):
                parts[idx] = self.parse_expression()
        return "slice(" + ", ".join(p if p is not None else "None" for p in parts) + ")"

    def parse_call_args(self):
        self.expect("op", "(")
        args = []
        while not self.at("op", ")"):
            if self.at("op", "**"):
                self.next()
                args.append("**" + self.parse_expression())
            elif self.at("op", "*"):
                self.next()
                args.append("*" + self.parse_expression())
            elif self.cur.kind == "name" and self.peek().kind == "op" and self.peek().value == "=":
                name = self.next().value
                self.next()
                args.append(f"{name}={self.parse_expression()}")
            else:
                args.append(self.parse_expression())
            if not self.at("op", ","):
                break
            self.next()
        self.expect("op", ")")
        return ", ".join(args)

    def parse_filter_expr(self, node):
        while True:
            if self.at("op", "|"):
                self.next()
                name = self.expect("name").value
                while self.at("op", "."):
                    self.next()
                    name += "." + self.expect("name").value
                args = self.parse_call_args() if self.at("op", "(") else ""
                node = f"_flt({name!r})({node}{', ' + args if args else ''})"
            elif self.at_name("is"):
                self.next()
                negate = False
                if self.at_name("not"):
                    self.next()
                    negate = True
                name = self.expect("name").value
                if name in KEYWORDS_CONST:      # `is none` / `is true` / `is false`
                    name = name.lower()
                args = ""
                if self.at("op", "("):
                    args = self.parse_call_args()
                elif (self.cur.kind in ("name", "number", "string")
                      and not (self.cur.kind == "name" and self.cur.value in ("and", "or", "else", "if", "in", "not", "is"))):
                    args = self.parse_unary(False)
                call = f"_tst({name!r})({node}{', ' + args if args else ''})"
                node = f"(not {call})" if negate else call
            else:
                return node


class Expr:
    """A compiled expression: call with a scope to get its value."""
    __slots__ = ("source", "fn")

    def __init__(self, source: str, env: "Environment"):
        self.source = source
        parser = ExprParser(tokenize_expr(source))
        code = parser.parse_expression()
        if parser.at("op", ","):          # a, b, c  -> tuple (e.g. {% set x, y = a, b %})
            items = [code]
            while parser.at("op", ","):
                parser.next()
                if parser.at("end"):
                    break
                items.append(parser.parse_expression())
            code = "(" + ", ".join(items) + ",)"
        if not parser.at("end"):
            raise TemplateSyntaxError(f"Unexpected {parser.cur.value!r} at the end of {source!r}")
        self.fn = env.compile_code(code)

    def __call__(self, scope):
        return self.fn(scope)


# --------------------------------------------------------------- scope --

class Scope:
    __slots__ = ("vars", "parent")

    def __init__(self, parent=None, values=None):
        self.parent = parent
        self.vars = dict(values) if values else {}

    def lookup(self, name):
        s = self
        while s is not None:
            if name in s.vars:
                return s.vars[name]
            s = s.parent
        return Undefined(name)

    def set(self, name, value):
        self.vars[name] = value

    def child(self, values=None):
        return Scope(self, values)

    def flat(self) -> dict:
        chain = []
        s = self
        while s is not None:
            chain.append(s.vars)
            s = s.parent
        out = {}
        for d in reversed(chain):
            out.update(d)
        return out


# --------------------------------------------------------------- parsing templates --

class Node:
    __slots__ = ()


class Text(Node):
    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text


class Output(Node):
    __slots__ = ("expr",)

    def __init__(self, expr):
        self.expr = expr


class If(Node):
    __slots__ = ("branches", "else_")

    def __init__(self, branches, else_):
        self.branches = branches
        self.else_ = else_


class For(Node):
    __slots__ = ("targets", "iter", "cond", "body", "else_")

    def __init__(self, targets, iter_, cond, body, else_):
        self.targets, self.iter, self.cond, self.body, self.else_ = targets, iter_, cond, body, else_


class Set(Node):
    __slots__ = ("targets", "expr", "attr")

    def __init__(self, targets, expr, attr=None):
        self.targets, self.expr, self.attr = targets, expr, attr


class With(Node):
    __slots__ = ("assigns", "body")

    def __init__(self, assigns, body):
        self.assigns, self.body = assigns, body


class Include(Node):
    __slots__ = ("name", "ignore_missing")

    def __init__(self, name, ignore_missing):
        self.name, self.ignore_missing = name, ignore_missing


class Block(Node):
    __slots__ = ("name", "body")

    def __init__(self, name, body):
        self.name, self.body = name, body


class Macro(Node):
    __slots__ = ("name", "params", "body")

    def __init__(self, name, params, body):
        self.name, self.params, self.body = name, params, body


class FromImport(Node):
    __slots__ = ("template", "names")

    def __init__(self, template, names):
        self.template, self.names = template, names


class ImportAs(Node):
    __slots__ = ("template", "alias")

    def __init__(self, template, alias):
        self.template, self.alias = template, alias


class Extends(Node):
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name


_OPEN_RE = re.compile(r"\{\{|\{%|\{#")
_END_FOR = {"{{": "}}", "{%": "%}", "{#": "#}"}


def lex(source: str):
    """Splits the source into ('text', s) / ('var'|'stmt', inner, lineno) pieces, applying - trimming."""
    pieces = []
    pos, line = 0, 1
    trim_next = False
    n = len(source)
    while pos < n:
        m = _OPEN_RE.search(source, pos)
        if not m:
            text = source[pos:]
            if trim_next:
                text = text.lstrip()
            pieces.append(("text", text, line))
            break
        text = source[pos:m.start()]
        start = m.end()
        opener = m.group(0)
        trim_before = start < n and source[start] == "-"
        if trim_before:
            start += 1
        if trim_next:
            text = text.lstrip()
        if trim_before:
            text = text.rstrip()
        if text:
            pieces.append(("text", text, line))
        line += source.count("\n", pos, m.start())
        end_marker = _END_FOR[opener]
        # find the end marker, skipping quoted strings inside the tag
        i, quote = start, None
        while i < n:
            ch = source[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in "'\"" and opener != "{#":
                quote = ch
            elif source.startswith(end_marker, i):
                break
            elif ch == "-" and source.startswith(end_marker, i + 1):
                break
            i += 1
        else:
            raise TemplateSyntaxError(f"Missing {end_marker!r}", lineno=line)
        trim_after = source[i] == "-"
        inner_end = i
        end = i + (1 if trim_after else 0) + len(end_marker)
        inner = source[start:inner_end].strip()
        if opener == "{{":
            pieces.append(("var", inner, line))
        elif opener == "{%":
            pieces.append(("stmt", inner, line))
            if re.fullmatch(r"raw", inner):
                close = re.compile(r"\{%-?\s*endraw\s*-?%\}").search(source, end)
                if not close:
                    raise TemplateSyntaxError("Missing endraw", lineno=line)
                pieces.append(("text", source[end:close.start()], line))
                pieces[-2] = ("text", "", line)
                end = close.end()
        line += source.count("\n", m.start(), end)
        trim_next = trim_after
        pos = end
    return pieces


class Parser:
    def __init__(self, env, source, name):
        self.env = env
        self.name = name
        self.pieces = lex(source)
        self.i = 0
        self.blocks = {}
        self.extends = None

    def expr(self, src, lineno):
        try:
            return Expr(src, self.env)
        except TemplateSyntaxError as exc:
            raise TemplateSyntaxError(str(exc), self.name, lineno) from None

    def parse(self):
        body = self.subparse(())
        return body

    def subparse(self, end_words):
        nodes = []
        while self.i < len(self.pieces):
            kind, value, lineno = self.pieces[self.i]
            if kind == "text":
                self.i += 1
                if value:
                    nodes.append(Text(value))
                continue
            if kind == "var":
                self.i += 1
                nodes.append(Output(self.expr(value, lineno)))
                continue
            word = value.split(None, 1)[0] if value else ""
            if word in end_words:
                return nodes
            self.i += 1
            nodes.append(self.statement(word, value[len(word):].strip(), lineno))
        if end_words:
            raise TemplateSyntaxError(f"Missing {' or '.join(end_words)}", self.name)
        return nodes

    def _end(self, word):
        kind, value, lineno = self.pieces[self.i]
        self.i += 1
        return value

    def statement(self, word, rest, lineno):
        if word == "if":
            branches = [(self.expr(rest, lineno), self.subparse(("elif", "else", "endif")))]
            else_ = None
            while True:
                stmt = self._end(None)
                w = stmt.split(None, 1)[0]
                if w == "elif":
                    branches.append((self.expr(stmt[4:].strip(), lineno), self.subparse(("elif", "else", "endif"))))
                elif w == "else":
                    else_ = self.subparse(("endif",))
                else:
                    break
            return If(branches, else_)
        if word == "for":
            m = re.match(r"(.+?)\s+in\s+(.+)$", rest, re.S)
            if not m:
                raise TemplateSyntaxError(f"Bad for loop: {rest!r}", self.name, lineno)
            targets = _parse_targets(m.group(1))
            iter_src, cond_src = m.group(2), None
            # `for x in items if cond` -> loop filter (only when the `if` is not inside brackets/strings)
            toks = tokenize_expr(iter_src)
            depth = 0
            pos = 0
            for t in toks:
                if t.kind == "op" and t.value in "([{":
                    depth += 1
                elif t.kind == "op" and t.value in ")]}":
                    depth -= 1
                elif t.kind == "name" and t.value == "if" and depth == 0:
                    found = re.search(r"\bif\b", iter_src[pos:])
                    split_at = pos + found.start()
                    cond_src = iter_src[split_at + 2:].strip()
                    iter_src = iter_src[:split_at].strip()
                    break
                if t.value:
                    idx = iter_src.find(t.value, pos)
                    pos = idx + len(t.value) if idx >= 0 else pos
            iter_src = re.sub(r"\s+recursive$", "", iter_src)
            body = self.subparse(("else", "endfor"))
            else_ = None
            if self._end(None).split(None, 1)[0] == "else":
                else_ = self.subparse(("endfor",))
                self._end(None)
            return For(targets, self.expr(iter_src, lineno), self.expr(cond_src, lineno) if cond_src else None, body, else_)
        if word == "set":
            m = re.match(r"([\w\s,.]+?)\s*=\s*(.+)$", rest, re.S)
            if not m:   # block set: {% set x %}...{% endset %}
                body = self.subparse(("endset",))
                self._end(None)
                return Set([rest.strip()], ("block", body))
            target = m.group(1).strip()
            if "." in target:
                obj, attr = target.split(".", 1)
                return Set([obj.strip()], self.expr(m.group(2), lineno), attr=attr.strip())
            return Set([t.strip() for t in target.split(",")], self.expr(m.group(2), lineno))
        if word == "with":
            assigns = []
            if rest:
                for name, src in _split_assignments(rest):
                    assigns.append((name, self.expr(src, lineno)))
            body = self.subparse(("endwith",))
            self._end(None)
            return With(assigns, body)
        if word == "include":
            ignore = False
            src = re.sub(r"\s+(with|without)\s+context\s*$", "", rest)
            if re.search(r"\s+ignore\s+missing\s*$", src):
                ignore = True
                src = re.sub(r"\s+ignore\s+missing\s*$", "", src)
            return Include(self.expr(src, lineno), ignore)
        if word == "extends":
            self.extends = self.expr(rest, lineno)
            return Extends(self.extends)
        if word == "block":
            name = rest.split()[0]
            body = self.subparse(("endblock",))
            self._end(None)
            block = Block(name, body)
            self.blocks[name] = block
            return block
        if word == "macro":
            m = re.match(r"(\w+)\s*\((.*)\)\s*$", rest, re.S)
            if not m:
                raise TemplateSyntaxError(f"Bad macro: {rest!r}", self.name, lineno)
            params = []
            for name, src in _split_params(m.group(2)):
                params.append((name, self.expr(src, lineno) if src is not None else None))
            body = self.subparse(("endmacro",))
            self._end(None)
            return Macro(m.group(1), params, body)
        if word == "from":
            m = re.match(r"(.+?)\s+import\s+(.+?)(?:\s+(?:with|without)\s+context)?\s*$", rest, re.S)
            if not m:
                raise TemplateSyntaxError(f"Bad import: {rest!r}", self.name, lineno)
            names = []
            for part in m.group(2).split(","):
                bits = part.split()
                if not bits:
                    continue
                names.append((bits[0], bits[2] if len(bits) == 3 and bits[1] == "as" else bits[0]))
            return FromImport(self.expr(m.group(1), lineno), names)
        if word == "import":
            m = re.match(r"(.+?)\s+as\s+(\w+)(?:\s+(?:with|without)\s+context)?\s*$", rest, re.S)
            if not m:
                raise TemplateSyntaxError(f"Bad import: {rest!r}", self.name, lineno)
            return ImportAs(self.expr(m.group(1), lineno), m.group(2))
        raise TemplateSyntaxError(f"Unknown tag {word!r}", self.name, lineno)


def _split_top_level(src: str):
    """Splits on commas that are not inside brackets or strings."""
    parts, depth, quote, buf = [], 0, None, ""
    i = 0
    while i < len(src):
        ch = src[i]
        if quote:
            buf += ch
            if ch == "\\" and i + 1 < len(src):
                buf += src[i + 1]
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            buf += ch
        elif ch in "([{":
            depth += 1
            buf += ch
        elif ch in ")]}":
            depth -= 1
            buf += ch
        elif ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
        i += 1
    if buf.strip():
        parts.append(buf)
    return parts


def _parse_targets(src: str):
    """'a, (b, c)' -> ['a', ['b', 'c']] (loop variables, possibly nested)."""
    src = src.strip()
    if src.startswith("(") and src.endswith(")"):
        depth = 0
        for i, ch in enumerate(src):
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0 and i < len(src) - 1:
                break
        else:
            src = src[1:-1]
    out = []
    for part in _split_top_level(src):
        part = part.strip()
        if part.startswith("("):
            out.append(_parse_targets(part))
        elif part:
            out.append(part)
    return out


def _split_assignments(src):
    out = []
    for part in _split_top_level(src):
        name, _, value = part.partition("=")
        out.append((name.strip(), value.strip()))
    return out


def _split_params(src):
    out = []
    for part in _split_top_level(src):
        if "=" in part:
            name, _, value = part.partition("=")
            out.append((name.strip(), value.strip()))
        elif part.strip():
            out.append((part.strip(), None))
    return out


# --------------------------------------------------------------- rendering --

class Template:
    def __init__(self, env, source, name):
        self.env = env
        self.name = name
        if source.endswith("\n"):          # like keep_trailing_newline=False
            source = source[:-1]
        parser = Parser(env, source, name)
        self.body = parser.parse()
        self.blocks = parser.blocks
        self.extends = parser.extends

    def render(self, context: dict | None = None, **kwargs) -> str:
        values = dict(self.env.globals)
        values.update(context or {})
        values.update(kwargs)
        scope = Scope(None, values)
        out = []
        blocks = {}
        scope.set("self", _SelfRef(self.env, blocks, scope))
        self.env.render_template(self, scope, out, blocks)
        return "".join(out)


class Environment:
    def __init__(self, loader_dirs, autoescape=True, auto_reload=True):
        self.dirs = [str(d) for d in (loader_dirs if isinstance(loader_dirs, (list, tuple)) else [loader_dirs])]
        self.autoescape = autoescape
        self.auto_reload = auto_reload
        self.filters = dict(FILTERS)
        self.tests = dict(TESTS)
        self.globals = {"range": range, "dict": dict, "lipsum": lambda *a, **k: "", "true": True, "false": False, "none": None}
        self._cache = {}
        self._lock = threading.Lock()
        self._helpers = {
            "_ga": getattr_, "_gi": getitem_, "_cat": concat_, "_U": UNDEF,
            "_flt": self._filter, "_tst": self._test, "slice": slice,
        }

    def _filter(self, name):
        try:
            return self.filters[name]
        except KeyError:
            raise TemplateError(f"No filter named {name!r}") from None

    def _test(self, name):
        try:
            return self.tests[name]
        except KeyError:
            raise TemplateError(f"No test named {name!r}") from None

    def compile_code(self, code: str):
        """Compiles generated Python code (names are read from the scope argument `_s`)."""
        return eval(compile(f"lambda _s: {code}", "<template>", "eval"), dict(self._helpers))  # noqa: S307

    # -- loading
    def _find(self, name):
        name = name.replace("\\", "/")
        if ".." in name.split("/"):
            raise TemplateNotFound(name)
        for d in self.dirs:
            path = os.path.join(d, *name.split("/"))
            if os.path.isfile(path):
                return path
        raise TemplateNotFound(name)

    def get_template(self, name) -> Template:
        if isinstance(name, Template):
            return name
        path = self._find(name)
        mtime = os.path.getmtime(path)
        with self._lock:
            cached = self._cache.get(name)
            if cached and (not self.auto_reload or cached[0] == mtime):
                return cached[1]
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        template = Template(self, source, name)
        with self._lock:
            self._cache[name] = (mtime, template)
        return template

    def from_string(self, source, name="<string>") -> Template:
        return Template(self, source, name)

    # -- running
    def render_template(self, template, scope, out, blocks):
        _collect_blocks(template.body, blocks)
        if template.extends is not None:
            # child template: run its top-level code (sets, imports, macros) but not its output
            self.run(template.body, scope, [], blocks, skip_blocks=True)
            parent = self.get_template(template.extends(scope))
            self.render_template(parent, scope, out, blocks)
        else:
            self.run(template.body, scope, out, blocks)

    def output(self, value, out):
        if value is None:
            out.append("None")
        elif isinstance(value, Undefined):
            return
        elif hasattr(value, "__html__"):
            out.append(value.__html__())
        elif self.autoescape:
            out.append(escape(value))
        else:
            out.append(str(value))

    def run(self, nodes, scope, out, blocks, skip_blocks=False):
        for node in nodes:
            t = type(node)
            if t is Text:
                if not skip_blocks:
                    out.append(node.text)
            elif t is Output:
                if not skip_blocks:
                    self.output(node.expr(scope), out)
            elif t is If:
                for cond, body in node.branches:
                    if cond(scope):
                        self.run(body, scope, out, blocks, skip_blocks)
                        break
                else:
                    if node.else_ is not None:
                        self.run(node.else_, scope, out, blocks, skip_blocks)
            elif t is For:
                self._run_for(node, scope, out, blocks, skip_blocks)
            elif t is Set:
                if isinstance(node.expr, tuple):     # block set
                    buf = []
                    self.run(node.expr[1], scope.child(), buf, blocks)
                    scope.set(node.targets[0], Markup("".join(buf)))
                    continue
                value = node.expr(scope)
                if node.attr:
                    obj = scope.lookup(node.targets[0])
                    setattr(obj, node.attr, value)
                elif len(node.targets) == 1:
                    scope.set(node.targets[0], value)
                else:
                    for name, v in zip(node.targets, value):
                        scope.set(name, v)
            elif t is With:
                inner = scope.child()
                for name, expr in node.assigns:
                    inner.set(name, expr(scope))
                self.run(node.body, inner, out, blocks, skip_blocks)
            elif t is Include:
                if skip_blocks:
                    continue
                try:
                    tmpl = self.get_template(node.name(scope))
                except TemplateNotFound:
                    if node.ignore_missing:
                        continue
                    raise
                self.render_template(tmpl, scope.child(), out, {})
            elif t is Block:
                if skip_blocks:
                    continue
                block = blocks.get(node.name, node)
                self.run(block.body, scope.child(), out, blocks)
            elif t is Macro:
                scope.set(node.name, self._make_macro(node, scope, blocks))
            elif t is FromImport:
                module = self._import(node.template(scope), scope)
                for name, alias in node.names:
                    scope.set(alias, module.lookup(name))
            elif t is ImportAs:
                module = self._import(node.template(scope), scope)
                scope.set(node.alias, _Module(module))
            elif t is Extends:
                continue

    def _run_for(self, node, scope, out, blocks, skip_blocks):
        items = node.iter(scope)
        if isinstance(items, dict):
            items = list(items)
        items = list(items) if not isinstance(items, list) else items
        if node.cond is not None:
            kept = []
            for item in items:
                probe = scope.child()
                self._bind_targets(probe, node.targets, item)
                if node.cond(probe):
                    kept.append(item)
            items = kept
        if not items:
            if node.else_ is not None:
                self.run(node.else_, scope, out, blocks, skip_blocks)
            return
        loop = LoopContext(items)
        for index, item in enumerate(items):
            loop.index0 = index
            inner = scope.child({"loop": loop})
            self._bind_targets(inner, node.targets, item)
            self.run(node.body, inner, out, blocks, skip_blocks)

    @staticmethod
    def _bind_targets(scope, targets, item):
        if len(targets) == 1 and isinstance(targets[0], str):
            scope.vars[targets[0]] = item
            return
        values = list(item)
        if len(values) != len(targets):
            raise TemplateError(f"Cannot unpack {len(values)} values into {len(targets)} loop variables")
        for target, value in zip(targets, values):
            if isinstance(target, list):
                Environment._bind_targets(scope, target, value)
            else:
                scope.vars[target] = value

    def _make_macro(self, node, def_scope, blocks):
        env = self

        def macro(*args, **kwargs):
            inner = def_scope.child()
            for i, (name, default) in enumerate(node.params):
                if i < len(args):
                    value = args[i]
                elif name in kwargs:
                    value = kwargs.pop(name)
                elif default is not None:
                    value = default(inner)
                else:
                    value = Undefined(name)
                inner.set(name, value)
            inner.set("varargs", list(args[len(node.params):]))
            inner.set("kwargs", kwargs)
            buf = []
            env.run(node.body, inner, buf, blocks)
            return Markup("".join(buf))
        macro.__name__ = node.name
        return macro

    def _import(self, name, scope):
        tmpl = self.get_template(name)
        module = scope.child()
        self.run(tmpl.body, module, [], {}, skip_blocks=False)
        return module


class _SelfRef:
    """`self.<block>()` renders a block again (the most specific override)."""

    def __init__(self, env, blocks, scope):
        self._env, self._blocks, self._scope = env, blocks, scope

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        block = self._blocks.get(name)

        def render():
            if block is None:
                return Markup("")
            buf = []
            self._env.run(block.body, self._scope.child(), buf, self._blocks)
            return Markup("".join(buf))
        return render


class _Module:
    def __init__(self, scope):
        self._scope = scope

    def __getattr__(self, name):
        return self._scope.lookup(name)


def _collect_blocks(nodes, blocks):
    for node in nodes:
        if isinstance(node, Block):
            blocks.setdefault(node.name, node)
            _collect_blocks(node.body, blocks)
        elif isinstance(node, If):
            for _c, body in node.branches:
                _collect_blocks(body, blocks)
            if node.else_:
                _collect_blocks(node.else_, blocks)
        elif isinstance(node, (For, With)):
            _collect_blocks(node.body, blocks)

