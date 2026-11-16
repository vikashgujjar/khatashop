"""Restaurant data isolation: restaurant A must not see or change anything of restaurant B.

Creates two throwaway restaurants, gives B one of everything, then tries every page and
action as A's admin. Also checks real data: drivers are separated per restaurant.
Run:  .venv\\Scripts\\python tests\\isolation_test.py
"""
import io
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import menu_item, payment, restaurant, tables  # noqa: E402
from app.security import CSRF_SESSION_KEY  # noqa: E402

failures = []
H = {"X-CSRF-Token": "tok"}


def check(label, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + label + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def client(app, cid, user_type=1, role="staff"):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = {"id": 0, "name": "T", "email": "t", "user_type": user_type, "role": role, "company_id": cid, "company_name": None}
        s[CSRF_SESSION_KEY] = "tok"
    return c


def new_restaurant(tag):
    used = {r["abbreviation"] for r in db.fetch_all("SELECT abbreviation FROM company_detail")}
    abbr = next(a + b for a in "QZXWV" for b in "qzxjwv" if a + b not in used)
    return restaurant.create({"name": f"Iso {tag}", "location": "X", "abbreviation": abbr, "gst_no": "X", "state": None, "address": None,
                              "open_time": None, "close_time": None, "status": "open"})


def main():
    app = create_app()
    app.config["TESTING"] = True
    tag = uuid.uuid4().hex[:5]
    A = B = None
    ids = {}
    try:
        with app.app_context():
            A, B = new_restaurant("A" + tag), new_restaurant("B" + tag)
        b = client(app, B)
        # ---- give restaurant B one of everything
        png = io.BytesIO(); Image.new("RGB", (8, 8), "red").save(png, "PNG"); png.seek(0)
        b.post("/menu-categories/new", data={"_csrf": "tok", "name": "B-Cat", "sort_order": "1", "active": "1", "image": (png, "c.png")},
               content_type="multipart/form-data")
        with app.app_context():
            ids["cat"] = db.fetch_value("SELECT menu_category_id FROM menu_category WHERE company_detail_id=%s", (B,))
        b.post("/menu/new", data={"_csrf": "tok", "name": "B-Item", "category_id": ids["cat"], "rate": "10", "gst": "5", "veg": "1", "active": "1"})
        with app.app_context():
            ids["item"] = db.fetch_value("SELECT item_id FROM menu WHERE menu_category_id=%s", (ids["cat"],))
            payment.add_defaults(B)
            cash = db.fetch_value("SELECT payment_mode_id FROM bill_payment_mode WHERE company_detail_id=%s LIMIT 1", (B,))
            tables.create_tables(B, "", "B1")
            ids["table"] = db.fetch_value("SELECT serving_table_id FROM serving_table WHERE company_detail_id=%s", (B,))
        ids["bill"] = b.post("/api/bills", headers=H, json={"items": [{"item_id": ids["item"], "quantity": 1}], "payment_mode_id": cash}).get_json()["bill_id"]
        kot = b.post(f"/api/tables/{ids['table']}/kot", headers=H, json={"items": [{"item_id": ids["item"], "quantity": 1}], "print": True}).get_json()
        ids["kot"] = kot["state"]["kots"][0]["id"]
        b.post("/m/ingredient_categories/new", data={"_csrf": "tok", "ingredient_category_name": "B-IngCat", "active": "1"})
        with app.app_context():
            ids["ingcat"] = db.fetch_value("SELECT ingredient_category_id FROM ingredient_category WHERE company_detail_id=%s", (B,))
        b.post("/m/ingredients/new", data={"_csrf": "tok", "ingredient_name": "B-Ing", "ingredient_category_id": ids["ingcat"], "ingredient_unit": "KG", "ingredient_gst": "5", "active": "1"})
        b.post("/m/vendors/new", data={"_csrf": "tok", "vendor_name": "B-Vendor", "vendor_contact": "9000000000"})
        with app.app_context():
            ids["ing"] = db.fetch_value("SELECT ingredient_id FROM ingredient WHERE ingredient_category_id=%s", (ids["ingcat"],))
            ids["vendor"] = db.fetch_value("SELECT vendor_id FROM vendor WHERE company_detail_id=%s", (B,))
        b.post("/inventory/procurement/new", data={"_csrf": "tok", "note_date": "2026-11-01", "vendor_id": ids["vendor"],
                                                   "ingredient_id": [ids["ing"]], "quantity": ["1"], "rate": ["5"], "gst": ["5"]})
        b.post(f"/consumption-formula/{ids['item']}", data={"_csrf": "tok", "ingredient_id": [ids["ing"]], "quantity": ["1"]})
        b.post("/staff/new", data={"_csrf": "tok", "name": "B-Staff", "email": f"bstaff_{tag}@test.local", "password": "Staff-1234", "user_type": "2", "active": "1"})
        b.post("/m/drivers/new", data={"_csrf": "tok", "first_name": "B-Driver", "mobile": f"9{uuid.uuid4().int % 10**9:09d}"})
        pdf = io.BytesIO(b"%PDF-1.4\n% test\n"); b.post("/menu-pdf/", data={"_csrf": "tok", "pdf": (pdf, "m.pdf")}, content_type="multipart/form-data")
        with app.app_context():
            ids["note"] = db.fetch_value("SELECT procurement_note_id FROM procurement_note WHERE company_detail_id=%s", (B,))
            ids["staff"] = db.fetch_value("SELECT aspirant_registration_id FROM aspirant_registration WHERE company_detail_id=%s", (B,))
            ids["driver"] = db.fetch_value("SELECT L.driver_id FROM ks_driver_company L WHERE L.company_detail_id=%s", (B,))
        check("restaurant B has one of everything", all(ids.values()), ids)

        # ---- restaurant A tries everything of B
        a = client(app, A)
        print("Pages of B opened by A")
        for url in [f"/bills/{ids['bill']}", f"/tables/{ids['table']}", f"/api/tables/{ids['table']}", f"/tables/kot?ids={ids['kot']}",
                    f"/menu-categories/{ids['cat']}/edit", f"/menu/{ids['item']}/edit", f"/m/ingredient_categories/{ids['ingcat']}",
                    f"/m/ingredients/{ids['ing']}", f"/m/vendors/{ids['vendor']}", f"/inventory/procurement/{ids['note']}",
                    f"/inventory/procurement/{ids['note']}/print", f"/consumption-formula/{ids['item']}", f"/staff/{ids['staff']}",
                    f"/m/drivers/{ids['driver']}", f"/uploads/category_img/{ids['cat']}.jpg", f"/uploads/menu_pdf/{B}.pdf",
                    f"/uploads/logo/{B}.png"]:
            r = a.get(url)
            check(f"A cannot open {url}", r.status_code in (403, 404), r.status_code)
        print("Actions on B's data by A")
        actions = [("post", f"/api/tables/{ids['table']}/kot", {"json": {"items": [{"item_id": ids["item"], "quantity": 1}]}}),
                   ("post", f"/api/tables/{ids['table']}/bill", {"json": {}}),
                   ("post", f"/api/kitchen/{ids['kot']}/ready", {"json": {"ready": True}}),
                   ("post", "/api/bills", {"json": {"items": [{"item_id": ids["item"], "quantity": 1}], "payment_mode_id": cash}}),
                   ("post", f"/menu/{ids['item']}/toggle", {"data": {"_csrf": "tok"}}),
                   ("post", f"/menu-categories/{ids['cat']}/delete", {"data": {"_csrf": "tok"}}),
                   ("post", f"/m/vendors/{ids['vendor']}/delete", {"data": {"_csrf": "tok"}}),
                   ("post", f"/inventory/procurement/{ids['note']}/delete", {"data": {"_csrf": "tok"}}),
                   ("post", f"/m/drivers/{ids['driver']}/delete", {"data": {"_csrf": "tok"}})]
        for method, url, kw in actions:
            r = getattr(a, method)(url, headers=H, **kw)
            check(f"A cannot change via {url}", r.status_code in (403, 404, 422), r.status_code)
        with app.app_context():
            still = db.fetch_one("SELECT (SELECT active FROM menu WHERE item_id=%s) item_active, (SELECT COUNT(*) FROM menu_category WHERE menu_category_id=%s) cat, "
                                 "(SELECT COUNT(*) FROM vendor WHERE vendor_id=%s) vendor, (SELECT COUNT(*) FROM procurement_note WHERE procurement_note_id=%s) note, "
                                 "(SELECT COUNT(*) FROM ks_driver_company WHERE driver_id=%s AND company_detail_id=%s) driver_link",
                                 (ids["item"], ids["cat"], ids["vendor"], ids["note"], ids["driver"], B))
        check("B's data unchanged after A's attempts", still == {"item_active": 1, "cat": 1, "vendor": 1, "note": 1, "driver_link": 1}, still)
        print("Lists of A show nothing of B")
        for url, marker in [("/bills", "B-Item"), ("/menu/", "B-Item"), ("/menu-categories/", "B-Cat"), ("/m/ingredients/", "B-Ing"),
                            ("/m/vendors/", "B-Vendor"), ("/inventory/procurement/", "B-Ing"), ("/staff/", "B-Staff"), ("/m/drivers/", "B-Driver"),
                            ("/kitchen", "B1"), ("/tables", "B1"), ("/reports/?type=items", "B-Item"), ("/consumption-formula/", "B-Item"),
                            ("/bills/new", "B-Item"), ("/menu-pdf/", "menu_pdf/")]:
            body = a.get(url).data.decode()
            check(f"{url} shows nothing of B", marker not in body)
        check("A has no menu PDF of its own", b"No menu PDF has been uploaded yet" in a.get("/menu-pdf/").data)
        check("B still opens its own files", b.get(f"/uploads/category_img/{ids['cat']}.jpg").status_code == 200 and b.get(f"/uploads/menu_pdf/{B}.pdf").status_code == 200)

        print("App-wide settings are read-only for restaurants")
        for url in ["/m/discounts/", "/m/delivery_charges/"]:
            r = a.get(url, follow_redirects=True)
            check(f"{url} viewable, no save button", r.status_code == 200 and b"View only" in r.data or b"Add " not in r.data, r.status_code)
        r = a.post("/m/delivery_charges/new", data={"_csrf": "tok", "from_km": "0", "to_km": "1", "charges": "999"})
        check("restaurant cannot add a delivery charge", r.status_code == 403)
        with app.app_context():
            disc = db.fetch_value("SELECT id FROM discount LIMIT 1")
        r = a.post(f"/m/discounts/{disc}", data={"_csrf": "tok", "percentage": "99", "first_order_enable": "1"})
        check("restaurant cannot change the app discount", r.status_code == 403)

        print("Real data: drivers per restaurant")
        def driver_names(cid):
            return {r["name"] for r in __import__("app.models.online_orders", fromlist=["x"]).drivers(cid)}
        with app.test_request_context():
            mm, ogi, sg = driver_names(2), driver_names(8), driver_names(1)
        check("M. M. Continental sees its delivery drivers", "Gaurav Kumar" in mm, mm)
        check("Ogi kailash Parvatam sees only drivers who delivered for it", "Gaurav Kumar" not in ogi, ogi)
        check("Spiced Garden (no online orders) sees no drivers", not sg, sg)
    finally:
        with app.app_context():
            for cid in (A, B):
                if not cid:
                    continue
                for bid in [r["bill_id"] for r in db.fetch_all("SELECT bill_id FROM bill WHERE company_detail_id=%s", (cid,))]:
                    db.execute("DELETE FROM bill_detail WHERE bill_id=%s", (bid,))
                db.execute("DELETE FROM bill WHERE company_detail_id=%s", (cid,))
                for k in db.fetch_all("SELECT table_order_id FROM table_order WHERE company_detail_id=%s", (cid,)):
                    db.execute("DELETE FROM ks_kot_ready WHERE table_order_id=%s", (k["table_order_id"],))
                    db.execute("DELETE FROM table_order_detail WHERE table_order_id=%s", (k["table_order_id"],))
                db.execute("DELETE FROM table_order WHERE company_detail_id=%s", (cid,))
                db.execute("DELETE A FROM ks_table_area A JOIN serving_table T USING(serving_table_id) WHERE T.company_detail_id=%s", (cid,))
                db.execute("DELETE FROM serving_table WHERE company_detail_id=%s", (cid,))
                db.execute("DELETE D FROM procurement_note_detail D JOIN procurement_note N USING(procurement_note_id) WHERE N.company_detail_id=%s", (cid,))
                db.execute("DELETE FROM procurement_note WHERE company_detail_id=%s", (cid,))
                db.execute("DELETE F FROM consumption_formula F JOIN menu M USING(item_id) JOIN menu_category C USING(menu_category_id) WHERE C.company_detail_id=%s", (cid,))
                db.execute("DELETE M FROM menu M JOIN menu_category C USING(menu_category_id) WHERE C.company_detail_id=%s", (cid,))
                db.execute("DELETE FROM menu_category WHERE company_detail_id=%s", (cid,))
                db.execute("DELETE I FROM ingredient I JOIN ingredient_category C USING(ingredient_category_id) WHERE C.company_detail_id=%s", (cid,))
                drivers = [r["driver_id"] for r in db.fetch_all("SELECT driver_id FROM ks_driver_company WHERE company_detail_id=%s", (cid,))]
                db.execute("DELETE FROM ks_driver_company WHERE company_detail_id=%s", (cid,))
                for d in drivers:
                    if not db.fetch_value("SELECT COUNT(*) AS c FROM ks_driver_company WHERE driver_id=%s", (d,), 0):
                        db.execute("DELETE FROM drivers WHERE id=%s AND first_name='B-Driver'", (d,))
                for t in ("ingredient_category", "vendor", "bill_payment_mode", "aspirant_registration"):
                    db.execute(f"DELETE FROM {t} WHERE company_detail_id=%s", (cid,))
                db.execute("DELETE FROM company_detail WHERE company_detail_id=%s", (cid,))
                with app.test_request_context():
                    from app.services import uploads
                    for p in (uploads.menu_pdf_path(cid), uploads.base_dir() / "category_img" / f"{ids.get('cat')}.jpg"):
                        try:
                            p.unlink(missing_ok=True)
                        except OSError:
                            pass
        print("  both throwaway restaurants and their data/files removed")
    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("All checks passed")


if __name__ == "__main__":
    main()
