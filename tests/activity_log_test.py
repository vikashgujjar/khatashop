"""End-to-end test of the super admin Activity Log.

Signs in (and fails once), registers a throwaway restaurant, bills a table, exports a report,
then checks every action is in the log with user, restaurant, bill number and IP; that
passwords are never stored; that filters, search and Excel export work; and that restaurant
staff cannot open the log. Everything it creates (including its log rows) is removed at the end.
Run:  .venv\\Scripts\\python tests\\activity_log_test.py
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


def token_of(c, path):
    return re.search(rb'csrf-token" content="([^"]+)', c.get(path).data).group(1).decode()


def main():
    app = create_app()
    app.config["TESTING"] = True
    sa_email, sa_pw = f"sa_{tag}@test.local", "Test-pass-123"
    staff_email, staff_pw = f"staff_{tag}@test.local", "Staff-pass-123"
    company_id = None
    with app.app_context():
        sa_id = super_admin.create("Log Tester", sa_email, sa_pw)
    c = app.test_client()

    def log_rows(**where):
        sql = "SELECT * FROM ks_activity_log WHERE (user_email IN (%s, %s) OR target IN (%s, %s))"
        params = [sa_email, staff_email, sa_email, staff_email]
        for k, v in where.items():
            sql += f" AND {k} = %s"
            params.append(v)
        with app.app_context():
            return db.fetch_all(sql + " ORDER BY id", params)

    try:
        print("Sign-in")
        token = token_of(c, "/login")
        c.post("/login", data={"_csrf": token, "email": sa_email, "password": "wrong-password-999"})
        bad = log_rows(action="auth.login", status="failed")
        check("failed sign-in recorded with the email typed", len(bad) == 1 and bad[0]["label"] == "Failed sign-in" and bad[0]["user_email"] == sa_email, bad)
        c.post("/login", data={"_csrf": token, "email": sa_email, "password": sa_pw})
        ok = log_rows(action="auth.login", status="ok")
        check("sign-in recorded as the super admin", len(ok) == 1 and ok[0]["role"] == "super" and ok[0]["user_name"] == "Log Tester", ok)
        check("IP address recorded", ok and ok[0]["ip"] == "127.0.0.1", ok and ok[0]["ip"])

        print("Super admin actions")
        token = token_of(c, "/admin/restaurants")
        with app.app_context():
            used = {r["abbreviation"] for r in db.fetch_all("SELECT abbreviation FROM company_detail")}
        abbr = next(a + b for a in "QZXW" for b in "qzxjw" if a + b not in used)
        r = c.post("/admin/restaurants/new", data={"_csrf": token, "name": f"Log Test {tag}", "location": "X",
                                                   "abbreviation": abbr, "status": "open", "default_payments": "1"})
        company_id = int(re.search(r"/admin/restaurants/(\d+)", r.headers["Location"]).group(1))
        reg = log_rows(action="admin.restaurant_new")
        check("restaurant registration recorded with its message", len(reg) == 1 and f"Log Test {tag}" in (reg[0]["details"] or ""), reg)
        r = c.post(f"/admin/restaurants/{company_id}/staff/new", data={"_csrf": token, "name": "Log Staff", "email": staff_email,
                                                                        "password": staff_pw, "user_type": "8", "active": "1"})
        st = log_rows(action="admin.staff_new")
        check("staff login creation recorded", len(st) == 1 and st[0]["company_detail_id"] == company_id, st)
        check("password is never stored", all(staff_pw not in (x["details"] or "") and sa_pw not in (x["details"] or "") for x in log_rows()))
        c.post(f"/admin/restaurants/{company_id}/open", data={"_csrf": token})
        check("entering the restaurant recorded against it", log_rows(action="admin.restaurant_open", company_detail_id=company_id) != [])

        print("Billing inside the restaurant")
        wb = Workbook(); ws = wb.active
        for row in [["Item Name", "Category", "Price", "GST %"], ["TEA", "HOT", 20, 5]]:
            ws.append(row)
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        c.post("/menu/import", data={"_csrf": token, "create_categories": "1", "file": (buf, "m.xlsx")}, content_type="multipart/form-data")
        check("menu import recorded", log_rows(action="menu.import_file", company_detail_id=company_id) != [])
        c.post("/tables/manage", data={"_csrf": token, "area": "AC", "names": "L1"})
        tid = [t for a in c.get("/api/tables").get_json()["areas"] for t in a["tables"]][0]["id"]
        with app.app_context():
            item = db.fetch_value("SELECT M.item_id FROM menu M JOIN menu_category MC USING(menu_category_id) WHERE MC.company_detail_id=%s", (company_id,))
            cash = db.fetch_value("SELECT payment_mode_id FROM bill_payment_mode WHERE company_detail_id=%s AND payment_mode='Cash'", (company_id,))
        H = {"X-CSRF-Token": token}
        c.post(f"/api/tables/{tid}/kot", headers=H, json={"items": [{"item_id": item, "quantity": 2}], "print": True})
        b = c.post(f"/api/tables/{tid}/bill", headers=H, json={"paid": False}).get_json()["bill"]
        c.post(f"/api/tables/{tid}/settle", headers=H, json={"payment_mode_id": cash})
        kot = log_rows(action="tables.kot")
        check("KOT recorded with the table name and item count", kot and kot[0]["target"] == "Table L1" and "items: 1 line(s)" in (kot[0]["details"] or ""), kot)
        settle = log_rows(action="tables.settle")
        check("settle recorded with bill number and amount", settle and f"Bill {b['bill_no']} · ₹42.00" in (settle[0]["details"] or ""), settle)
        r = c.post(f"/api/tables/{tid}/settle", headers=H, json={"payment_mode_id": cash})
        failed = log_rows(action="tables.settle", status="failed")
        check("a refused action is recorded as failed with the reason", failed and "Print the bill" in (failed[0]["details"] or ""), failed)
        c.get("/reports/export?type=daily&range=today")
        check("report export recorded", log_rows(action="reports.export", company_detail_id=company_id) != [])
        c.get("/menu/new")
        check("just opening a form is not recorded", log_rows(action="menu.create") == [])

        print("Activity Log page")
        html = c.get("/admin/activity").get_data(as_text=True)
        check("page opens with today's actions", "Activity Log" in html and "Settled bill" in html and f"Log Test {tag}" in html)
        html = c.get(f"/admin/activity?company_id={company_id}&category=Billing").get_data(as_text=True)
        check("restaurant + area filter", "Settled bill" in html and "Imported menu" not in html)
        html = c.get(f"/admin/activity?q={b['bill_no']}").get_data(as_text=True)
        check("search by bill number", "Settled bill" in html and "Signed in" not in html)
        html = c.get(f"/admin/activity?status=failed&q={tag}").get_data(as_text=True) + c.get(f"/admin/activity?status=failed&q={sa_email}").get_data(as_text=True)
        check("failed filter shows the failed sign-in", "Failed sign-in" in html)
        with app.app_context():
            for i in range(55):   # more than one page (50 per page)
                db.execute("INSERT INTO ks_activity_log (created, user_email, role, company_detail_id, action, category, label, status) "
                           "VALUES (NOW(), %s, 'super', %s, 'menu.toggle', 'Menu', %s, 'ok')", (sa_email, company_id, f"Paging row {i}"))
        r1 = c.get(f"/admin/activity?company_id={company_id}")
        r2 = c.get(f"/admin/activity?company_id={company_id}&page=2")
        check("paging works (page 1 and 2)", r1.status_code == 200 and r2.status_code == 200 and "Showing" in r2.get_data(as_text=True)
              and "page=2" in r1.get_data(as_text=True), (r1.status_code, r2.status_code))
        r = c.get(f"/admin/activity/export?company_id={company_id}")
        ws = load_workbook(io.BytesIO(r.data)).active
        values = [cell.value for row in ws.iter_rows() for cell in row]
        check("Excel export has the actions and no Total row", r.status_code == 200 and "Settled bill" in values and "Total" not in values)

        print("Access")
        c.post("/logout", data={"_csrf": token})
        check("sign-out recorded", log_rows(action="auth.logout") != [])
        s = app.test_client()
        t2 = token_of(s, "/login")
        s.post("/login", data={"_csrf": t2, "email": staff_email, "password": staff_pw})
        check("restaurant staff cannot open the log", s.get("/admin/activity").status_code == 403)
        check("restaurant staff cannot export the log", s.get("/admin/activity/export").status_code == 403)
    finally:
        with app.app_context():
            if company_id:
                for b in db.fetch_all("SELECT bill_id FROM bill WHERE company_detail_id=%s", (company_id,)):
                    db.execute("DELETE FROM bill_detail WHERE bill_id=%s", (b["bill_id"],))
                db.execute("DELETE FROM ks_bill_payment WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM bill WHERE company_detail_id=%s", (company_id,))
                for k in db.fetch_all("SELECT table_order_id FROM table_order WHERE company_detail_id=%s", (company_id,)):
                    db.execute("DELETE FROM ks_kot_ready WHERE table_order_id=%s", (k["table_order_id"],))
                    db.execute("DELETE FROM table_order_detail WHERE table_order_id=%s", (k["table_order_id"],))
                db.execute("DELETE FROM table_order WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE A FROM ks_table_area A JOIN serving_table T USING(serving_table_id) WHERE T.company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM serving_table WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE M FROM menu M JOIN menu_category MC USING(menu_category_id) WHERE MC.company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM menu_category WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM bill_payment_mode WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM aspirant_registration WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM ks_company_modules WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM company_detail WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM ks_activity_log WHERE company_detail_id=%s", (company_id,))
            db.execute("DELETE FROM ks_activity_log WHERE user_email IN (%s, %s) OR target IN (%s, %s)", (sa_email, staff_email, sa_email, staff_email))
            db.execute("DELETE FROM ks_super_admin WHERE id=%s", (sa_id,))
        print("  test restaurant, its data and its log rows were removed")
    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("All checks passed")


if __name__ == "__main__":
    main()
