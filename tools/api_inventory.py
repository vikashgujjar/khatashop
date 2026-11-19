"""Writes docs/API_ENDPOINTS.md: every old URL, who calls it, and how the new server answers it.

Usage:  python tools/api_inventory.py ../backend/project
"""
from __future__ import annotations
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from list_legacy_routes import parse  # noqa: E402

from app.controllers.mobile_api import NATIVE  # noqa: E402

WEBSITE = {"rooms", "roomDetails", "check-availability", "book", "verifyotp", "getmenuitems", "submituser", "submitfeedback",
           "place_site_order", "verify_site_order", "copyimage"}
PANEL = {"admin", "store_manager", "counter_manager", "executive", "manager", "coordinator", "employee", "waiter", "kitchen"}

GROUPS = OrderedDict([
    ("customer", ("Customer mobile app", "Login by OTP, menu, addresses, orders, tracking, notifications, Juspay payments")),
    ("driver", ("Driver mobile app", "Login by OTP, assigned orders, accept / pick up / deliver")),
    ("website", ("Website (rooms, menu, feedback, site orders)", "Called by the public website / QR menu pages")),
    ("payment", ("Payment gateway (HDFC / Juspay callbacks)", "Called by the bank / payment gateway, not by people")),
    ("attendance", ("Biometric attendance machine", "The machine pushes punches and asks for the last id")),
    ("mb_api", ("Mobile menu API", "")),
    ("auth", ("Old admin panel - login", "Replaced by the new /login page")),
    ("panel", ("Old admin panel (Angular) - screen data", "Replaced by the new web pages; still forwarded so the old panel keeps working")),
    ("other", ("Other", "")),
])


def group_of(url: str) -> str:
    parts = url.strip("/").split("/")
    if parts[0] != "api":
        return "payment" if parts[0] == "payment" else "other"
    head = parts[1] if len(parts) > 1 else ""
    if head in ("customer", "driver", "mb_api", "auth"):
        return head
    if head in ("pull_Data", "get_lastid_attendances"):
        return "attendance"
    if head in ("payment", "UpdateOrderStatus", "initiate", "response", "status", "refund"):
        return "payment"
    if head in WEBSITE:
        return "website"
    if head in PANEL:
        return "panel"
    return "other"


def main():
    laravel = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT.parent / "backend" / "project")
    routes = parse(laravel / "routes" / "api.php", "api") + parse(laravel / "routes" / "web.php", "")
    seen, rows = set(), []
    for meth, url, target in routes:
        if (meth, url) in seen or url == "/":
            continue
        seen.add((meth, url))
        rows.append((group_of(url), meth, url, target, "Python" if f"{meth} {url}" in NATIVE else "Forwarded to Laravel"))
    by_group = OrderedDict((k, []) for k in GROUPS)
    for r in rows:
        by_group[r[0]].append(r)

    out = ["# API endpoints", "",
           "Base URL for every endpoint: the new server, e.g. `https://api.khatashop.com` (same paths as before).",
           "Apps do not need any URL change.", "",
           "* **Python**: answered by the new Python server (checked identical to Laravel by `tests/api_compat_test.py`).",
           "* **Forwarded to Laravel**: the new server passes the request unchanged to the old Laravel backend",
           "  (`legacy_api.base_url` in db_config.json) and returns its answer. Same URL, same behaviour.", "",
           "Regenerate: `python tools/api_inventory.py ../backend/project`", "",
           "## Summary", "", "| Used by | Endpoints | Python | Forwarded |", "|---|---:|---:|---:|"]
    for key, items in by_group.items():
        if items:
            n_py = sum(1 for r in items if r[4] == "Python")
            out.append(f"| {GROUPS[key][0]} | {len(items)} | {n_py} | {len(items) - n_py} |")
    out.append(f"| **Total** | **{len(rows)}** | **{sum(1 for r in rows if r[4] == 'Python')}** | **{sum(1 for r in rows if r[4] != 'Python')}** |")
    for key, items in by_group.items():
        if not items:
            continue
        title, note = GROUPS[key]
        out += ["", f"## {title}", ""] + ([note, ""] if note else []) + ["| Method | URL | Served by | Old handler |", "|---|---|---|---|"]
        for _g, meth, url, target, served in sorted(items, key=lambda r: (r[2], r[1])):
            out.append(f"| {meth} | `{url}` | {served} | {target} |")
    dest = ROOT / "docs" / "API_ENDPOINTS.md"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {dest} ({len(rows)} endpoints)")


if __name__ == "__main__":
    main()
