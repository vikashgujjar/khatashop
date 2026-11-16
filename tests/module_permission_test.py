"""Per-restaurant module permissions set by super admins. Uses a throwaway restaurant.
Run:  .venv\\Scripts\\python tests\\module_permission_test.py
"""
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app, db  # noqa: E402
from app.models import super_admin  # noqa: E402
from app.security import CSRF_SESSION_KEY  # noqa: E402

failures = []


def check(label, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + label + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def main():
    app = create_app()
    app.config["TESTING"] = True
    tag = uuid.uuid4().hex[:6]
    email, pw = f"sa_{tag}@test.local", "Test-pass-123"
    cid = None
    with app.app_context():
        sa_id = super_admin.create("Perm Tester", email, pw)
    sa = app.test_client()
    try:
        token = re.search(rb'csrf-token" content="([^"]+)', sa.get("/login").data).group(1).decode()
        sa.post("/login", data={"_csrf": token, "email": email, "password": pw})
        page = sa.get("/admin/restaurants/new")
        token = re.search(rb'csrf-token" content="([^"]+)', page.data).group(1).decode()
        check("register form lists all 7 modules, all ticked", len(re.findall(rb'type="checkbox" name="modules" value="\w+" checked', page.data)) == 7)
        with app.app_context():
            used = {r["abbreviation"] for r in db.fetch_all("SELECT abbreviation FROM company_detail")}
        abbr = next(a + b for a in "QZXW" for b in "qzxjw" if a + b not in used)
        r = sa.post("/admin/restaurants/new", data={"_csrf": token, "name": f"Perm {tag}", "location": "X", "abbreviation": abbr,
                                                    "status": "open", "default_payments": "1", "modules_present": "1", "modules": ["billing", "menu"]})
        cid = int(re.search(r"/admin/restaurants/(\d+)", r.headers["Location"]).group(1))
        with app.app_context():
            row = db.fetch_value("SELECT modules FROM ks_company_modules WHERE company_detail_id=%s", (cid,))
        check("modules saved", row == '["billing", "menu"]', row)
        cards = sa.get("/admin/restaurants").data.decode()
        card = next((a for a in re.findall(r"<article class=\"rest-card.*?</article>", cards, re.S) if f"Perm {tag}" in a), "")
        chips = re.findall(r'badge (badge-green|badge-grey off)">([^<]+)<', card)
        check("restaurant card shows on/off modules", ("badge-green", "Billing") in chips and ("badge-grey off", "Inventory") in chips, chips)
        r = sa.post(f"/admin/restaurants/{cid}", data={"_csrf": token, "name": f"Perm {tag}", "location": "X", "abbreviation": abbr, "status": "open"})
        with app.app_context():
            same = db.fetch_value("SELECT modules FROM ks_company_modules WHERE company_detail_id=%s", (cid,))
        check("saving without the module section leaves modules unchanged", same == '["billing", "menu"]', same)

        admin = app.test_client()
        with admin.session_transaction() as s:
            s["user"] = {"id": 0, "name": "A", "email": "a", "user_type": 1, "role": "staff", "company_id": cid, "company_name": None}
            s[CSRF_SESSION_KEY] = "t"
        nav = admin.get("/dashboard").data.decode()
        labels = re.findall(r'class="nav-link[^"]*"[^>]*title="([^"]+)"', nav)
        check("admin sidebar shows only Dashboard + Billing + Menu pages", set(labels) == {"Dashboard", "Generate Bill", "Bill Detail", "Daily Sale Consolidated", "Daily Sale", "All Reports",
              "Feedback Users", "Users", "Menu Category", "Menu", "Upload PDF"}, labels)
        for url in ["/tables", "/kitchen", "/online-orders/", "/inventory/procurement/", "/staff/", "/attendance/", "/m/drivers/"]:
            r = admin.get(url)
            check(f"{url} blocked for restaurant admin", r.status_code == 403 and b"turned off for this restaurant" in r.data, r.status_code)
        for url in ["/bills/new", "/bills", "/reports/", "/menu/"]:
            check(f"{url} allowed", admin.get(url).status_code == 200)
        reports = admin.get("/reports/").data
        check("inventory report tabs hidden", b"Ingredient purchase" not in reports and b"Daily sale (item-wise)" in reports)
        check("inventory report via URL falls back to daily", b"Ingredient" not in admin.get("/reports/?type=ingredient_stock").data.split(b"<h1>")[1][:200])

        waiter = app.test_client()
        with waiter.session_transaction() as s:
            s["user"] = {"id": 0, "name": "W", "email": "w", "user_type": 4, "role": "staff", "company_id": cid, "company_name": None}
            s[CSRF_SESSION_KEY] = "t"
        from app.roles import home_endpoint
        with app.test_request_context():
            from flask import g
            g.user = {"user_type": 4, "role": "staff", "company_id": cid}
            check("waiter lands on dashboard when table service is off", home_endpoint(g.user) == "dashboard.index")

        sa.post(f"/admin/restaurants/{cid}/open", data={"_csrf": token})
        r = sa.get("/tables")
        check("super admin inside the restaurant is blocked too", r.status_code == 403)

        sa.post("/admin/exit", data={"_csrf": token})
        form = {"_csrf": token, "name": f"Perm {tag}", "location": "X", "abbreviation": abbr, "status": "open",
                "modules_present": "1", "modules": ["billing", "menu", "tables"]}
        r = sa.post(f"/admin/restaurants/{cid}", data=form)
        check("modules updated", r.status_code == 302)
        check("table service now opens", admin.get("/tables").status_code == 200)
        r = sa.post(f"/admin/restaurants/{cid}", data={k: v for k, v in form.items() if k != "modules"})  # section sent, nothing ticked
        with app.app_context():
            row = db.fetch_value("SELECT modules FROM ks_company_modules WHERE company_detail_id=%s", (cid,))
        check("all modules can be turned off (dashboard only)", row == "[]" and admin.get("/bills").status_code == 403, row)

        with app.app_context():
            LEGACY = db.fetch_value("SELECT C.company_detail_id FROM company_detail C LEFT JOIN ks_company_modules M USING(company_detail_id) "
                                    "WHERE M.company_detail_id IS NULL ORDER BY C.company_detail_id LIMIT 1")
        legacy = app.test_client()
        with legacy.session_transaction() as s:
            s["user"] = {"id": 0, "name": "L", "email": "l", "user_type": 1, "role": "staff", "company_id": LEGACY, "company_name": None}
            s[CSRF_SESSION_KEY] = "t"
        check("existing restaurant without settings keeps every module", all(legacy.get(u).status_code == 200 for u in ["/tables", "/inventory/procurement/", "/online-orders/"]))
    finally:
        with app.app_context():
            if cid:
                db.execute("DELETE FROM ks_company_modules WHERE company_detail_id=%s", (cid,))
                db.execute("DELETE FROM bill_payment_mode WHERE company_detail_id=%s", (cid,))
                db.execute("DELETE FROM company_detail WHERE company_detail_id=%s", (cid,))
            db.execute("DELETE FROM ks_super_admin WHERE id=%s", (sa_id,))
        print("  throwaway restaurant and super admin removed")
    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("All checks passed")


if __name__ == "__main__":
    main()
