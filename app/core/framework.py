"""A small WSGI web framework on the Python standard library.

It offers the pieces this app uses, with the same names the code already calls:
  App / Blueprint / route()           URL rules with <int:id>, <path:p> and <name> parts
  request, session, g, current_app    per-request objects (safe with threads)
  url_for, redirect, render_template, flash, get_flashed_messages, jsonify,
  send_file, send_from_directory, abort, Response
  before_request / after_request / teardown_appcontext / errorhandler / context_processor
  app.test_client()                   for the test suites
Sessions are kept in a signed cookie (HMAC-SHA256 with the secret key).
"""
from __future__ import annotations

import base64
import contextvars
import functools
import hashlib
import hmac
import io
import json
import logging
import mimetypes
import os
import re
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from .http import (BadRequest, Headers, HTTPException, InternalServerError, MethodNotAllowed, MultiDict, NotFound,
                   Request, RequestEntityTooLarge, Response, abort, dumps, http_date)
from .markup import Markup
from .templating import Environment

log = logging.getLogger("khatashop.core")

# --------------------------------------------------------------- context --

_app_ctx: contextvars.ContextVar = contextvars.ContextVar("ks_app_ctx", default=None)
_req_ctx: contextvars.ContextVar = contextvars.ContextVar("ks_req_ctx", default=None)


class _Globals:
    """`g`: a namespace for the current request / app context."""

    def get(self, name, default=None):
        return self.__dict__.get(name, default)

    def pop(self, name, default=None):
        return self.__dict__.pop(name, default)

    def setdefault(self, name, default=None):
        return self.__dict__.setdefault(name, default)

    def __contains__(self, name):
        return name in self.__dict__

    def __iter__(self):
        return iter(self.__dict__)

    def __repr__(self):
        return f"<g {self.__dict__!r}>"


class AppContext:
    def __init__(self, app):
        self.app = app
        self.g = _Globals()
        self._tokens = []

    def push(self):
        self._tokens.append(_app_ctx.set(self))

    def pop(self, exc=None):
        try:
            for fn in reversed(self.app.teardown_appcontext_funcs):
                try:
                    fn(exc)
                except Exception:  # noqa: BLE001 - cleanup must not hide the real error
                    log.exception("Error in teardown")
        finally:
            _app_ctx.reset(self._tokens.pop())

    def __enter__(self):
        self.push()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.pop(exc)
        return False


class RequestContext:
    def __init__(self, app, environ):
        self.app = app
        self.request = Request(environ, app.config.get("MAX_CONTENT_LENGTH"))
        self.session = app.session_interface.open(app, self.request)
        self.flashes = None
        self.app_ctx = None
        self._token = None

    def push(self):
        current = _app_ctx.get()
        if current is None or current.app is not self.app:
            self.app_ctx = AppContext(self.app)
            self.app_ctx.push()
        self._token = _req_ctx.set(self)

    def pop(self, exc=None):
        _req_ctx.reset(self._token)
        if self.app_ctx is not None:
            self.app_ctx.pop(exc)
            self.app_ctx = None

    def __enter__(self):
        self.push()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.pop(exc)
        return False


def has_app_context() -> bool:
    return _app_ctx.get() is not None


def has_request_context() -> bool:
    return _req_ctx.get() is not None


class LocalProxy:
    """Forwards everything to the object returned by the getter (request, session, g, current_app)."""
    __slots__ = ("_get", "__name")

    def __init__(self, getter, name):
        object.__setattr__(self, "_get", getter)
        object.__setattr__(self, "_LocalProxy__name", name)

    def _obj(self):
        obj = object.__getattribute__(self, "_get")()
        if obj is None:
            raise RuntimeError(f"Working outside of {object.__getattribute__(self, '_LocalProxy__name')} context.")
        return obj

    def __getattr__(self, name):
        return getattr(self._obj(), name)

    def __setattr__(self, name, value):
        setattr(self._obj(), name, value)

    def __delattr__(self, name):
        delattr(self._obj(), name)

    def __getitem__(self, key):
        return self._obj()[key]

    def __setitem__(self, key, value):
        self._obj()[key] = value

    def __delitem__(self, key):
        del self._obj()[key]

    def __contains__(self, key):
        return key in self._obj()

    def __iter__(self):
        return iter(self._obj())

    def __len__(self):
        return len(self._obj())

    def __bool__(self):
        try:
            return bool(self._obj())
        except RuntimeError:
            return False

    def __eq__(self, other):
        return self._obj() == other

    def __repr__(self):
        try:
            return repr(self._obj())
        except RuntimeError:
            return f"<unbound {object.__getattribute__(self, '_LocalProxy__name')}>"


