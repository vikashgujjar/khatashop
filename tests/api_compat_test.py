"""Old app-facing API compatibility: every request to the Python server must give the same
result as the Laravel backend at the same URL.

Needs both servers running: Python (default http://127.0.0.1:5000) and Laravel
(legacy_api.base_url, default http://127.0.0.1:8000). Only read-only requests are sent.
Run:  .venv\\Scripts\\python tests\\api_compat_test.py
"""
from __future__ import annotations
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

PY, PHP = "http://127.0.0.1:5000", "http://127.0.0.1:8000"
failures = []


class _Reply:
    def __init__(self, status, data):
        self.status_code, self.content = status, data
        self.text = data.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)


def http(method, url, timeout=60, headers=None, data=None, json_body=None):
    """Small HTTP client on urllib (no third-party packages)."""
    headers = dict(headers or {})
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _Reply(r.status, r.read())
    except urllib.error.HTTPError as e:
        return _Reply(e.code, e.read())


def check(label, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + label + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def body(r):
    try:
        return r.json()
    except ValueError:
        return r.text.strip()


def same(method, path, served, **kw):
    if "json" in kw:
        kw["json_body"] = kw.pop("json")
    a = http(method, PY + path, **kw)
    b = http(method, PHP + path, **kw)
    ja, jb = body(a), body(b)
    equal = a.status_code == b.status_code and ja == jb
    size = len(json.dumps(ja)) if not isinstance(ja, str) else len(ja)
    check(f"[{served}] {method} {path} -> {a.status_code}, {size} bytes, identical to Laravel", equal,
          f"python {a.status_code} {str(ja)[:120]} | laravel {b.status_code} {str(jb)[:120]}")


def main():
    try:
        http("GET", PHP + "/api/rooms", timeout=10)
    except OSError:
        print("Laravel is not running on", PHP); sys.exit(2)
    print("Rebuilt in Python")
    same("POST", "/api/mb_api/menu_category", "native")
    same("GET", "/api/get_lastid_attendances", "native")
    for cid in (6, 8, 9, 2, 999):
        same("POST", "/api/getmenuitems", "native", data={"company_detail_id": cid})
    same("POST", "/api/getmenuitems", "native", json={"company_detail_id": 8})
    print("Forwarded to Laravel (same URL)")
    same("GET", "/api/rooms?limit=5&page=1", "forward")
    same("GET", "/api/customer/get_menu_category", "forward", headers={"Accept": "application/json"})
    same("POST", "/api/customer/user_profile", "forward", headers={"Accept": "application/json", "Authorization": "Bearer invalid.token.x"})
    same("GET", "/api/driver/profile", "forward", headers={"Accept": "application/json"})
    same("POST", "/api/customer/login_with_otp", "forward", headers={"Accept": "application/json"}, json={})
    same("POST", "/api/auth/login", "forward", headers={"Accept": "application/json"}, json={})
    same("GET", "/api/customer/restaurants", "forward", headers={"Accept": "application/json"})
    r = http("GET", PY + "/api/this/does/not/exist", headers={"Accept": "application/json"}, timeout=30)
    check("unknown old URL answered by Laravel (404 passes through)", r.status_code == http("GET", PHP + "/api/this/does/not/exist", headers={"Accept": "application/json"}).status_code)
    print("New app's own pages still served by Python")
    r = http("GET", PY + "/login", timeout=30)
    check("/login is the Python page", r.status_code == 200 and "Kailash" in r.text)
    r = http("POST", PY + "/api/bills", json_body={}, headers={"Accept": "application/json"}, timeout=30)
    check("/api/bills is the Python endpoint (needs sign-in)", r.status_code in (401, 403) and "ok" in r.json())
    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED"); sys.exit(1)
    print("All checks passed")


if __name__ == "__main__":
    main()
