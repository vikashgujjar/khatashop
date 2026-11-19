"""HTTP building blocks on the standard library: request / response objects, form and
file-upload parsing, headers, cookies and HTTP errors. Used by app.core.framework."""
from __future__ import annotations

import io
import json
import re
from datetime import date, datetime, timezone
from email.utils import format_datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, quote, unquote


# ----------------------------------------------------------------- errors --

class HTTPException(Exception):
    code = 500
    description = "Internal Server Error"

    def __init__(self, description: str | None = None, response=None):
        super().__init__(description or self.description)
        if description is not None:
            self.description = description
        self.response = response

    @property
    def name(self) -> str:
        try:
            return HTTPStatus(self.code).phrase
        except ValueError:
            return "Error"


class BadRequest(HTTPException):
    code = 400
    description = "The browser (or proxy) sent a request that this server could not understand."


class Unauthorized(HTTPException):
    code = 401
    description = "Please sign in."


class Forbidden(HTTPException):
    code = 403
    description = "You don't have the permission to access the requested resource."


class NotFound(HTTPException):
    code = 404
    description = "The requested URL was not found on the server."


class MethodNotAllowed(HTTPException):
    code = 405
    description = "The method is not allowed for the requested URL."

    def __init__(self, valid_methods=None, description=None):
        super().__init__(description)
        self.valid_methods = valid_methods or []


class RequestEntityTooLarge(HTTPException):
    code = 413
    description = "The data value transmitted exceeds the capacity limit."


class InternalServerError(HTTPException):
    code = 500
    description = "The server encountered an internal error."


_BY_CODE = {cls.code: cls for cls in (BadRequest, Unauthorized, Forbidden, NotFound, MethodNotAllowed,
                                       RequestEntityTooLarge, InternalServerError)}


def abort(code: int, description: str | None = None):
    cls = _BY_CODE.get(code)
    if cls is None:
        cls = type(f"HTTP{code}", (HTTPException,), {"code": code, "description": HTTPStatus(code).phrase})
    raise cls(description)


# ------------------------------------------------------------ collections --

class MultiDict:
    """Ordered key -> several values. get() / [] return the first value (like a form)."""

    def __init__(self, pairs=None):
        self._items: list[tuple[str, object]] = []
        if pairs:
            items = pairs.items() if isinstance(pairs, dict) else pairs
            for k, v in items:
                if isinstance(v, (list, tuple)) and not isinstance(pairs, list):
                    for one in v:
                        self._items.append((k, one))
                else:
                    self._items.append((k, v))

    def add(self, key, value):
        self._items.append((key, value))

    def get(self, key, default=None, type=None):  # noqa: A002 - same name as werkzeug
        for k, v in self._items:
            if k == key:
                if type is not None:
                    try:
                        return type(v)
                    except (ValueError, TypeError):
                        return default
                return v
        return default

    def getlist(self, key, type=None):  # noqa: A002
        out = []
        for k, v in self._items:
            if k == key:
                if type is not None:
                    try:
                        v = type(v)
                    except (ValueError, TypeError):
                        continue
                out.append(v)
        return out

    def __getitem__(self, key):
        for k, v in self._items:
            if k == key:
                return v
        raise BadRequestKeyError(key)

    def __contains__(self, key):
        return any(k == key for k, _ in self._items)

    def __iter__(self):
        return iter(self.keys())

    def __len__(self):
        return len(self.keys())

    def __bool__(self):
        return bool(self._items)

    def keys(self):
        seen = []
        for k, _ in self._items:
            if k not in seen:
                seen.append(k)
        return seen

    def values(self):
        return [self.get(k) for k in self.keys()]

    def items(self, multi=False):
        if multi:
            return list(self._items)
        return [(k, self.get(k)) for k in self.keys()]

    def lists(self):
        return [(k, self.getlist(k)) for k in self.keys()]

    def to_dict(self, flat=True):
        if flat:
            return {k: self.get(k) for k in self.keys()}
        return {k: self.getlist(k) for k in self.keys()}

    def copy(self):
        return MultiDict(list(self._items))

    def __repr__(self):
        return f"MultiDict({self._items!r})"


