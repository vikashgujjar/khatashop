"""End-to-end test of the converted admin modules: roles, kitchen, inventory, recipes,
online orders, staff, attendance, master pages and every sidebar link.

Writes only inside a throwaway restaurant (removed at the end). Shared tables used by the
customer/driver apps (drivers, discount, delivery charges, menu revisions) are only read.
Run:  .venv\\Scripts\\python tests\\modules_smoke_test.py
"""
import json
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app, db  # noqa: E402
from app.models import menu_item, payment, restaurant, tables  # noqa: E402
from app.security import CSRF_SESSION_KEY  # noqa: E402

failures = []


def check(label, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + label + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def client_for(app, company_id, user_type, uid=0, name="Tester"):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = {"id": uid, "name": name, "email": "t@test.local", "user_type": user_type, "role": "staff",
                     "company_id": company_id, "company_name": None}
        s[CSRF_SESSION_KEY] = "tok"
    return c


H = {"X-CSRF-Token": "tok"}


def main():
    app = create_app()
    app.config["TESTING"] = True
    tag = uuid.uuid4().hex[:5]
    cid, order_id = None, None
    with app.app_context():
        used = {r["abbreviation"] for r in db.fetch_all("SELECT abbreviation FROM company_detail")}
        abbr = next(a + b for a in "QZXW" for b in "qzxjw" if a + b not in used)
        cid = restaurant.create({"name": f"Module Test {tag}", "location": "X", "abbreviation": abbr, "gst_no": "X", "state": None,
                                 "address": None, "open_time": None, "close_time": None, "status": "open"})
    try:
        admin = client_for(app, cid, 1)
        print("Every sidebar page (outlet admin)")
        page = admin.get("/dashboard").data.decode()
        links = sorted(set(re.findall(r'class="nav-link[^"]*" href="([^"]+)"', page)))
        check("sidebar has all module links", len(links) >= 20, len(links))
        for url in links:
            r = admin.get(url, follow_redirects=True)
            check(f"GET {url}", r.status_code == 200 and b"built-in method" not in r.data, r.status_code)
        for url in ["/m/ingredients/new", "/m/vendors/new", "/m/drivers/new", "/inventory/procurement/new", "/inventory/issuing/new",
                    "/staff/new", "/attendance/?month=2025-04", "/online-orders/item-summary"]:
            r = admin.get(url)
            check(f"GET {url}", r.status_code == 200 and b"built-in method" not in r.data, r.status_code)

        print("Roles")
        rules = [(4, "/tables", 200), (4, "/bills", 403), (4, "/menu/", 403), (5, "/kitchen", 200), (5, "/m/vendors/", 403),
                 (3, "/inventory/procurement/", 200), (3, "/bills/new", 403), (6, "/online-orders/", 200), (6, "/m/drivers/", 200),
                 (6, "/inventory/procurement/", 403), (2, "/bills/new", 200), (2, "/inventory/issuing/", 200), (2, "/m/ingredients/", 403),
                 (0, "/attendance/", 200), (0, "/tables", 403)]
        for ut, url, want in rules:
            r = client_for(app, cid, ut).get(url)
            check(f"user_type {ut} {url} -> {want}", r.status_code == want, r.status_code)
        waiter_nav = client_for(app, cid, 4).get("/tables").data
        check("waiter sees no Generate Bill link", b"Generate Bill" not in waiter_nav and b"Quick Bill" not in waiter_nav)

        print("Inventory masters")
        r = admin.post("/m/ingredient_categories/new", data={"_csrf": "tok", "ingredient_category_name": "", "active": "1"})
        check("empty category rejected", r.status_code == 422)
        admin.post("/m/ingredient_categories/new", data={"_csrf": "tok", "ingredient_category_name": "Dairy", "active": "1"})
        r = admin.post("/m/ingredient_categories/new", data={"_csrf": "tok", "ingredient_category_name": "Dairy", "active": "1"})
        check("duplicate category rejected", r.status_code == 422 and b"already exists" in r.data)
        with app.app_context():
            cat = db.fetch_value("SELECT ingredient_category_id FROM ingredient_category WHERE company_detail_id=%s", (cid,))
        admin.post("/m/ingredients/new", data={"_csrf": "tok", "ingredient_name": "MILK", "ingredient_category_id": cat, "ingredient_unit": "LTR", "ingredient_gst": "5", "active": "1"})
        admin.post("/m/ingredients/new", data={"_csrf": "tok", "ingredient_name": "TEA LEAF", "ingredient_category_id": cat, "ingredient_unit": "KG", "ingredient_gst": "5", "active": "1"})
        r = admin.post("/m/ingredients/new", data={"_csrf": "tok", "ingredient_name": "X", "ingredient_category_id": "999999", "ingredient_unit": "KG"})
        check("ingredient with another restaurant's category rejected", r.status_code == 422)
        r = admin.post("/m/vendors/new", data={"_csrf": "tok", "vendor_name": "Amul Agency", "vendor_contact": "98765 43210", "vendor_email": "bad"})
        check("vendor with bad email rejected", r.status_code == 422 and b"valid email" in r.data)
        admin.post("/m/vendors/new", data={"_csrf": "tok", "vendor_name": "Amul Agency", "vendor_contact": "9876543210", "vendor_city": "Ambala"})
        with app.app_context():
            ing = {r["ingredient_name"]: r["ingredient_id"] for r in db.fetch_all(
                "SELECT I.ingredient_id, I.ingredient_name FROM ingredient I JOIN ingredient_category C USING(ingredient_category_id) WHERE C.company_detail_id=%s", (cid,))}
            vendor = db.fetch_value("SELECT vendor_id FROM vendor WHERE company_detail_id=%s", (cid,))
        check("2 ingredients + vendor created", len(ing) == 2 and vendor)
        r = admin.post(f"/m/ingredient_categories/{cat}/delete", data={"_csrf": "tok"}, follow_redirects=True)
        check("category with ingredients cannot be deleted", b"Move or delete them first" in r.data)

        print("Procurement / issuing / consumption notes")
        r = admin.post("/inventory/procurement/new", data={"_csrf": "tok", "note_date": "2026-11-10", "vendor_id": vendor,
                       "ingredient_id": [ing["MILK"], ing["TEA LEAF"]], "quantity": ["20", "2.5"], "rate": ["50", "400"], "gst": ["5", "5"]})
        check("fractional quantity refused on procurement (whole numbers in old schema)", r.status_code == 422 and b"whole numbers" in r.data)
        r = admin.post("/inventory/procurement/new", data={"_csrf": "tok", "note_date": "2026-11-10", "vendor_id": vendor, "invoice_no": "INV-7",
                       "payment_status": "1", "ingredient_id": [ing["MILK"], ing["TEA LEAF"]], "quantity": ["20", "2"], "rate": ["50", "400"], "gst": ["5", ""]})
        check("procurement saved", r.status_code == 302)
        with app.app_context():
            p = db.fetch_one("SELECT procurement_note_id AS id, procurement_ref_no AS ref, total, cgst, sgst, total_quantity FROM procurement_note WHERE company_detail_id=%s", (cid,))
        check("procurement totals: (1000 + 800) + 5% = 1890, ref continues prefix", p and float(p["total"]) == 1890.0
              and float(p["cgst"]) == 45.0 and p["ref"] == f"{abbr}01" and p["total_quantity"] == "22", p)
        doc = admin.get(f"/inventory/procurement/{p['id']}/print").data
        check("procurement print shows vendor, invoice and total", b"Amul Agency" in doc and b"INV-7" in doc and b"1,890.00" in doc)
        admin.post("/inventory/issuing/new", data={"_csrf": "tok", "note_date": "2026-11-11", "ingredient_id": [ing["MILK"]], "quantity": ["7.5"]})
        r = admin.post("/inventory/consumption/new", data={"_csrf": "tok", "note_date": "2026-11-11", "ingredient_id": [""], "quantity": [""]})
        check("empty consumption note refused", r.status_code == 422 and b"at least one" in r.data)
        admin.post("/inventory/consumption/new", data={"_csrf": "tok", "note_date": "2026-11-11", "ingredient_id": [ing["TEA LEAF"]], "quantity": ["0.125"]})
        with app.app_context():
            iss = db.fetch_one("SELECT issuing_note_id AS id, issuing_ref_no AS ref FROM issuing_note WHERE company_detail_id=%s", (cid,))
            cons = db.fetch_value("SELECT COUNT(*) AS c FROM consumption_note WHERE company_detail_id=%s", (cid,))
        check("issuing + consumption saved", iss and cons == 1)
        r = admin.post(f"/inventory/issuing/{iss['id']}", data={"_csrf": "tok", "note_date": "2026-11-11", "ingredient_id": [ing["MILK"]], "quantity": ["8"]})
        check("issuing note edited, keeps its ref", r.status_code == 302 and admin.get("/inventory/issuing/").data.count(iss["ref"].encode()) >= 1)
        stock = admin.get("/reports/?type=ingredient_stock&date_from=2026-11-01&date_to=2026-11-30").data.decode()
        check("stock report: MILK purchased 20, issued 8, balance 12", re.search(r"MILK.*?>20<.*?>8<.*?>12<", stock, re.S) is not None)

        print("Recipes -> item consumption")
        with app.app_context():
            menu_item.import_rows(cid, [{"_row": 2, "name": "CHAI", "category": "HOT", "rate": "20", "gst": "5", "veg": "", "status": "", "description": ""}], create_categories=True)
            chai = db.fetch_value("SELECT M.item_id FROM menu M JOIN menu_category C USING(menu_category_id) WHERE C.company_detail_id=%s", (cid,))
            payment.add_defaults(cid)
            cash = db.fetch_value("SELECT payment_mode_id FROM bill_payment_mode WHERE company_detail_id=%s AND payment_mode='Cash'", (cid,))
        r = admin.post(f"/consumption-formula/{chai}", data={"_csrf": "tok", "ingredient_id": [ing["MILK"], ing["TEA LEAF"]], "quantity": ["0.15", "0.005"]})
        check("recipe saved", r.status_code == 302 and b"MILK" in admin.get("/consumption-formula/").data)
        counter = client_for(app, cid, 2)
        counter.post("/api/bills", headers=H, json={"items": [{"item_id": chai, "quantity": 10}], "payment_mode_id": cash})
        cons_page = admin.get("/reports/?type=item_consumption").data.decode()
        check("10 chai -> 1.5 LTR milk + 0.05 KG tea", re.search(r"MILK.*?>1\.5<", cons_page, re.S) and re.search(r"TEA LEAF.*?>0\.05<", cons_page, re.S))

        print("Kitchen")
        with app.app_context():
            tables.create_tables(cid, "AC", "K1")
            t1 = db.fetch_value("SELECT serving_table_id FROM serving_table WHERE company_detail_id=%s", (cid,))
        waiter = client_for(app, cid, 4, uid=0)
        r = waiter.post(f"/api/tables/{t1}/kot", headers=H, json={"items": [{"item_id": chai, "quantity": 2, "note": "kadak"}], "print": True})
        check("waiter sends KOT", r.status_code == 200)
        check("waiter cannot bill", waiter.post(f"/api/tables/{t1}/bill", headers=H, json={}).status_code == 403)
        kitchen = client_for(app, cid, 5)
        board = kitchen.get("/api/kitchen").get_json()["board"]
        check("kitchen sees the KOT with note", len(board["waiting"]) == 1 and board["waiting"][0]["lines"][0]["note"] == "kadak", board)
        kid = board["waiting"][0]["id"]
        tile = lambda: next(t for a in admin.get("/api/tables").get_json()["areas"] for t in a["tables"] if t["id"] == t1)
        check("table is yellow (KOT sent) while the kitchen prepares", tile()["status"] == "kot", tile())
        board = kitchen.post(f"/api/kitchen/{kid}/ready", headers=H, json={"ready": True}).get_json()["board"]
        check("marked ready", not board["waiting"] and len(board["ready"]) == 1)
        check("table turns green (Food ready) on Table View", tile()["status"] == "ready", tile())
        kitchen.post(f"/api/kitchen/{kid}/ready", headers=H, json={"ready": False})
        check("undo in the kitchen turns it back to yellow", tile()["status"] == "kot")
        kitchen.post(f"/api/kitchen/{kid}/ready", headers=H, json={"ready": True})
        st = admin.get(f"/api/tables/{t1}").get_json()["state"]
        check("table screen shows KOT as ready", st["kots"][0]["ready"] is True)

        print("Online orders (test order)")
        with app.app_context():
            now_s = db.fetch_value("SELECT NOW() AS n")
            _, order_id = db.execute(
                "INSERT INTO orders (uid, vendor_id, date_time, paid_method, order_to, orders, notes, address, total, tax, discount, delivery_charge, "
                "grand_total, status, isReady, is_confirmed, payment_status, created_at, updated_at, mobile, name) "
                "VALUES (0, %s, %s, 'online', 'home', '', %s, 0, 100, 5, 0, 0, 105, %s, 0, 0, 1, %s, %s, '9999999999', 'Test Customer')",
                (cid, now_s, json.dumps([{"status": 1, "value": "Order Created", "time": "x"}]), json.dumps([{"id": "1", "status": "pending"}]), now_s, now_s))
            db.execute("INSERT INTO order_items (order_id, item_id, item_name, quantity, rate, item_gst, total, created_at, updated_at) "
                       "VALUES (%s, %s, 'CHAI', 5, 20, 5, 100, %s, %s)", (order_id, chai, now_s, now_s))
            driver = db.fetch_value("SELECT id FROM drivers ORDER BY id LIMIT 1")
            from app.models import driver_links
            driver_links.link(driver, cid)  # drivers belong to restaurants; attach one to the test restaurant
        page = admin.get("/online-orders/").data
        check("order card shows customer, item and final amount", b"Test Customer" in page and b"CHAI" in page and b"105.00" in page)
        r = admin.post(f"/online-orders/{order_id}/update", headers=H, json={"status": "prepared", "driver_id": driver})
        o = r.get_json()["order"]
        check("status -> prepared sets Ready and assigns driver", o["status_key"] == "prepared" and o["isReady"] == 1 and o["driver_name"])
        with app.app_context():
            row = db.fetch_one("SELECT status, notes, driver_id FROM orders WHERE order_id=%s", (order_id,))
        check("written in the PHP format", json.loads(row["status"]) == [{"id": "1", "status": "prepared"}]
              and json.loads(row["notes"])[-2]["value"] == "Order prepared" and row["driver_id"] == driver, row)
        r = admin.post(f"/online-orders/{order_id}/update", headers=H, json={"status": "teleported"})
        check("unknown status refused", r.status_code == 422)
        admin.post(f"/online-orders/{order_id}/update", headers=H, json={"status": "delivered"})
        summary = admin.get("/online-orders/item-summary").data
        check("item summary counts delivered order", re.search(rb"CHAI</td><td class=\"num\"><strong>5<", summary) is not None)
        check("order print", b"ONLINE ORDER #" in admin.get(f"/online-orders/{order_id}/print").data)
        other = client_for(app, 2, 1)
        check("another restaurant cannot change this order", other.post(f"/online-orders/{order_id}/update", headers=H, json={"status": "cancelled"}).status_code == 404)

        print("Staff + attendance")
        r = admin.post("/staff/new", data={"_csrf": "tok", "name": "Ravi Waiter", "email": f"ravi_{tag}@test.local", "password": "Waiter-123",
                                           "user_type": "4", "employee_id": "abc", "active": "1"})
        check("bad employee id rejected", r.status_code == 422)
        r = admin.post("/staff/new", data={"_csrf": "tok", "name": "Ravi Waiter", "email": f"ravi_{tag}@test.local", "password": "Waiter-123",
                                           "user_type": "4", "employee_id": "777", "active": "1"})
        check("waiter login created", r.status_code == 302 and b"Ravi Waiter" in admin.get("/staff/?role=4").data)
        att = admin.get("/attendance/?month=2026-11")
        check("attendance lists staff with employee id", b"Ravi Waiter" in att.data and b"#777" in att.data)
        check("attendance Excel", admin.get("/attendance/?month=2026-11&export=1").data[:2] == b"PK")
        real = client_for(app, 1, 1).get("/attendance/?month=2025-04")
        check("real punches render (restaurant 1, Apr 2025)", real.status_code == 200)
    finally:
        with app.app_context():
            db.execute("DELETE FROM ks_driver_company WHERE company_detail_id=%s", (cid,))
            if order_id:
                db.execute("DELETE FROM order_items WHERE order_id=%s", (order_id,))
                db.execute("DELETE FROM orders WHERE order_id=%s", (order_id,))
            for nt, det, pk in [("procurement_note", "procurement_note_detail", "procurement_note_id"), ("issuing_note", "issuing_note_detail", "issuing_note_id"),
                                ("consumption_note", "consumption_note_detail", "consumption_note_id")]:
                db.execute(f"DELETE D FROM {det} D JOIN {nt} N USING({pk}) WHERE N.company_detail_id=%s", (cid,))
                db.execute(f"DELETE FROM {nt} WHERE company_detail_id=%s", (cid,))
            db.execute("DELETE F FROM consumption_formula F JOIN menu M USING(item_id) JOIN menu_category C USING(menu_category_id) WHERE C.company_detail_id=%s", (cid,))
            for b in db.fetch_all("SELECT bill_id FROM bill WHERE company_detail_id=%s", (cid,)):
                db.execute("DELETE FROM bill_detail WHERE bill_id=%s", (b["bill_id"],))
            db.execute("DELETE FROM bill WHERE company_detail_id=%s", (cid,))
            for k in db.fetch_all("SELECT table_order_id FROM table_order WHERE company_detail_id=%s", (cid,)):
                for t in ("ks_kot_note N JOIN table_order_detail D USING(table_order_detail_id)",):
                    db.execute(f"DELETE N FROM {t} WHERE D.table_order_id=%s", (k["table_order_id"],))
                db.execute("DELETE FROM ks_kot_ready WHERE table_order_id=%s", (k["table_order_id"],))
                db.execute("DELETE FROM table_order_detail WHERE table_order_id=%s", (k["table_order_id"],))
            db.execute("DELETE FROM table_order WHERE company_detail_id=%s", (cid,))
            db.execute("DELETE A FROM ks_table_area A JOIN serving_table T USING(serving_table_id) WHERE T.company_detail_id=%s", (cid,))
            db.execute("DELETE FROM serving_table WHERE company_detail_id=%s", (cid,))
            db.execute("DELETE M FROM menu M JOIN menu_category C USING(menu_category_id) WHERE C.company_detail_id=%s", (cid,))
            db.execute("DELETE FROM menu_category WHERE company_detail_id=%s", (cid,))
            db.execute("DELETE I FROM ingredient I JOIN ingredient_category C USING(ingredient_category_id) WHERE C.company_detail_id=%s", (cid,))
            for t in ("ingredient_category", "vendor", "bill_payment_mode", "aspirant_registration"):
                db.execute(f"DELETE FROM {t} WHERE company_detail_id=%s", (cid,))
            db.execute("DELETE FROM company_detail WHERE company_detail_id=%s", (cid,))
        print("  throwaway restaurant and everything it created were removed")
    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("All checks passed")


if __name__ == "__main__":
    main()