def _get_app():
    ctx = _app_ctx.get()
    return ctx.app if ctx else None


def _get_g():
    ctx = _app_ctx.get()
    return ctx.g if ctx else None


def _get_request():
    ctx = _req_ctx.get()
    return ctx.request if ctx else None


def _get_session():
    ctx = _req_ctx.get()
    return ctx.session if ctx else None


current_app = LocalProxy(_get_app, "application")
g = LocalProxy(_get_g, "application")
request = LocalProxy(_get_request, "request")
session = LocalProxy(_get_session, "request")


# --------------------------------------------------------------- sessions --

class Session(dict):
    """The visitor's session; changes are saved in a signed cookie."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.modified = False
        self.accessed = False
        self.new = False

    def _mark(self):
        self.modified = True
        self.accessed = True

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self._mark()

    def __delitem__(self, key):
        super().__delitem__(key)
        self._mark()

    def __getitem__(self, key):
        self.accessed = True
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.accessed = True
        return super().get(key, default)

    def setdefault(self, key, default=None):
        if key not in self:
            self._mark()
        return super().setdefault(key, default)

    def pop(self, key, *default):
        if key in self:
            self._mark()
        return super().pop(key, *default)

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        self._mark()

    def clear(self):
        if self:
            self._mark()
        super().clear()

    @property
    def permanent(self):
        return self.get("_permanent", False)

    @permanent.setter
    def permanent(self, value):
        if value:
            dict.__setitem__(self, "_permanent", True)
            self.modified = True
        elif dict.get(self, "_permanent"):
            dict.__delitem__(self, "_permanent")
            self.modified = True


class SessionInterface:
    cookie_name = "session"

    def _key(self, app) -> bytes:
        secret = app.config.get("SECRET_KEY") or ""
        return hashlib.sha256(("khatashop-session:" + str(secret)).encode("utf-8")).digest()

    def _sign(self, app, payload: bytes) -> str:
        return base64.urlsafe_b64encode(hmac.new(self._key(app), payload, hashlib.sha256).digest()).decode().rstrip("=")

    def dumps(self, app, data: dict) -> str:
        body = base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":"), default=str).encode("utf-8")).decode().rstrip("=")
        stamp = str(int(time.time()))
        return f"{body}.{stamp}.{self._sign(app, (body + '.' + stamp).encode())}"

    def loads(self, app, value: str) -> dict | None:
        try:
            body, stamp, sig = value.split(".")
        except ValueError:
            return None
        if not hmac.compare_digest(sig, self._sign(app, (body + "." + stamp).encode())):
            return None
        lifetime = app.config.get("PERMANENT_SESSION_LIFETIME") or timedelta(days=31)
        try:
            if time.time() - int(stamp) > lifetime.total_seconds():
                return None
            data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def open(self, app, req: Request) -> Session:
        raw = req.cookies.get(self.cookie_name)
        data = self.loads(app, raw) if raw else None
        s = Session(data or {})
        s.modified = False
        s.accessed = False
        s.new = data is None
        return s

    def save(self, app, s: Session, response: Response):
        name = self.cookie_name
        opts = dict(path="/", httponly=app.config.get("SESSION_COOKIE_HTTPONLY", True),
                    secure=app.config.get("SESSION_COOKIE_SECURE", False),
                    samesite=app.config.get("SESSION_COOKIE_SAMESITE", "Lax"))
        if not s:
            if s.modified:
                response.delete_cookie(name, path="/")
            return
        if s.accessed:
            response.headers.add("Vary", "Cookie")
        if not (s.modified or s.permanent):
            return
        expires = None
        if s.permanent:
            lifetime = app.config.get("PERMANENT_SESSION_LIFETIME") or timedelta(days=31)
            expires = time.time() + lifetime.total_seconds()
        response.set_cookie(name, self.dumps(app, dict(s)), expires=expires, **opts)


# ---------------------------------------------------------------- routing --

_PART_RE = re.compile(r"<(?:(?P<conv>\w+):)?(?P<name>\w+)>")
_PATH_SAFE = "!$&'()*+,/:;=@"
_QUERY_SAFE = "!$'()*,/:;?@"


class Rule:
    def __init__(self, rule: str, endpoint: str, methods, view):
        self.rule = rule
        self.endpoint = endpoint
        self.methods = {m.upper() for m in (methods or ["GET"])}
        if "GET" in self.methods:
            self.methods.add("HEAD")
        self.view = view
        self.arguments = []
        self.converters = {}
        regex, static_len, pos = "^", 0, 0
        for m in _PART_RE.finditer(rule):
            static = rule[pos:m.start()]
            regex += re.escape(static)
            static_len += len(static)
            conv = m.group("conv") or "default"
            name = m.group("name")
            self.arguments.append(name)
            self.converters[name] = conv
            if conv == "int":
                regex += rf"(?P<{name}>\d+)"
            elif conv == "path":
                regex += rf"(?P<{name}>[^/].*?)"
            else:
                regex += rf"(?P<{name}>[^/]+)"
            pos = m.end()
        static = rule[pos:]
        regex += re.escape(static) + "$"
        static_len += len(static)
        self.regex = re.compile(regex)
        self.sort_key = (sum(1 for c in self.converters.values() if c == "path"), -static_len, len(self.arguments))

    def match(self, path: str):
        m = self.regex.match(path)
        if not m:
            return None
        values = {}
        for name, raw in m.groupdict().items():
            if self.converters[name] == "int":
                values[name] = int(raw)
            else:
                values[name] = raw
        return values

    def build(self, values: dict) -> tuple[str, dict]:
        rest = dict(values)
        def repl(m):
            name = m.group("name")
            value = rest.pop(name)
            if m.group("conv") == "int":
                return str(int(value))
            return quote(str(value), safe=_PATH_SAFE)
        return _PART_RE.sub(repl, self.rule), rest

    def __repr__(self):
        return f"<Rule {self.rule!r} -> {self.endpoint}>"


class UrlMap:
    def __init__(self):
        self.rules: list[Rule] = []
        self.by_endpoint: dict[str, list[Rule]] = {}
        self._sorted = None

    def add(self, rule: Rule):
        self.rules.append(rule)
        self.by_endpoint.setdefault(rule.endpoint, []).append(rule)
        self._sorted = None

    def iter_rules(self):
        return iter(self.rules)

    def _ordered(self):
        if self._sorted is None:
            indexed = list(enumerate(self.rules))
            self._sorted = [r for _i, r in sorted(indexed, key=lambda ir: (ir[1].sort_key, ir[0]))]
        return self._sorted

    def match(self, path: str, method: str):
        allowed = set()
        for rule in self._ordered():
            values = rule.match(path)
            if values is None:
                continue
            if method in rule.methods:
                return rule, values
            allowed |= rule.methods
        if allowed:
            if method == "OPTIONS":
                return "options", sorted(allowed | {"OPTIONS"})
            raise MethodNotAllowed(sorted(allowed))
        if not path.endswith("/"):
            for rule in self._ordered():
                if rule.rule.endswith("/") and rule.match(path + "/") is not None:
                    return "slash", path + "/"
        raise NotFound()


# ------------------------------------------------------------- blueprints --

class Blueprint:
    def __init__(self, name: str, import_name: str = "", url_prefix: str | None = None):
        self.name = name
        self.url_prefix = url_prefix or ""
        self.deferred = []

    def route(self, rule: str, methods=None, endpoint: str | None = None, **_options):
        def decorator(fn):
            self.deferred.append((rule, endpoint or fn.__name__, methods, fn))
            return fn
        return decorator


# -------------------------------------------------------------------- app --

class Config(dict):
    pass


def _json_response(obj, status=None):
    text = dumps(obj, separators=(",", ":")) + "\n"
    return Response(text, status=status, mimetype="application/json")


class App:
    def __init__(self, import_name: str, root_path: str | None = None):
        self.import_name = import_name
        mod = __import__(import_name, fromlist=["__file__"]) if import_name else None
        self.root_path = root_path or (os.path.dirname(os.path.abspath(mod.__file__)) if mod and getattr(mod, "__file__", None) else os.getcwd())
        self.static_folder = os.path.join(self.root_path, "static")
        self.template_folder = os.path.join(self.root_path, "templates")
        self.config = Config(DEBUG=False, TESTING=False, SECRET_KEY=None, MAX_CONTENT_LENGTH=None,
                             PERMANENT_SESSION_LIFETIME=timedelta(days=31), SESSION_COOKIE_HTTPONLY=True,
                             SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=False, TEMPLATES_AUTO_RELOAD=True)
        self.url_map = UrlMap()
        self.view_functions = {}
        self.before_request_funcs = []
        self.after_request_funcs = []
        self.teardown_appcontext_funcs = []
        self.error_handlers = []          # (exception class, handler), most specific found by MRO
        self.context_processors = []
        self.session_interface = SessionInterface()
        self.jinja_env = Environment([self.template_folder])
        self.add_url_rule("/static/<path:filename>", "static", self._serve_static, ["GET"])

    # -- registration
    def add_url_rule(self, rule, endpoint, view, methods=None):
        self.url_map.add(Rule(rule, endpoint, methods, view))
        self.view_functions[endpoint] = view

    def route(self, rule, methods=None, endpoint=None, **_options):
        def decorator(fn):
            self.add_url_rule(rule, endpoint or fn.__name__, fn, methods)
            return fn
        return decorator

    def register_blueprint(self, bp: Blueprint):
        for rule, endpoint, methods, fn in bp.deferred:
            self.add_url_rule(bp.url_prefix.rstrip("/") + rule if bp.url_prefix else rule, f"{bp.name}.{endpoint}", fn, methods)

    def before_request(self, fn):
        self.before_request_funcs.append(fn)
        return fn

    def after_request(self, fn):
        self.after_request_funcs.append(fn)
        return fn

    def teardown_appcontext(self, fn):
        self.teardown_appcontext_funcs.append(fn)
        return fn

    def context_processor(self, fn):
        self.context_processors.append(fn)
        return fn

    def errorhandler(self, key):
        def decorator(fn):
            exc_class = key
            if isinstance(key, int):
                exc_class = {404: NotFound, 405: MethodNotAllowed, 413: RequestEntityTooLarge, 500: InternalServerError,
                             400: BadRequest}.get(key, HTTPException)
            self.error_handlers.append((exc_class, fn))
            return fn
        return decorator

    def add_template_filter(self, fn, name=None):
        self.jinja_env.filters[name or fn.__name__] = fn

    def add_template_global(self, fn, name=None):
        self.jinja_env.globals[name or fn.__name__] = fn

    # -- contexts
    def app_context(self):
        return AppContext(self)

    def test_request_context(self, path="/", method="GET", **kwargs):
        environ = _build_environ(path, method, **kwargs)
        return RequestContext(self, environ)

    def test_client(self):
        return TestClient(self)

    # -- request handling
    def __call__(self, environ, start_response):
        return self.wsgi_app(environ, start_response)

    def wsgi_app(self, environ, start_response):
        ctx = RequestContext(self, environ)
        ctx.push()
        error = None
        try:
            try:
                response = self.full_dispatch_request(ctx)
            except Exception as exc:  # noqa: BLE001 - last line of defence
                error = exc
                response = self.handle_exception(exc)
            return response(environ, start_response)
        finally:
            ctx.pop(error)

    def _match(self, ctx):
        """Routing happens first so before_request hooks know request.endpoint (like Flask)."""
        req = ctx.request
        ctx.route = None
        ctx.routing_exception = None
        try:
            rule, values = self.url_map.match(req.path, req.method)
        except HTTPException as exc:
            ctx.routing_exception = exc
            return
        ctx.route = (rule, values)
        if isinstance(rule, Rule):
            req.url_rule = rule
            req.endpoint = rule.endpoint
            req.view_args = values

    def full_dispatch_request(self, ctx):
        self._match(ctx)
        try:
            rv = None
            for fn in self.before_request_funcs:
                rv = fn()
                if rv is not None:
                    break
            if rv is None:
                rv = self.dispatch_request(ctx)
        except Exception as exc:  # noqa: BLE001
            rv = self.handle_user_exception(exc)
        response = self.make_response(rv)
        return self.process_response(ctx, response)

    def dispatch_request(self, ctx):
        req = ctx.request
        if ctx.routing_exception is not None:
            raise ctx.routing_exception
        rule, values = ctx.route
        if rule == "options":
            resp = Response("", 200)
            resp.headers.set("Allow", ", ".join(values))
            return resp
        if rule == "slash":
            qs = req.query_string.decode("latin-1")
            return redirect(values + (f"?{qs}" if qs else ""), 308)
        return rule.view(**values)

    def _find_handler(self, exc):
        for cls in type(exc).__mro__:
            for exc_class, handler in reversed(self.error_handlers):
                if exc_class is cls:
                    return handler
        return None

    def handle_user_exception(self, exc):
        handler = self._find_handler(exc)
        if handler is None:
            if isinstance(exc, HTTPException):
                return self._default_http_response(exc)
            raise exc
        return handler(exc)

    def handle_exception(self, exc):
        """An error that no handler dealt with (or that happened inside a handler)."""
        log.exception("Unhandled error: %s", exc)
        server_error = InternalServerError()
        try:
            handler = self._find_handler(server_error)
            rv = handler(server_error) if handler else self._default_http_response(server_error)
            response = self.make_response(rv)
            response.status_code = 500
        except Exception:  # noqa: BLE001
            response = Response("Internal Server Error", 500, mimetype="text/plain")
        return response

    @staticmethod
    def _default_http_response(exc: HTTPException):
        body = (f"<!doctype html>\n<html lang=en>\n<title>{exc.code} {exc.name}</title>\n"
                f"<h1>{exc.name}</h1>\n<p>{exc.description}</p>\n")
        return Response(body, exc.code)

    def make_response(self, rv):
        status = headers = None
        if isinstance(rv, tuple):
            if len(rv) == 3:
                rv, status, headers = rv
            elif len(rv) == 2:
                if isinstance(rv[1], (dict, list, Headers)) and not isinstance(rv[1], int):
                    rv, headers = rv
                else:
                    rv, status = rv
        if isinstance(rv, Response):
            response = rv
        elif isinstance(rv, (str, bytes, bytearray)):
            response = Response(rv)
        elif isinstance(rv, (dict, list)):
            response = _json_response(rv)
        elif rv is None:
            raise TypeError("The view did not return a response.")
        else:
            raise TypeError(f"The view returned an unsupported type: {type(rv).__name__}")
        if status is not None:
            response.status = status
        if headers:
            for k, v in (headers.items() if hasattr(headers, "items") else headers):
                response.headers.set(k, v)
        return response

    def process_response(self, ctx, response):
        for fn in reversed(self.after_request_funcs):
            response = fn(response)
        self.session_interface.save(self, ctx.session, response)
        return response

    # -- static files
    def _serve_static(self, filename):
        return send_from_directory(self.static_folder, filename, max_age=None)

    # -- running (development)
    def run(self, host="127.0.0.1", port=5000, debug=False, **_kwargs):
        from .server import serve
        serve(self, host=host, port=port)


# ------------------------------------------------------------ helpers --

def url_for(endpoint: str, _external: bool = False, _anchor: str | None = None, **values) -> str:
    app = current_app._obj()
    if endpoint.startswith(".") and has_request_context() and request.blueprint:
        endpoint = request.blueprint + endpoint
    rules = app.url_map.by_endpoint.get(endpoint)
    if not rules:
        raise RuntimeError(f"Could not build url for endpoint {endpoint!r}.")
    given = {k: v for k, v in values.items() if v is not None}
    for rule in rules:
        if all(arg in given for arg in rule.arguments):
            path, rest = rule.build(given)
            break
    else:
        raise RuntimeError(f"Could not build url for endpoint {endpoint!r}: missing {rules[0].arguments}.")
    query = []
    for k, v in rest.items():
        if isinstance(v, (list, tuple)):
            query.extend((k, x) for x in v if x is not None)
        else:
            query.append((k, v))
    url = path + ("?" + urlencode(query, safe=_QUERY_SAFE) if query else "")
    if _anchor:
        url += "#" + quote(_anchor, safe="")
    if _external:
        base = request.host_url.rstrip("/") if has_request_context() else "http://localhost"
        url = base + url
    return url


def redirect(location: str, code: int = 302) -> Response:
    from .markup import escape
    body = (f"<!doctype html>\n<html lang=en>\n<title>Redirecting...</title>\n<h1>Redirecting...</h1>\n"
            f"<p>You should be redirected automatically to the target URL: <a href=\"{escape(location)}\">"
            f"{escape(location)}</a>. If not, click the link.\n")
    resp = Response(body, code)
    resp.headers.set("Location", location)
    return resp


def jsonify(*args, **kwargs) -> Response:
    if args and kwargs:
        raise TypeError("jsonify() takes positional or keyword arguments, not both")
    obj = args[0] if len(args) == 1 else (list(args) if args else kwargs)
    return _json_response(obj)


def flash(message, category="message"):
    flashes = session.get("_flashes", [])
    flashes.append([category, str(message)])
    session["_flashes"] = flashes


def get_flashed_messages(with_categories=False, category_filter=()):
    ctx = _req_ctx.get()
    if ctx.flashes is None:
        ctx.flashes = [tuple(f) for f in session.pop("_flashes", [])] if "_flashes" in session else []
    flashes = ctx.flashes
    if category_filter:
        flashes = [f for f in flashes if f[0] in category_filter]
    return flashes if with_categories else [m for _c, m in flashes]


def render_template(name: str, **context) -> str:
    app = current_app._obj()
    values = {"config": app.config, "url_for": url_for, "get_flashed_messages": get_flashed_messages}
    if has_request_context():
        values.update(request=request._obj(), session=session._obj(), g=g._obj())
        for fn in app.context_processors:
            values.update(fn() or {})
    values.update(context)
    return app.jinja_env.get_template(name).render(values)


def send_file(path_or_file, mimetype=None, as_attachment=False, download_name=None, max_age=None, last_modified=None):
    if hasattr(path_or_file, "read"):
        data = path_or_file.read()
        path = None
    else:
        path = Path(path_or_file)
        data = path.read_bytes()
        download_name = download_name or path.name
        last_modified = last_modified or path.stat().st_mtime
    if mimetype is None and download_name:
        mimetype = mimetypes.guess_type(download_name)[0]
    mimetype = mimetype or "application/octet-stream"
    resp = Response(data, mimetype=mimetype)
    if download_name:
        disposition = "attachment" if as_attachment else "inline"
        try:
            download_name.encode("ascii")
            simple = re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", download_name)
            resp.headers.set("Content-Disposition", f"{disposition}; filename={download_name if simple else json.dumps(download_name)}")
        except UnicodeEncodeError:
            ascii_name = download_name.encode("ascii", "ignore").decode() or "download"
            resp.headers.set("Content-Disposition", f"{disposition}; filename={json.dumps(ascii_name)}; "
                                                    f"filename*=UTF-8''{quote(download_name, safe='')}")
    if last_modified:
        resp.headers.set("Last-Modified", http_date(last_modified))
    if max_age is None:
        resp.headers.set("Cache-Control", "no-cache")
    else:
        resp.headers.set("Cache-Control", f"public, max-age={int(max_age)}")
    if path is not None and has_request_context():
        stat = path.stat()
        etag = f'"{int(stat.st_mtime)}-{stat.st_size}"'
        resp.headers.set("ETag", etag)
        if request.headers.get("If-None-Match") == etag:
            not_modified = Response(b"", 304)
            for h in ("ETag", "Cache-Control", "Last-Modified"):
                if resp.headers.get(h):
                    not_modified.headers.set(h, resp.headers.get(h))
            not_modified.headers.remove("Content-Type")
            return not_modified
    return resp


def send_from_directory(directory, filename, max_age=None, **kwargs):
    base = Path(directory).resolve()
    parts = filename.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts) or filename.startswith("/"):
        raise NotFound()
    path = (base / Path(*parts)).resolve()
    if base not in path.parents or not path.is_file():
        raise NotFound()
    return send_file(path, max_age=max_age, **kwargs)


def make_response(*args):
    return current_app._obj().make_response(args if len(args) > 1 else args[0])


# ---------------------------------------------------------- test client --

def _build_environ(path="/", method="GET", data=None, json_data=None, headers=None, content_type=None,
                   query_string=None, environ_base=None, cookies=""):
    split = urlsplit(path)
    qs = split.query
    if query_string:
        qs = query_string if isinstance(query_string, str) else urlencode(query_string, doseq=True)
    body = b""
    ctype = content_type
    if json_data is not None:
        body = json.dumps(json_data).encode("utf-8")
        ctype = "application/json"
    elif isinstance(data, dict):
        has_files = any(isinstance(v, tuple) or hasattr(v, "read") for v in data.values())
        if has_files or (ctype or "").startswith("multipart/form-data"):
            boundary = "----ksboundary" + os.urandom(8).hex()
            out = io.BytesIO()
            for key, value in data.items():
                values = value if isinstance(value, list) else [value]
                for v in values:
                    out.write(f"--{boundary}\r\n".encode())
                    if isinstance(v, tuple) or hasattr(v, "read"):
                        stream, filename = (v[0], v[1]) if isinstance(v, tuple) else (v, getattr(v, "name", "file"))
                        mt = (v[2] if isinstance(v, tuple) and len(v) > 2 else None) or mimetypes.guess_type(filename)[0] or "application/octet-stream"
                        out.write(f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'
                                  f"Content-Type: {mt}\r\n\r\n".encode())
                        content = stream.read()
                        out.write(content if isinstance(content, bytes) else content.encode())
                    else:
                        out.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
                        out.write(str(v).encode("utf-8"))
                    out.write(b"\r\n")
            out.write(f"--{boundary}--\r\n".encode())
            body = out.getvalue()
            ctype = f"multipart/form-data; boundary={boundary}"
        else:
            pairs = []
            for k, v in data.items():
                for one in (v if isinstance(v, list) else [v]):
                    if one is not None:
                        pairs.append((k, str(one)))
            body = urlencode(pairs).encode("utf-8")
            ctype = ctype or "application/x-www-form-urlencoded"
    elif isinstance(data, (str, bytes)):
        body = data.encode("utf-8") if isinstance(data, str) else data
    environ = {
        "REQUEST_METHOD": method.upper(),
        "PATH_INFO": (split.path or "/").encode("utf-8").decode("latin-1"),
        "QUERY_STRING": qs,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "HTTP_HOST": "localhost",
        "REMOTE_ADDR": "127.0.0.1",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(body),
        "wsgi.errors": io.StringIO(),
        "CONTENT_LENGTH": str(len(body)) if body else "",
        "CONTENT_TYPE": ctype or "",
    }
    if cookies:
        environ["HTTP_COOKIE"] = cookies
    for k, v in (headers or {}).items():
        key = k.upper().replace("-", "_")
        if key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            environ[key] = v
        else:
            environ["HTTP_" + key] = v
    environ.update(environ_base or {})
    return environ


class TestClient:
    """Calls the app without a network; keeps cookies between calls like a browser."""

    def __init__(self, app):
        self.app = app
        self.cookies: dict[str, str] = {}

    def _cookie_header(self):
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def _store_cookies(self, response):
        for header in response.headers.getlist("Set-Cookie"):
            first = header.split(";", 1)[0]
            name, _, value = first.partition("=")
            lowered = header.lower()
            if "max-age=0" in lowered or (value == "" and "expires=" in lowered):
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value

    def open(self, path="/", method="GET", data=None, json=None, headers=None, content_type=None,
             query_string=None, follow_redirects=False, environ_base=None):
        environ = _build_environ(path, method, data, json, headers, content_type, query_string, environ_base,
                                 self._cookie_header())
        captured = {}

        def start_response(status, response_headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = response_headers

        body = b"".join(self.app(environ, start_response))
        response = Response(body, status=int(captured["status"].split()[0]), headers=captured["headers"])
        self._store_cookies(response)
        if follow_redirects and response.status_code in (301, 302, 303, 307, 308) and response.location:
            return self.open(response.location, "GET" if response.status_code in (301, 302, 303) else method,
                             headers=headers, follow_redirects=True)
        return response

    def get(self, path="/", **kwargs):
        return self.open(path, "GET", **kwargs)

    def post(self, path="/", **kwargs):
        return self.open(path, "POST", **kwargs)

    def put(self, path="/", **kwargs):
        return self.open(path, "PUT", **kwargs)

    def patch(self, path="/", **kwargs):
        return self.open(path, "PATCH", **kwargs)

    def delete(self, path="/", **kwargs):
        return self.open(path, "DELETE", **kwargs)

    def options(self, path="/", **kwargs):
        return self.open(path, "OPTIONS", **kwargs)

    def head(self, path="/", **kwargs):
        return self.open(path, "HEAD", **kwargs)

    @contextmanager
    def session_transaction(self):
        environ = _build_environ("/", cookies=self._cookie_header())
        ctx = RequestContext(self.app, environ)
        ctx.push()
        try:
            s = ctx.session
            yield s
            response = Response(b"")
            s.modified = True
            self.app.session_interface.save(self.app, s, response)
            self._store_cookies(response)
        finally:
            ctx.pop()