class BadRequestKeyError(BadRequest, KeyError):
    def __init__(self, key):
        BadRequest.__init__(self, f"The form field '{key}' is missing.")
        self.key = key

    def __str__(self):
        return self.description


class Headers:
    """Case-insensitive header list that keeps repeated headers (Set-Cookie)."""

    def __init__(self, items=None):
        self._list: list[tuple[str, str]] = []
        if items:
            for k, v in (items.items() if isinstance(items, dict) else items):
                self._list.append((str(k), str(v)))

    def get(self, key, default=None, type=None):  # noqa: A002
        low = key.lower()
        for k, v in self._list:
            if k.lower() == low:
                if type is not None:
                    try:
                        return type(v)
                    except (ValueError, TypeError):
                        return default
                return v
        return default

    def getlist(self, key):
        low = key.lower()
        return [v for k, v in self._list if k.lower() == low]

    def __getitem__(self, key):
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        self.set(key, value)

    def __delitem__(self, key):
        self.remove(key)

    def __contains__(self, key):
        return self.get(key) is not None

    def __iter__(self):
        return iter(self._list)

    def __len__(self):
        return len(self._list)

    def set(self, key, value):
        self.remove(key)
        self._list.append((key, str(value)))

    def add(self, key, value):
        self._list.append((key, str(value)))

    def setdefault(self, key, value):
        if key not in self:
            self.add(key, value)
        return self.get(key)

    def remove(self, key):
        low = key.lower()
        self._list = [(k, v) for k, v in self._list if k.lower() != low]

    def pop(self, key, default=None):
        value = self.get(key, default)
        self.remove(key)
        return value

    def items(self):
        return list(self._list)

    def keys(self):
        return [k for k, _ in self._list]

    def to_wsgi_list(self):
        return [(k, v) for k, v in self._list]

    def __repr__(self):
        return f"Headers({self._list!r})"


class FileStorage:
    """An uploaded file."""

    def __init__(self, stream=None, filename="", name="", content_type="application/octet-stream"):
        self.stream = stream or io.BytesIO()
        self.filename = filename
        self.name = name
        self.content_type = content_type
        self.mimetype = (content_type or "").split(";")[0].strip()

    def read(self, *args):
        return self.stream.read(*args)

    def seek(self, *args):
        return self.stream.seek(*args)

    def save(self, dst):
        self.stream.seek(0)
        if hasattr(dst, "write"):
            dst.write(self.stream.read())
        else:
            with open(dst, "wb") as fh:
                fh.write(self.stream.read())

    def __bool__(self):
        return bool(self.filename)

    def __repr__(self):
        return f"<FileStorage {self.filename!r} ({self.content_type})>"


class Accept:
    """Parsed Accept header: best_match() and quality lookup."""

    def __init__(self, header: str | None):
        self._values: list[tuple[str, float]] = []
        for part in (header or "").split(","):
            part = part.strip()
            if not part:
                continue
            bits = [b.strip() for b in part.split(";")]
            quality = 1.0
            for b in bits[1:]:
                if b.startswith("q="):
                    try:
                        quality = float(b[2:])
                    except ValueError:
                        quality = 0.0
            self._values.append((bits[0].lower(), quality))

    @staticmethod
    def _matches(pattern: str, value: str) -> bool:
        if pattern in ("*", "*/*"):
            return True
        p_main, _, p_sub = pattern.partition("/")
        v_main, _, v_sub = value.partition("/")
        return (p_main == v_main or p_main == "*") and (p_sub == v_sub or p_sub == "*")

    def quality(self, value: str) -> float:
        best = 0.0
        for pattern, q in self._values:
            if self._matches(pattern, value.lower()):
                best = max(best, q)
        return best

    def __getitem__(self, value: str) -> float:
        return self.quality(value)

    def best_match(self, matches, default=None):
        best, best_q = default, 0.0
        if not self._values:
            return matches[0] if matches else default
        for m in matches:
            q = self.quality(m)
            if q > best_q:
                best, best_q = m, q
        return best


