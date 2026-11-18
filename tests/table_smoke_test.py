"""End-to-end test of table service: Table View, KOTs, bill print/reprint, settle, move, cancel.

Runs inside a throwaway restaurant that is removed again at the end.
Run:  .venv\\Scripts\\python tests\\table_smoke_test.py
"""
import io
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openpyxl import Workbook  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import super_admin  # noqa: E402

failures = []
tag = uuid.uuid4().hex[:6]


def check(label, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + label + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def main():
    app = create_app()
    app.config["TESTING"] = True
    sa_email, sa_pw = f"sa_{tag}@test.local", "Test-pass-123"
    company_id = None
    with app.app_context():
        sa_id = super_admin.create("Table Tester", sa_email, sa_pw)
    c = app.test_client()
    try:
        token = re.search(rb'csrf-token" content="([^"]+)', c.get("/login").data).group(1).decode()
        c.post("/login", data={"_csrf": token, "email": sa_email, "password": sa_pw})
        # signing in rotates the CSRF token
        token = re.search(rb'csrf-token" content="([^"]+)', c.get("/admin/restaurants").data).group(1).decode()
        with app.app_context():
            used = {r["abbreviation"] for r in db.fetch_all("SELECT abbreviation FROM company_detail")}
        abbr = next(a + b for a in "QZXW" for b in "qzxjw" if a + b not in used)
        r = c.post("/admin/restaurants/new", data={"_csrf": token, "name": f"Table Test {tag}", "location": "X",
                                                   "abbreviation": abbr, "status": "open", "default_payments": "1"})
        company_id = int(re.search(r"/admin/restaurants/(\d+)", r.headers["Location"]).group(1))
        c.post(f"/admin/restaurants/{company_id}/open", data={"_csrf": token})
        wb = Workbook(); ws = wb.active
        for row in [["Item Name", "Category", "Price", "GST %"], ["TEA", "HOT", 20, 5], ["POHA", "SNACKS", 100, 5], ["COFFEE", "HOT", 50, 5]]:
            ws.append(row)
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        c.post("/menu/import", data={"_csrf": token, "create_categories": "1", "file": (buf, "m.xlsx")}, content_type="multipart/form-data")
        with app.app_context():
            items = {r["item_name"]: r["item_id"] for r in db.fetch_all(
                "SELECT M.item_id, M.item_name FROM menu M JOIN menu_category MC USING(menu_category_id) WHERE MC.company_detail_id=%s", (company_id,))}
            cash = db.fetch_value("SELECT payment_mode_id FROM bill_payment_mode WHERE company_detail_id=%s AND payment_mode='Cash'", (company_id,))
        H = {"X-CSRF-Token": token}

        print("Tables")
        check("empty table view explains setup", b"No tables set up yet" in c.get("/tables").data)
        r = c.post("/tables/manage", data={"_csrf": token, "area": "AC", "names": "AC1-AC3"})
        check("range AC1-AC3 added", r.status_code == 302)
        c.post("/tables/manage", data={"_csrf": token, "area": "", "names": "1, 2"})
        r = c.post("/tables/manage", data={"_csrf": token, "area": "AC", "names": "AC2"})
        check("duplicate table rejected", r.status_code == 422 and b"already exist" in r.data)
        board = c.get("/api/tables").get_json()["areas"]
        check("areas AC then Other, natural order", [a["name"] for a in board] == ["AC", "Other"] and
              [t["name"] for t in board[0]["tables"]] == ["AC1", "AC2", "AC3"], board)
        tid = {t["name"]: t["id"] for a in board for t in a["tables"]}

        def status(name):
            for a in c.get("/api/tables").get_json()["areas"]:
                for t in a["tables"]:
                    if t["name"] == name:
                        return t
        print("Running order on AC1")
        base = f"/api/tables/{tid['AC1']}"
        check("table screen opens", c.get(f"/tables/{tid['AC1']}").status_code == 200)
        r = c.post(base + "/kot", headers=H, json={"items": [{"item_id": items["TEA"], "quantity": 2, "note": "less sugar"}], "print": False})
        check("save without KOT -> running", r.status_code == 200 and status("AC1")["status"] == "running", r.get_json())
        r = c.post(base + "/kot", headers=H, json={"items": [{"item_id": items["POHA"], "quantity": 1}], "print": True})
        body = r.get_json()
        check("KOT & Print prints the saved + new KOT", body["print_url"] and body["state"]["status"] == "kot"
              and len(body["state"]["kots"]) == 2 and all(k["printed"] for k in body["state"]["kots"]), body)
        kot_page = c.get(body["print_url"]).data
        check("KOT ticket shows table, items and note, no prices", b"Table AC1" in kot_page and b"less sugar" in kot_page and b"POHA" in kot_page and b"\xe2\x82\xb9" not in kot_page)
        r = c.post(base + "/kot", headers=H, json={"items": [], "print": True})
        check("nothing new to send", r.status_code == 422)
        t = status("AC1")
        check("board shows amount incl. GST (2x20 + 100) x 1.05 = 147", abs(t["amount"] - 147.0) < 0.01 and t["minutes"] == 0, t)

        print("Bill, reprint, settle")
        r = c.post(base + "/bill", headers=H, json={"paid": False, "customer_name": "Ravi"})
        b1 = r.get_json()
        check("bill printed, table green", r.status_code == 200 and b1["state"]["status"] == "printed" and b1["bill"]["total"] == 147.0, b1)
        check("receipt opens", c.get(b1["print_url"]).status_code == 200)
        check("unpaid bill hidden from Bill Detail", b1["bill"]["bill_no"].encode() not in c.get("/bills").data)
        r = c.post(base + "/settle", headers=H, json={"payment_mode_id": cash})
        # add more after print instead: first undo by checking reprint path on another table below
        check("settle frees the table", r.status_code == 200 and status("AC1")["status"] == "blank")
        check("settled bill appears in Bill Detail", b1["bill"]["bill_no"].encode() in c.get("/bills").data)

        print("Reprint after adding items (AC2)")
        base2 = f"/api/tables/{tid['AC2']}"
        c.post(base2 + "/kot", headers=H, json={"items": [{"item_id": items["COFFEE"], "quantity": 1}], "print": True})
        b2 = c.post(base2 + "/bill", headers=H, json={}).get_json()["bill"]
        r = c.post(base + "/kot", headers=H, json={"items": [{"item_id": items["TEA"], "quantity": 1}], "print": True})  # AC1 again
        quick = c.post("/api/bills", headers=H, json={"items": [{"item_id": items["TEA"], "quantity": 1}], "payment_mode_id": cash}).get_json()
        c.post(base2 + "/kot", headers=H, json={"items": [{"item_id": items["TEA"], "quantity": 2}], "print": True})
        check("items after print -> running again", status("AC2")["status"] != "printed")
        r = c.post(base2 + "/settle", headers=H, json={"payment_mode_id": cash})
        check("settle blocked until reprint", r.status_code == 422 and "again" in r.get_json()["error"])
        b2b = c.post(base2 + "/bill", headers=H, json={"paid": True, "payment_mode_id": cash}).get_json()["bill"]
        check("reprint keeps bill number, new total 94.50 -> 95", b2b["bill_no"] == b2["bill_no"] and b2b["total"] == 95.0 and b2b["settled"], (b2, b2b))
        with app.app_context():
            nos = [r["bill_no"] for r in db.fetch_all("SELECT bill_no FROM bill WHERE company_detail_id=%s ORDER BY bill_id", (company_id,))]
        check("no duplicate bill numbers across table + quick bills", len(nos) == len(set(nos)) and quick["bill_no"] in nos, nos)

        print("Void, move, cancel")
        st = c.get(base).get_json()["state"]  # AC1 has 1 TEA KOT running
        line_id = st["kots"][0]["lines"][0]["id"]
        r = c.post(base + "/void", headers=H, json={"line_id": line_id})
        check("removing the last item frees the table", r.get_json()["state"]["status"] == "blank")
        c.post(base + "/kot", headers=H, json={"items": [{"item_id": items["POHA"], "quantity": 1}], "print": True})
        r = c.post(base + "/move", headers=H, json={"to_table_id": tid["AC2"]})
        check("move to free table", r.status_code == 200 and status("AC1")["status"] == "blank" and status("AC2")["status"] == "kot", r.get_json())
        c.post(f"/api/tables/{tid['1']}/kot", headers=H, json={"items": [{"item_id": items["TEA"], "quantity": 1}], "print": False})
        r = c.post(f"/api/tables/{tid['AC2']}/move", headers=H, json={"to_table_id": tid["1"]})
        check("move to busy table refused", r.status_code == 422)
        c.post(f"/api/tables/{tid['AC2']}/bill", headers=H, json={})
        r = c.post(f"/api/tables/{tid['AC2']}/cancel", headers=H, json={})
        check("cancel clears table and unpaid bill", r.status_code == 200 and status("AC2")["status"] == "blank")
        r = c.post(f"/api/tables/{tid['AC3']}/kot", headers=H, json={"items": [{"item_id": 999999, "quantity": 1}]})
        check("unknown item refused", r.status_code == 422)
        r = c.post(f"/api/tables/{tid['AC3']}/kot", headers=H, json={"items": [{"item_id": items["TEA"], "quantity": 0}]})
        check("zero quantity refused", r.status_code == 422)
        check("other restaurant's table -> 404", c.get("/api/tables/999999").status_code == 404)

        print("Manage + reports")
        r = c.post(f"/tables/manage/{tid['1']}", data={"_csrf": token, "name": "1", "area": "", "active": "0"})
        check("cannot switch off a table with a running order", b"running order" in c.get("/tables/manage").data)
        r = c.post(f"/tables/manage/{tid['AC3']}/delete", data={"_csrf": token})
        check("unused table deleted", "AC3" not in str(c.get("/api/tables").get_json()))
        check("used table cannot be deleted", c.post(f"/tables/manage/{tid['AC1']}/delete", data={"_csrf": token}).status_code == 302
              and "AC1" in str(c.get("/api/tables").get_json()))
        daily = c.get("/reports/?type=daily").data
        check("reports include only settled bills (147 + 95 + 21)", b"263.00" in daily)
    finally:
        with app.app_context():
            if company_id:
                for b in db.fetch_all("SELECT bill_id FROM bill WHERE company_detail_id=%s", (company_id,)):
                    db.execute("DELETE FROM bill_detail WHERE bill_id=%s", (b["bill_id"],))
                db.execute("DELETE FROM bill WHERE company_detail_id=%s", (company_id,))
                for k in db.fetch_all("SELECT table_order_id FROM table_order WHERE company_detail_id=%s", (company_id,)):
                    db.execute("DELETE N FROM ks_kot_note N JOIN table_order_detail D USING(table_order_detail_id) WHERE D.table_order_id=%s", (k["table_order_id"],))
                    db.execute("DELETE FROM table_order_detail WHERE table_order_id=%s", (k["table_order_id"],))
                db.execute("DELETE FROM table_order WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE A FROM ks_table_area A JOIN serving_table T USING(serving_table_id) WHERE T.company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM serving_table WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE M FROM menu M JOIN menu_category MC USING(menu_category_id) WHERE MC.company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM menu_category WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM bill_payment_mode WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM company_detail WHERE company_detail_id=%s", (company_id,))
            db.execute("DELETE FROM ks_super_admin WHERE id=%s", (sa_id,))
        print("  test restaurant and all its tables, orders and bills were removed")
    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("All checks passed")


if __name__ == "__main__":
    main()
