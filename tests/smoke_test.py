"""End-to-end smoke test against the configured database.

Signs in as an existing staff user (by injecting the session, so no password
is needed), opens every screen, exercises error handling, and saves one real
bill which is deleted again at the end.

Run:  .venv\\Scripts\\python tests\\smoke_test.py [user_id]
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app, db  # noqa: E402
from app.security import CSRF_SESSION_KEY  # noqa: E402

failures = []


def check(label, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + label + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def main(user_id: int):
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    with app.app_context():
        user = db.fetch_one(
            "SELECT aspirant_registration_id AS id, name, email, user_type, company_detail_id FROM aspirant_registration WHERE aspirant_registration_id=%s",
            (user_id,),
        )
    assert user, f"user {user_id} not found"
    print(f"Testing as {user['name']} <{user['email']}> company {user['company_detail_id']}")

    print("Anonymous access")
    r = client.get("/dashboard")
    check("protected page redirects to login", r.status_code == 302 and "/login" in r.headers["Location"])
    check("login page renders", client.get("/login").status_code == 200)
    r = client.post("/api/bills", json={})
    check("API without session is rejected", r.status_code in (401, 403), r.status_code)
    r = client.post("/login", data={"email": "nobody@example.com", "password": "x"})
    check("login without CSRF token is refused", r.status_code == 403, r.status_code)

    with client.session_transaction() as s:
        s["user"] = {"id": user["id"], "name": user["name"], "email": user["email"], "user_type": user["user_type"], "company_id": user["company_detail_id"]}
        s[CSRF_SESSION_KEY] = "test-token"
    H = {"X-CSRF-Token": "test-token"}

    print("Pages")
    for url in ["/dashboard", "/dashboard?range=year", "/dashboard?range=30d", "/menu-categories/", "/menu-categories/new",
                "/menu/", "/menu/?q=tea&status=1", "/menu/new", "/menu-pdf/", "/bills", "/bills?q=tea&per_page=50",
                "/bills?session=2025&quarter=Q2", "/bills/new"]:
        r = client.get(url)
        check(f"GET {url}", r.status_code == 200 and b"built-in method" not in r.data, r.status_code)
    r = client.get("/bills/export?session=2025")
    check("Excel export", r.status_code == 200 and r.data[:2] == b"PK", r.status_code)
    check("unknown page -> 404", client.get("/nope").status_code == 404)
    check("other company's bill -> 404", client.get("/bills/999999999").status_code == 404)
    r = client.get("/bills/999999999", headers={"Accept": "application/json"})
    check("JSON error body for API clients", r.is_json and r.get_json().get("ok") is False)

    print("Validation")
    r = client.post("/menu-categories/new", data={"_csrf": "test-token", "name": "", "sort_order": "abc"})
    check("empty category name rejected", r.status_code == 422 and b"Enter a category name" in r.data)
    r = client.post("/menu/new", data={"_csrf": "test-token", "name": "X", "rate": "-5", "gst": "99"})
    check("bad price/GST rejected", r.status_code == 422 and b"Price must be" in r.data and b"GST must be" in r.data)
    r = client.post("/menu-pdf/", data={"_csrf": "test-token", "pdf": (io.BytesIO(b"not a pdf"), "menu.pdf")}, content_type="multipart/form-data")
    check("fake PDF rejected", r.status_code == 422 and b"not a valid PDF" in r.data)
    r = client.post("/api/bills", json={"items": []}, headers=H)
    check("empty bill rejected", r.status_code == 422 and "at least one item" in r.get_json()["error"])
    r = client.post("/api/bills", json={"items": [{"item_id": 1, "quantity": 1}]})
    check("save without CSRF token refused", r.status_code == 403)
    r = client.post("/api/bills", data="{broken", headers={**H, "Content-Type": "application/json"})
    check("malformed JSON handled", r.status_code == 422, r.status_code)

    print("Saving a bill")
    with app.app_context():
        item = db.fetch_one(
            "SELECT M.item_id, M.rate, M.item_gst FROM menu M JOIN menu_category MC USING (menu_category_id) "
            "WHERE MC.company_detail_id=%s AND M.active=1 AND MC.active=1 LIMIT 1",
            (user["company_detail_id"],),
        )
        mode = db.fetch_value("SELECT payment_mode_id FROM bill_payment_mode WHERE company_detail_id=%s LIMIT 1", (user["company_detail_id"],))
    if not item or not mode:
        print("  skip (company has no billable items or payment modes)")
    else:
        r = client.post("/api/bills", json={"items": [{"item_id": item["item_id"], "quantity": 2}], "payment_mode_id": mode, "customer_phone": "12"}, headers=H)
        check("bad phone rejected", r.status_code == 422)
        r = client.post("/api/bills", headers=H, json={
            "items": [{"item_id": item["item_id"], "quantity": 2}], "payment_mode_id": mode,
            "customer_name": "Smoke Test", "container": {"enabled": True, "mode": "amount", "value": 10},
        })
        body = r.get_json()
        check("bill saved", r.status_code == 201 and body.get("ok"), body)
        if r.status_code == 201:
            bill_id = body["bill_id"]
            rate, gst = float(item["rate"]) * 2, float(item["item_gst"])
            print(f"     saved {body['bill_no']} total {body['total']} (2 x {item['rate']} + {gst}% GST + 10 container)")
            check("receipt renders", client.get(f"/bills/{bill_id}").status_code == 200)
            check("receipt shows customer", b"Smoke Test" in client.get(f"/bills/{bill_id}?embed=1").data)
            check("bill appears in list", body["bill_no"].encode() in client.get("/bills").data)
            with app.app_context():
                db.execute("DELETE FROM bill_detail WHERE bill_id=%s", (bill_id,))
                db.execute("DELETE FROM bill WHERE bill_id=%s", (bill_id,))
            print("     test bill removed again")

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        sys.exit(1)
    print("All checks passed")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 93)