# ---------------------------------------------------------------- parsing --

def _parse_options(header: str) -> tuple[str, dict]:
    value, _, rest = (header or "").partition(";")
    options = {}
    for m in re.finditer(r';?\s*([\w*-]+)\s*=\s*("(?:[^"\\]|\\.)*"|[^;]*)', ";" + rest):
        val = m.group(2).strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        options[m.group(1).lower()] = val
    return value.strip().lower(), options


def parse_multipart(body: bytes, boundary: str, charset="utf-8") -> tuple[MultiDict, MultiDict]:
    form, files = MultiDict(), MultiDict()
    delimiter = b"--" + boundary.encode("latin-1")
    parts = body.split(delimiter)
    for part in parts[1:]:
        if part.startswith(b"--"):
            break
        if part.startswith(b"\r\n"):
            part = part[2:]
        head, sep, content = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        if content.endswith(b"\r\n"):
            content = content[:-2]
        headers = {}
        for line in head.decode("utf-8", "replace").split("\r\n"):
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
        _, disp = _parse_options(headers.get("content-disposition", ""))
        name = disp.get("name")
        if name is None:
            continue
        if "filename" in disp or "filename*" in disp:
            filename = disp.get("filename", "")
            if "filename*" in disp:
                enc = disp["filename*"]
                if "''" in enc:
                    filename = unquote(enc.split("''", 1)[1])
            filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
            files.add(name, FileStorage(io.BytesIO(content), filename, name, headers.get("content-type", "application/octet-stream")))
        else:
            form.add(name, content.decode(charset, "replace"))
    return form, files


def http_date(value) -> str:
    """'Wed, 18 Nov 2026 09:30:00 GMT' (naive values are taken as UTC)."""
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(value, timezone.utc)
    elif isinstance(value, datetime):
        value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    elif isinstance(value, date):
        value = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return format_datetime(value, usegmt=True)


def json_default(o):
    """How values that json cannot handle are written (same as Flask's provider)."""
    from decimal import Decimal
    from uuid import UUID
    import dataclasses

    if isinstance(o, date):
        return http_date(o)
    if isinstance(o, (Decimal, UUID)):
        return str(o)
    if dataclasses.is_dataclass(o) and not isinstance(o, type):
        return dataclasses.asdict(o)
    if hasattr(o, "__html__"):
        return str(o.__html__())
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def dumps(obj, **kwargs) -> str:
    kwargs.setdefault("default", json_default)
    kwargs.setdefault("ensure_ascii", True)
    kwargs.setdefault("sort_keys", True)
    return json.dumps(obj, **kwargs)


# ---------------------------------------------------------------- request --

