"""End-to-end test of part payment (one bill paid by several modes).

Covers the table screen (take amounts, remove one, settle the rest, settle when fully paid,
cancel with amounts taken), Generate Bill with a split, the bill list / receipt, and the
payment-mode reports. Runs inside a throwaway restaurant that is removed again at the end.
Run:  .venv\\Scripts\\python tests\\part_payment_test.py
"""
from __future__ import annotations
import io
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.xlsx import Workbook  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models import dashboard, report, super_admin  # noqa: E402
from app.utils import today  # noqa: E402

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
        sa_id = super_admin.create("Part Pay Tester", sa_email, sa_pw)
    c = app.test_client()
    try:
        token = re.search(rb'csrf-token" content="([^"]+)', c.get("/login").data).group(1).decode()
        c.post("/login", data={"_csrf": token, "email": sa_email, "password": sa_pw})
        token = re.search(rb'csrf-token" content="([^"]+)', c.get("/admin/restaurants").data).group(1).decode()
        with app.app_context():
            used = {r["abbreviation"] for r in db.fetch_all("SELECT abbreviation FROM company_detail")}
        abbr = next(a + b for a in "QZXW" for b in "qzxjw" if a + b not in used)
        r = c.post("/admin/restaurants/new", data={"_csrf": token, "name": f"Part Pay Test {tag}", "location": "X",
                                                   "abbreviation": abbr, "status": "open", "default_payments": "1"})
        company_id = int(re.search(r"/admin/restaurants/(\d+)", r.headers["Location"]).group(1))
        c.post(f"/admin/restaurants/{company_id}/open", data={"_csrf": token})
        wb = Workbook(); ws = wb.active
        for row in [["Item Name", "Category", "Price", "GST %"], ["TEA", "HOT", 20, 5], ["POHA", "SNACKS", 100, 5]]:
            ws.append(row)
        buf = io.BytesIO(); wb.save(buf); buf.seek(0)
        c.post("/menu/import", data={"_csrf": token, "create_categories": "1", "file": (buf, "m.xlsx")}, content_type="multipart/form-data")
        with app.app_context():
            items = {r["item_name"]: r["item_id"] for r in db.fetch_all(
                "SELECT M.item_id, M.item_name FROM menu M JOIN menu_category MC USING(menu_category_id) WHERE MC.company_detail_id=%s", (company_id,))}
            mode = {r["payment_mode"]: r["payment_mode_id"] for r in db.fetch_all(
                "SELECT payment_mode_id, payment_mode FROM bill_payment_mode WHERE company_detail_id=%s", (company_id,))}
        H = {"X-CSRF-Token": token}
        c.post("/tables/manage", data={"_csrf": token, "area": "AC", "names": "T1-T3"})
        tid = {t["name"]: t["id"] for a in c.get("/api/tables").get_json()["areas"] for t in a["tables"]}

        print("Table T1: 2 x TEA + 1 x POHA = 140 + 5% GST = 147.00")
        base = f"/api/tables/{tid['T1']}"
        c.post(base + "/kot", headers=H, json={"items": [{"item_id": items["TEA"], "quantity": 2}, {"item_id": items["POHA"], "quantity": 1}], "print": True})
        r = c.post(base + "/part-payment", headers=H, json={"payment_mode_id": mode["UPI"], "amount": "50"})
        check("part payment refused before the bill is printed", r.status_code == 422 and "Print the bill" in r.get_json()["error"], r.get_json())
        r = c.post(base + "/bill", headers=H, json={"paid": False})
        bill = r.get_json()["state"]["bill"]
        check("bill printed for 147.00 with nothing paid", bill["total"] == 147.0 and bill["due"] == 147.0 and bill["payments"] == [], bill)

        r = c.post(base + "/part-payment", headers=H, json={"payment_mode_id": mode["UPI"], "amount": "100"})
        bill = r.get_json()["state"]["bill"]
        check("UPI 100 taken, 47 due", r.status_code == 200 and bill["paid"] == 100.0 and bill["due"] == 47.0, bill)
        check("table still shows Bill printed", r.get_json()["state"]["status"] == "printed")
        r = c.post(base + "/part-payment", headers=H, json={"payment_mode_id": mode["Cash"], "amount": "50"})
        check("more than due is refused", r.status_code == 422 and "47.00" in r.get_json()["error"], r.get_json())
        for bad in ("0", "-5", "abc", "1.234"):
            r = c.post(base + "/part-payment", headers=H, json={"payment_mode_id": mode["Cash"], "amount": bad})
            check(f"bad amount {bad!r} refused", r.status_code == 422)
        r = c.post(base + "/part-payment", headers=H, json={"payment_mode_id": 999999, "amount": "5"})
        check("unknown payment mode refused", r.status_code == 422)
        r = c.post(base + "/part-payment", headers=H, json={"payment_mode_id": mode["Cash"], "amount": "20"})
        cash_id = [p for p in r.get_json()["state"]["bill"]["payments"] if p["mode"] == "Cash"][0]["id"]
        r = c.post(base + "/part-payment/remove", headers=H, json={"payment_id": cash_id})
        check("a part payment can be removed", r.get_json()["state"]["bill"]["due"] == 47.0, r.get_json()["state"]["bill"])
        r = c.post(base + "/settle", headers=H, json={"payment_mode_id": None})
        check("settle without a mode refused while 47 is due", r.status_code == 422 and "47.00" in r.get_json()["error"], r.get_json())
        r = c.post(base + "/settle", headers=H, json={"payment_mode_id": mode["Cash"]})
        body = r.get_json()
        check("settle takes the remaining 47 by Cash", r.status_code == 200 and body["bill"]["part"] is True, body)
        check("table is free after settling", body["state"]["status"] == "blank")
        bill_id = body["bill"]["bill_id"]
        with app.app_context():
            b = db.fetch_one("SELECT payment_mode_id, active FROM bill WHERE bill_id=%s", (bill_id,))
            parts = db.fetch_all("SELECT payment_mode_id, amount FROM ks_bill_payment WHERE bill_id=%s ORDER BY id", (bill_id,))
        check("bill marked paid with the largest share (UPI)", b["payment_mode_id"] == mode["UPI"] and b["active"] == 1, b)
        check("split stored as UPI 100 + Cash 47", [(p["payment_mode_id"], float(p["amount"])) for p in parts] == [(mode["UPI"], 100.0), (mode["Cash"], 47.0)], parts)
        r = c.post(base + "/part-payment", headers=H, json={"payment_mode_id": mode["Cash"], "amount": "1"})
        check("no part payment once the table is settled", r.status_code == 422)

        html = c.get(f"/bills/{bill_id}").get_data(as_text=True)
        check("receipt lists each part", "Paid via UPI" in html and "Paid via Cash" in html, "")
        html = c.get("/bills").get_data(as_text=True)
        check("bill list shows the split", "Part: UPI ₹100.00 + Cash ₹47.00" in html)
        html = c.get(f"/bills?payment_mode_id={mode['Cash']}").get_data(as_text=True)
        check("filtering bills by Cash finds the part-paid bill", "Part: UPI ₹100.00 + Cash ₹47.00" in html)

        print("Table T2: paid in full by parts, settled without choosing a mode")
        base2 = f"/api/tables/{tid['T2']}"
        c.post(base2 + "/kot", headers=H, json={"items": [{"item_id": items["POHA"], "quantity": 1}], "print": True})
        c.post(base2 + "/bill", headers=H, json={"paid": False})   # 105.00
        c.post(base2 + "/part-payment", headers=H, json={"payment_mode_id": mode["Card"], "amount": "60"})
        r = c.post(base2 + "/part-payment", headers=H, json={"payment_mode_id": mode["UPI"], "amount": "45"})
        check("fully paid by parts", r.get_json()["state"]["bill"]["due"] == 0.0, r.get_json()["state"]["bill"])
        r = c.post(base2 + "/part-payment", headers=H, json={"payment_mode_id": mode["UPI"], "amount": "1"})
        check("nothing more can be taken when fully paid", r.status_code == 422)
        r = c.post(base2 + "/settle", headers=H, json={"payment_mode_id": None})
        check("settled with no extra mode", r.status_code == 200 and r.get_json()["bill"]["part"] is True, r.get_json())
        with app.app_context():
            check("main mode is the larger part (Card)", db.fetch_value("SELECT payment_mode_id FROM bill WHERE bill_id=%s", (r.get_json()["bill"]["bill_id"],)) == mode["Card"])

        print("Table T3: items changed after a part payment, then cancelled")
        base3 = f"/api/tables/{tid['T3']}"
        c.post(base3 + "/kot", headers=H, json={"items": [{"item_id": items["POHA"], "quantity": 1}], "print": True})
        c.post(base3 + "/bill", headers=H, json={"paid": False})   # 105.00
        c.post(base3 + "/part-payment", headers=H, json={"payment_mode_id": mode["Cash"], "amount": "100"})
        r = c.post(base3 + "/kot", headers=H, json={"items": [{"item_id": items["TEA"], "quantity": 1}], "print": True})
        r = c.post(base3 + "/part-payment", headers=H, json={"payment_mode_id": mode["Cash"], "amount": "1"})
        check("new items mean the bill must be reprinted first", r.status_code == 422, r.get_json())
        r = c.post(base3 + "/bill", headers=H, json={"paid": False})   # 126.00 now
        bill3 = r.get_json()["state"]["bill"]
        check("reprinted bill keeps the 100 taken, 26 due", bill3["total"] == 126.0 and bill3["due"] == 26.0, bill3)
        r = c.post(base3 + "/void", headers=H, json={"line_id": [l for k in r.get_json()["state"]["kots"] for l in k["lines"] if l["item_id"] == items["POHA"]][0]["id"]})
        c.post(base3 + "/bill", headers=H, json={"paid": False})   # 21.00, but 100 was taken
        r = c.post(base3 + "/settle", headers=H, json={"payment_mode_id": mode["Cash"]})
        check("settle refused when more was taken than the bill", r.status_code == 422 and "Remove a payment" in r.get_json()["error"], r.get_json())
        r = c.post(base3 + "/cancel", headers=H, json={})
        with app.app_context():
            left = db.fetch_value("SELECT COUNT(*) FROM ks_bill_payment WHERE bill_id=%s", (bill3["id"],))
        check("cancelling the order removes its part payments", r.status_code == 200 and left == 0, left)

        print("Part straight from a new order (what the Part button does): save items, update bill, take amount")
        base4 = f"/api/tables/{tid['T1']}"
        c.post(base4 + "/kot", headers=H, json={"items": [{"item_id": items["TEA"], "quantity": 1}], "print": False})
        r = c.post(base4 + "/bill", headers=H, json={"paid": False})
        check("bill made without printing a KOT", r.status_code == 200 and r.get_json()["state"]["status"] == "printed", r.get_json()["state"]["status"])
        r = c.post(base4 + "/part-payment", headers=H, json={"payment_mode_id": mode["Cash"], "amount": "21"})
        check("amount taken on the unprinted order", r.status_code == 200 and r.get_json()["state"]["bill"]["due"] == 0.0, r.get_json())
        check("unsent items can still go to the kitchen", any(not k["printed"] for k in r.get_json()["state"]["kots"]))
        r = c.post(base4 + "/cancel", headers=H, json={})
        check("order cleaned up", r.status_code == 200)

        print("Generate Bill with a split")
        r = c.post("/api/bills", headers=H, json={"items": [{"item_id": items["TEA"], "quantity": 1}], "order_type": "dine_in",
                                                   "part": True, "payments": [{"payment_mode_id": mode["Cash"], "amount": "10"}, {"payment_mode_id": mode["UPI"], "amount": "10"}]})
        check("split that does not add up to 21.00 is refused", r.status_code == 422 and "21.00" in r.get_json()["error"], r.get_json())
        r = c.post("/api/bills", headers=H, json={"items": [{"item_id": items["TEA"], "quantity": 1}], "order_type": "dine_in",
                                                   "part": True, "payments": [{"payment_mode_id": mode["Cash"], "amount": "10"}, {"payment_mode_id": mode["UPI"], "amount": "11"}]})
        check("quick bill saved with Cash 10 + UPI 11", r.status_code in (200, 201) and r.get_json().get("ok"), r.get_json())
        quick_id = r.get_json().get("bill_id")
        with app.app_context():
            check("quick bill stored with UPI as main mode", db.fetch_value("SELECT payment_mode_id FROM bill WHERE bill_id=%s", (quick_id,)) == mode["UPI"])
        r = c.post("/api/bills", headers=H, json={"items": [{"item_id": items["TEA"], "quantity": 1}], "order_type": "dine_in", "payment_mode_id": mode["Cash"]})
        check("normal single-mode quick bill still works", r.status_code in (200, 201))

        print("Reports split part-paid bills by mode")
        with app.app_context():
            d = today()
            pays = {row["name"]: row for row in report.build("payments", company_id, d, d)["rows"]}
            # Cash: 47 (T1) + 10 (quick) + 21 (plain quick) = 78; UPI: 100 + 45 + 11 = 156; Card: 60
            check("payment report Cash = 78", float(pays["Cash"]["total"]) == 78.0, pays.get("Cash"))
            check("payment report UPI = 156", float(pays["UPI"]["total"]) == 156.0, pays.get("UPI"))
            check("payment report Card = 60", float(pays["Card"]["total"]) == 60.0, pays.get("Card"))
            check("part-paid bill counted once per mode", pays["UPI"]["bills"] == 3 and pays["Cash"]["bills"] == 3, pays)
            total = sum(float(r["total"]) for r in pays.values())
            check("report total equals bills total (294)", total == 294.0, total)
            dash = {row["name"]: float(row["amount"]) for row in dashboard.by_payment_mode(company_id, d, d)}
            check("dashboard split matches", dash == {"Cash": 78.0, "UPI": 156.0, "Card": 60.0}, dash)
            cons = report.build("daily_consolidated", company_id, d, d, "offline")
            row = cons["rows"][0]
            check("consolidated report per-mode columns", float(row[f"mode_{mode['Cash']}"]) == 78.0 and float(row[f"mode_{mode['UPI']}"]) == 156.0
                  and float(row[f"mode_{mode['Card']}"]) == 60.0 and float(row["total"]) == 294.0, row)
    finally:
        with app.app_context():
            if company_id:
                for b in db.fetch_all("SELECT bill_id FROM bill WHERE company_detail_id=%s", (company_id,)):
                    db.execute("DELETE FROM bill_detail WHERE bill_id=%s", (b["bill_id"],))
                db.execute("DELETE FROM ks_bill_payment WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM bill WHERE company_detail_id=%s", (company_id,))
                for k in db.fetch_all("SELECT table_order_id FROM table_order WHERE company_detail_id=%s", (company_id,)):
                    db.execute("DELETE N FROM ks_kot_note N JOIN table_order_detail D USING(table_order_detail_id) WHERE D.table_order_id=%s", (k["table_order_id"],))
                    db.execute("DELETE FROM ks_kot_ready WHERE table_order_id=%s", (k["table_order_id"],))
                    db.execute("DELETE FROM table_order_detail WHERE table_order_id=%s", (k["table_order_id"],))
                db.execute("DELETE FROM table_order WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE A FROM ks_table_area A JOIN serving_table T USING(serving_table_id) WHERE T.company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM serving_table WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE M FROM menu M JOIN menu_category MC USING(menu_category_id) WHERE MC.company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM menu_category WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM bill_payment_mode WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM ks_company_modules WHERE company_detail_id=%s", (company_id,))
                db.execute("DELETE FROM company_detail WHERE company_detail_id=%s", (company_id,))
            db.execute("DELETE FROM ks_super_admin WHERE id=%s", (sa_id,))
        print("  test restaurant and all its tables, orders, bills and part payments were removed")
    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("All checks passed")


if __name__ == "__main__":
    main()
