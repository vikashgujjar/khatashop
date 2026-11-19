"""HTML-safe strings (standard library only).

`Markup` is text that is already safe HTML; `escape()` turns any value into safe HTML.
The template engine escapes everything printed with {{ }} unless it is Markup.
Escaping matches markupsafe exactly (& < > ' " -> &amp; &lt; &gt; &#39; &#34;).
"""
from __future__ import annotations

import string


def _escape_text(text: str) -> str:
    return (text.replace("&", "&amp;").replace(">", "&gt;").replace("<", "&lt;")
            .replace("'", "&#39;").replace('"', "&#34;"))


def escape(value) -> "Markup":
    if hasattr(value, "__html__"):
        return Markup(value.__html__())
    return Markup(_escape_text(str(value)))


def soft_str(value) -> str:
    """str() that keeps Markup as Markup."""
    return value if isinstance(value, str) else str(value)


class _EscapeFormatter(string.Formatter):
    def format_field(self, value, format_spec):
        if hasattr(value, "__html__"):
            if format_spec:
                raise ValueError("Markup does not support format specs")
            return value.__html__()
        return _escape_text(super().format_field(value, format_spec))


class Markup(str):
    __slots__ = ()

    def __new__(cls, base="", encoding=None, errors="strict"):
        if hasattr(base, "__html__"):
            base = base.__html__()
        if encoding is None:
            return super().__new__(cls, base)
        return super().__new__(cls, base, encoding, errors)

    def __html__(self):
        return self

    def __add__(self, other):
        if isinstance(other, str) or hasattr(other, "__html__"):
            return Markup(str.__add__(self, escape(other)))
        return NotImplemented

    def __radd__(self, other):
        if isinstance(other, str) or hasattr(other, "__html__"):
            return escape(other).__add__(self)
        return NotImplemented

    def __mul__(self, n):
        return Markup(str.__mul__(self, n))

    def __mod__(self, arg):
        if isinstance(arg, tuple):
            arg = tuple(escape(a) if not isinstance(a, (int, float)) else a for a in arg)
        elif isinstance(arg, dict):
            arg = {k: (escape(v) if not isinstance(v, (int, float)) else v) for k, v in arg.items()}
        elif not isinstance(arg, (int, float)):
            arg = escape(arg)
        return Markup(str.__mod__(self, arg))

    def format(self, *args, **kwargs):
        return Markup(_EscapeFormatter().vformat(self, args, kwargs))

    def join(self, seq):
        return Markup(str.join(self, (escape(s) for s in seq)))

    def __repr__(self):
        return f"Markup({str.__repr__(self)})"