class Request:
    def __init__(self, environ: dict, max_content_length: int | None = None):
        self.environ = environ
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        raw_path = environ.get("PATH_INFO", "") or "/"
        try:
            self.path = raw_path.encode("latin-1").decode("utf-8")
        except UnicodeError:
            self.path = raw_path
        self.script_root = environ.get("SCRIPT_NAME", "").rstrip("/")
        self.query_string = environ.get("QUERY_STRING", "").encode("latin-1")
        self.headers = Headers(self._headers_from_environ(environ))
        self.args = MultiDict(parse_qsl(environ.get("QUERY_STRING", ""), keep_blank_values=True))
        self.max_content_length = max_content_length
        self.endpoint = None
        self.view_args = {}
        self.url_rule = None
        self._data = None
        self._form = None
        self._files = None
        self._json_cache = {}
        self._cookies = None

    @staticmethod
    def _headers_from_environ(environ):
        out = []
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                out.append((key[5:].replace("_", "-").title(), value))
            elif key in ("CONTENT_TYPE", "CONTENT_LENGTH") and value:
                out.append((key.replace("_", "-").title(), value))
        return out

    # -- basic attributes
    @property
    def full_path(self) -> str:
        return self.path + "?" + self.query_string.decode("latin-1")

    @property
    def host(self) -> str:
        return self.environ.get("HTTP_HOST") or f"{self.environ.get('SERVER_NAME', 'localhost')}:{self.environ.get('SERVER_PORT', '80')}"

    @property
    def scheme(self) -> str:
        return self.environ.get("wsgi.url_scheme", "http")

    @property
    def host_url(self) -> str:
        return f"{self.scheme}://{self.host}/"

    @property
    def url(self) -> str:
        qs = self.query_string.decode("latin-1")
        return f"{self.scheme}://{self.host}{self.script_root}{quote(self.path)}" + (f"?{qs}" if qs else "")

    @property
    def remote_addr(self) -> str | None:
        return self.environ.get("REMOTE_ADDR")

    @property
    def referrer(self) -> str | None:
        return self.environ.get("HTTP_REFERER")

    @property
    def user_agent(self) -> str:
        return self.environ.get("HTTP_USER_AGENT", "")

    @property
    def content_type(self) -> str:
        return self.environ.get("CONTENT_TYPE", "")

    @property
    def mimetype(self) -> str:
        return _parse_options(self.content_type)[0]

    @property
    def content_length(self) -> int | None:
        try:
            return int(self.environ.get("CONTENT_LENGTH") or 0) or None
        except ValueError:
            return None

    @property
    def accept_mimetypes(self) -> Accept:
        return Accept(self.environ.get("HTTP_ACCEPT"))

    @property
    def blueprint(self) -> str | None:
        if self.endpoint and "." in self.endpoint:
            return self.endpoint.rsplit(".", 1)[0]
        return None

    @property
    def cookies(self) -> dict:
        if self._cookies is None:
            jar = SimpleCookie()
            try:
                jar.load(self.environ.get("HTTP_COOKIE", ""))
            except Exception:  # noqa: BLE001 - a broken cookie header just means no cookies
                pass
            self._cookies = {k: m.value for k, m in jar.items()}
        return self._cookies

    # -- body
    def get_data(self, as_text: bool = False, cache: bool = True):
        if self._data is None:
            length = self.content_length or 0
            if self.max_content_length is not None and length > self.max_content_length:
                raise RequestEntityTooLarge()
            stream = self.environ.get("wsgi.input")
            self._data = stream.read(length) if (stream is not None and length) else b""
        return self._data.decode("utf-8", "replace") if as_text else self._data

    @property
    def data(self) -> bytes:
        return self.get_data()

    def _load_form(self):
        if self._form is not None:
            return
        self._form, self._files = MultiDict(), MultiDict()
        if self.method in ("GET", "HEAD", "OPTIONS"):
            return
        mimetype, options = _parse_options(self.content_type)
        if mimetype == "application/x-www-form-urlencoded":
            body = self.get_data().decode("latin-1")
            self._form = MultiDict([(k.encode("latin-1").decode("utf-8", "replace"), v.encode("latin-1").decode("utf-8", "replace"))
                                    for k, v in parse_qsl(body, keep_blank_values=True, encoding="latin-1")])
        elif mimetype == "multipart/form-data" and options.get("boundary"):
            self._form, self._files = parse_multipart(self.get_data(), options["boundary"])

    @property
    def form(self) -> MultiDict:
        self._load_form()
        return self._form

    @property
    def files(self) -> MultiDict:
        self._load_form()
        return self._files

    @property
    def values(self) -> MultiDict:
        combined = MultiDict(self.args.items(multi=True))
        for k, v in self.form.items(multi=True):
            combined.add(k, v)
        return combined

    @property
    def is_json(self) -> bool:
        mt = self.mimetype
        return mt == "application/json" or (mt.startswith("application/") and mt.endswith("+json"))

    def get_json(self, force: bool = False, silent: bool = False, cache: bool = True):
        key = (force,)
        if key in self._json_cache:
            return self._json_cache[key]
        if not (force or self.is_json):
            if silent:
                return None
            raise BadRequest("The request does not contain JSON (Content-Type is not application/json).")
        try:
            value = json.loads(self.get_data().decode("utf-8") or "null")
        except ValueError:
            if silent:
                return None
            raise BadRequest("The JSON sent could not be read.")
        self._json_cache[key] = value
        return value

    @property
    def json(self):
        return self.get_json()


