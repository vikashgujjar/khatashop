"""Old app-facing API compatibility: every request to the Python server must give the same
result as the Laravel backend at the same URL.

Needs both servers running: Python (default http://127.0.0.1:5000) and Laravel
(legacy_api.base_url, default http://127.0.0.1:8000). Only read-only requests are sent.
Run:  .venv\\Scripts\\python tests\\api_compat_test.py
"""
import json
import sys

import requests

PY, PHP = "http://127.0.0.1:5000", "http://127.0.0.1:8000"
failures = []


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
    a = requests.request(method, PY + path, timeout=60, **kw)
    b = requests.request(method, PHP + path, timeout=60, **kw)
    ja, jb = body(a), body(b)
    equal = a.status_code == b.status_code and ja == jb
    size = len(json.dumps(ja)) if not isinstance(ja, str) else len(ja)
    check(f"[{served}] {method} {path} -> {a.status_code}, {size} bytes, identical to Laravel", equal,
          f"python {a.status_code} {str(ja)[:120]} | laravel {b.status_code} {str(jb)[:120]}")


def main():
    try:
        requests.get(PHP + "/api/rooms", timeout=10)
    except requests.RequestException:
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
    r = requests.get(PY + "/api/this/does/not/exist", headers={"Accept": "application/json"}, timeout=30)
    check("unknown old URL answered by Laravel (404 passes through)", r.status_code == requests.get(PHP + "/api/this/does/not/exist", headers={"Accept": "application/json"}).status_code)
    print("New app's own pages still served by Python")
    r = requests.get(PY + "/login", timeout=30)
    check("/login is the Python page", r.status_code == 200 and "Kailash" in r.text)
    r = requests.post(PY + "/api/bills", json={}, headers={"Accept": "application/json"}, timeout=30)
    check("/api/bills is the Python endpoint (needs sign-in)", r.status_code in (401, 403) and "ok" in r.json())
    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED"); sys.exit(1)
    print("All checks passed")


if __name__ == "__main__":
    main()
