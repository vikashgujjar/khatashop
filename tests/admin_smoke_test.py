"""End-to-end test of super admin, restaurant registration, staff logins,
payment option setup, Excel import/export and reports.

Everything happens inside a throwaway test restaurant (and a throwaway super
admin), which are removed again at the end, so real data is not touched.

Run:  .venv\\Scripts\\python tests\\admin_smoke_test.py
"""
import io
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook, load_workbook  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import super_admin  # noqa: E402

failures = []
tag = uuid.uuid4().hex[:6]


def check(label, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + label + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def xlsx(rows):
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def csrf_of(html: bytes) -> str:
    return re.search(rb'name="csrf-token" content="([^"]+)"', html).group(1).decode()


def main():
    app = create_app()
    app.config["TESTING"] = True
    sa_email, sa_pw = f"sa_{tag}@test.local", "Test-pass-123"
    staff_email, staff_pw = f"staff_{tag}@test.local", "Staff-pass-123"
    company_id = None

    with app.app_context():
        sa_id = super_admin.create("Test Super", sa_email, sa_pw)

    c = app.test_client()
    try:
        print("Super admin sign-in")
        token = csrf_of(c.get("/login").data)
        r = c.post("/login", data={"_csrf": token, "email": sa_email, "password": "wrong-password"})
        check("wrong password refused", r.status_code == 401)
        r = c.post("/login", data={"_csrf": token, "email": sa_email, "password": sa_pw})
        check("super admin lands on Restaurants", r.status_code == 302 and r.headers["Location"].endswith("/admin/restaurants"), r.headers.get("Location"))
        page = c.get("/admin/restaurants")
        token = csrf_of(page.data)
        check("restaurants page", page.status_code == 200 and b"Register Restaurant" in page.data)
        check("restaurant cards show item counts (no Python internals)", b"built-in method" not in page.data
              and re.search(r"\d+ cat · \d+ items", page.data.decode("utf-8")) is not None)
        check("outlet screens need a restaurant first", c.get("/dashboard").status_code == 302)
        for url in ["/admin/super-admins", "/admin/restaurants/new", "/admin/restaurants/8"]:
            check(f"GET {url}", c.get(url).status_code == 200)

        print("Register restaurant")
        r = c.post("/admin/restaurants/new", data={"_csrf": token, "name": "", "location": "", "abbreviation": "X"})
        check("bad restaurant form rejected", r.status_code == 422 and b"exactly 2 letters" in r.data)
        with app.app_context():
            used = {row["abbreviation"] for row in db.fetch_all("SELECT abbreviation FROM company_detail")}
        abbr = next(a + b for a in "QZXW" for b in "qzxjw" if a + b not in used)
        r = c.post("/admin/restaurants/new", data={"_csrf": token, "name": f"Test Outlet {tag}", "location": "Test City",
                                                   "abbreviation": abbr, "status": "open"})  # no default payment options
        check("restaurant registered", r.status_code == 302, r.status_code)
        company_id = int(re.search(r"/admin/restaurants/(\d+)", r.headers["Location"]).group(1))
        r = c.post("/admin/restaurants/new", data={"_csrf": token, "name": "Dup", "location": "x", "abbreviation": abbr})
        check("duplicate bill prefix rejected", r.status_code == 422 and b"already used" in r.data)

        print("Staff login")
        r = c.post(f"/admin/restaurants/{company_id}/staff/new", data={"_csrf": token, "name": "Test Staff", "email": staff_email,
                                                                       "password": "short", "user_type": "8", "active": "1"})
        check("short password rejected", r.status_code == 422)
        r = c.post(f"/admin/restaurants/{company_id}/staff/new", data={"_csrf": token, "name": "Test Staff", "email": staff_email,
                                                                       "password": staff_pw, "user_type": "8", "active": "1"})
        check("staff login created", r.status_code == 302)
        r = c.post(f"/admin/restaurants/{company_id}/staff/new", data={"_csrf": token, "name": "Dup", "email": staff_email,
                                                                       "password": staff_pw, "user_type": "8"})
        check("duplicate staff email rejected", r.status_code == 422)

        print("Open restaurant + payment options")
        r = c.post(f"/admin/restaurants/{company_id}/open", data={"_csrf": token})
        check("open restaurant", r.status_code == 302 and c.get("/dashboard").status_code == 200)
        pos = c.get("/bills/new")
        check("billing screen offers payment setup", b"Set up Cash, Card" in pos.data)
        r = c.post("/bills/payment-options", data={"_csrf": token})
        with app.app_context():
            modes = db.fetch_all("SELECT payment_mode FROM bill_payment_mode WHERE company_detail_id=%s ORDER BY `order`", (company_id,))
        check("Cash, Card, UPI added", [m["payment_mode"] for m in modes] == ["Cash", "Card", "UPI"], modes)

        print("Excel import / export")
        r = c.get("/menu/template.xlsx")
        check("menu template downloads", r.status_code == 200 and r.data[:2] == b"PK")
        check("category template downloads", c.get("/menu-categories/template.xlsx").data[:2] == b"PK")
        bad = xlsx([["Item Name", "Category", "Price", "GST %"], ["TEA", "HOT", "abc", "5"], ["", "HOT", "10", "5"]])
        r = c.post("/menu/import", data={"_csrf": token, "file": (bad, "bad.xlsx")}, content_type="multipart/form-data")
        check("bad menu file rejected with row errors", r.status_code == 422 and b"Row 2" in r.data and b"Row 3" in r.data)
        menu_rows = [["Item Name *", "Category *", "Price *", "GST % *", "Veg", "Status"],
                     ["PLAIN TEA", "HOT BEVERAGES", 15, 5, "VEG", "Show"],
                     ["MASALA CHAI", "HOT BEVERAGES", 20, 5, "", ""],
                     ["MILK SHAKE", "COLD BEVERAGES", 80, 5, "VEG", "Show"],
                     ["CHICKEN ROLL", "ROLLS", 120, 5, "NON VEG", "Hide"]]
        r = c.post("/menu/import", data={"_csrf": token, "file": (xlsx(menu_rows), "menu.xlsx")}, content_type="multipart/form-data")
        check("menu import without new categories refused", r.status_code == 422 and b"does not exist" in r.data)
        r = c.post("/menu/import", data={"_csrf": token, "create_categories": "1", "file": (xlsx(menu_rows), "menu.xlsx")}, content_type="multipart/form-data")
        check("menu import succeeds", r.status_code == 302)
        flash = c.get("/menu/").data
        check("import summary shown", b"4 added" in flash and b"3 new categories" in flash, re.findall(rb"Import finished[^<]*", flash))

        exported = c.get("/menu/export.xlsx")
        wb = load_workbook(io.BytesIO(exported.data))
        ws = wb.worksheets[0]
        check("export has 4 items + header", ws.max_row == 5, ws.max_row)
        ws.cell(row=2, column=4).value = 99  # change one price
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        r = c.post("/menu/import", data={"_csrf": token, "file": (buf, "edited.xlsx")}, content_type="multipart/form-data")
        flash = c.get("/menu/").data
        check("re-import of edited export: 1 updated, 3 unchanged", b"0 added, 1 updated, 3 unchanged" in flash, re.findall(rb"Import finished[^<]*", flash))

        cats = io.BytesIO(b"Category Name,Display Order,Status\nHOT BEVERAGES,1,Show\nDESSERTS,9,Show\nROLLS,,Show\n")
        r = c.post("/menu-categories/import", data={"_csrf": token, "file": (cats, "cats.csv")}, content_type="multipart/form-data")
        flash = c.get("/menu-categories/").data
        check("category CSV import", r.status_code == 302 and b"1 added, 1 updated, 1 unchanged" in flash, re.findall(rb"Import finished[^<]*", flash))

        def roll_items_active():
            with app.app_context():
                return [x["active"] for x in db.fetch_all("SELECT M.active FROM menu M JOIN menu_category MC USING(menu_category_id) "
                                                           "WHERE MC.company_detail_id=%s AND MC.menu_category_name='ROLLS'", (company_id,))]
        c.post("/menu-categories/import", data={"_csrf": token, "file": (io.BytesIO(b"Category Name,Status\nROLLS,Hide\n"), "h.csv")}, content_type="multipart/form-data")
        check("hiding a category hides its items", roll_items_active() == [0], roll_items_active())
        c.post("/menu-categories/import", data={"_csrf": token, "file": (io.BytesIO(b"Category Name,Status\nROLLS,Show\n"), "s.csv")}, content_type="multipart/form-data")
        check("showing a category shows its items", roll_items_active() == [1], roll_items_active())
        check("category export", c.get("/menu-categories/export.xlsx").data[:2] == b"PK")
        r = c.post("/menu-categories/import", data={"_csrf": token, "file": (io.BytesIO(b"junk"), "x.xls")}, content_type="multipart/form-data")
        check(".xls explained", r.status_code == 422 and b"Save As" in r.data)

        print("Billing + reports in the test restaurant")
        with app.app_context():
            item = db.fetch_one("SELECT M.item_id FROM menu M JOIN menu_category MC USING(menu_category_id) WHERE MC.company_detail_id=%s AND M.item_name='PLAIN TEA'", (company_id,))
            mode = db.fetch_value("SELECT payment_mode_id FROM bill_payment_mode WHERE company_detail_id=%s AND payment_mode='Cash'", (company_id,))
        r = c.post("/api/bills", json={"items": [{"item_id": item["item_id"], "quantity": 3}], "payment_mode_id": mode}, headers={"X-CSRF-Token": token})
        body = r.get_json()
        check("bill saved with first number", r.status_code == 201 and body["bill_no"] == f"{abbr}01", body)
        H = {"X-CSRF-Token": token}
        line = [{"item_id": item["item_id"], "quantity": 2}]  # 2 x 15 = 30 + 5% GST = 31.50 -> rounded 32
        r = c.post("/api/bills", headers=H, json={"items": line, "payment_mode_id": mode, "order_type": "delivery",
                                                   "container": {"enabled": True, "mode": "percent", "value": 10}})
        delivery = r.get_json()
        check("delivery bill adds 10% container (31.50 + 3.00 = 34.50 -> 35)", r.status_code == 201 and delivery["total"] == 35.0, delivery)
        r = c.post("/api/bills", headers=H, json={"items": line, "payment_mode_id": mode, "order_type": "dine_in",
                                                   "container": {"enabled": True, "mode": "percent", "value": 10}})
        dine = r.get_json()
        check("dine-in ignores container charges (31.50 -> 32)", r.status_code == 201 and dine["total"] == 32.0, dine)
        r = c.post("/api/bills", headers=H, json={"items": line, "payment_mode_id": mode, "order_type": ["x"]})
        check("unknown order type rejected", r.status_code == 422, r.status_code)
        with app.app_context():
            types = {b["bill_no"]: b["order_placement_type"] for b in db.fetch_all(
                "SELECT bill_no, order_placement_type FROM bill WHERE company_detail_id=%s", (company_id,))}
        check("order types stored (1 dine-in, 3 delivery)", types.get(delivery["bill_no"]) == 3 and types.get(dine["bill_no"]) == 1, types)
        receipt = c.get(f"/bills/{delivery['bill_id']}").data
        check("receipt shows DELIVERY", b"DELIVERY" in receipt)
        check("bill list filters by order type", delivery["bill_no"].encode() in c.get("/bills?order_type=3").data
              and delivery["bill_no"].encode() not in c.get("/bills?order_type=1").data)
        pos = c.get("/bills/new").data
        check("billing screen has Dine-in / Pickup / Delivery", all(x in pos for x in (b'data-order="dine_in"', b'data-order="pickup"', b'data-order="delivery"')))
        for kind in ["daily", "items", "categories", "payments", "order_types", "counters"]:
            page = c.get(f"/reports/?type={kind}")
            check(f"report {kind}", page.status_code == 200 and b"Total" in page.data)
        rep = c.get("/reports/export?type=items")
        ws = load_workbook(io.BytesIO(rep.data)).worksheets[0]
        check("item report export has PLAIN TEA x7 (3 + 2 + 2)", any(r[0] == "PLAIN TEA" and r[2] == 7 for r in ws.iter_rows(min_row=3, values_only=True)))
        check("counter report shows super admin", b"Super admin" in c.get("/reports/?type=counters").data)

        print("Staff sign-in for the new restaurant")
        c.post("/admin/exit", data={"_csrf": token})
        c.post("/logout", data={"_csrf": token})
        s = app.test_client()
        t2 = csrf_of(s.get("/login").data)
        r = s.post("/login", data={"_csrf": t2, "email": staff_email, "password": staff_pw})
        check("staff signs in to dashboard", r.status_code == 302 and r.headers["Location"].endswith("/dashboard"))
        check("staff sees own restaurant", f"Test Outlet {tag}".encode() in s.get("/dashboard").data)
        check("staff cannot open super admin", s.get("/admin/restaurants").status_code == 403)
        check("staff POS shows Cash button", b'data-pay=' in s.get("/bills/new").data)
    finally:
        with app.app_context():
            if company_id:
                bills = [b["bill_id"] for b in db.fetch_all("SELECT bill_id FROM bill WHERE company_detail_id=%s", (company_id,))]
                for b in bills:
                    db.execute("DELETE FROM bill_detail WHERE bill_id=%s", (b,))
                db.execute("DELETE FROM bill WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE M FROM menu M JOIN menu_category MC USING(menu_category_id) WHERE MC.company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM menu_category WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM aspirant_registration WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM bill_payment_mode WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM company_detail WHERE company_detail_id=%s", (company_id,))
            db.execute("DELETE FROM ks_super_admin WHERE id=%s", (sa_id,))
        print("  test restaurant, its data and the test super admin were removed")

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("All checks passed")


if __name__ == "__main__":
    main()