# --------------------------------------------------------------- response --

class Response:
    default_mimetype = "text/html"

    def __init__(self, response=None, status=None, headers=None, mimetype=None, content_type=None):
        self.headers = Headers(headers)
        if content_type is None:
            mimetype = mimetype or self.default_mimetype
            content_type = f"{mimetype}; charset=utf-8" if mimetype.startswith("text/") or mimetype in (
                "application/json", "application/javascript") else mimetype
        if "Content-Type" not in self.headers:
            self.headers.set("Content-Type", content_type)
        self.status_code = 200
        if status is not None:
            self.status = status
        if response is None:
            response = b""
        self.set_data(response)

    # status
    @property
    def status(self) -> str:
        try:
            phrase = HTTPStatus(self.status_code).phrase
        except ValueError:
            phrase = "UNKNOWN"
        return f"{self.status_code} {phrase.upper()}"

    @status.setter
    def status(self, value):
        if isinstance(value, int):
            self.status_code = value
        else:
            self.status_code = int(str(value).split()[0])

    # body
    def set_data(self, value):
        if isinstance(value, str):
            value = value.encode("utf-8")
        elif not isinstance(value, (bytes, bytearray)):
            value = b"".join(v.encode("utf-8") if isinstance(v, str) else v for v in value)
        self._data = bytes(value)
        self.headers.set("Content-Length", str(len(self._data)))

    def get_data(self, as_text: bool = False):
        return self._data.decode("utf-8", "replace") if as_text else self._data

    @property
    def data(self) -> bytes:
        return self._data

    @data.setter
    def data(self, value):
        self.set_data(value)

    @property
    def text(self) -> str:
        return self.get_data(as_text=True)

    @property
    def content_type(self) -> str:
        return self.headers.get("Content-Type", "")

    @property
    def mimetype(self) -> str:
        return _parse_options(self.content_type)[0]

    @property
    def location(self) -> str | None:
        return self.headers.get("Location")

    @property
    def is_json(self) -> bool:
        mt = self.mimetype
        return mt == "application/json" or (mt.startswith("application/") and mt.endswith("+json"))

    def get_json(self, silent: bool = False):
        if not self.is_json:
            return None if silent else None
        try:
            return json.loads(self._data.decode("utf-8") or "null")
        except ValueError:
            if silent:
                return None
            raise

    @property
    def json(self):
        return self.get_json()

    # cookies
    def set_cookie(self, key, value="", max_age=None, expires=None, path="/", domain=None, secure=False,
                   httponly=False, samesite=None):
        parts = [f"{key}={value}"]
        if domain:
            parts.append(f"Domain={domain}")
        if expires is not None:
            if isinstance(expires, (int, float)):
                expires = datetime.fromtimestamp(expires, timezone.utc)
            parts.append(f"Expires={http_date(expires)}")
        if max_age is not None:
            if hasattr(max_age, "total_seconds"):
                max_age = int(max_age.total_seconds())
            parts.append(f"Max-Age={int(max_age)}")
        if secure:
            parts.append("Secure")
        if httponly:
            parts.append("HttpOnly")
        if path:
            parts.append(f"Path={path}")
        if samesite:
            parts.append(f"SameSite={samesite}")
        self.headers.add("Set-Cookie", "; ".join(parts))

    def delete_cookie(self, key, path="/", domain=None, secure=False, httponly=False, samesite=None):
        self.set_cookie(key, "", max_age=0, expires=0, path=path, domain=domain, secure=secure,
                        httponly=httponly, samesite=samesite)

    def __call__(self, environ, start_response):
        body = self._data
        if environ.get("REQUEST_METHOD") == "HEAD":
            body = b""
        start_response(self.status, self.headers.to_wsgi_list())
        return [body]

    def __repr__(self):
        return f"<Response {len(self._data)} bytes [{self.status_code}]>"
