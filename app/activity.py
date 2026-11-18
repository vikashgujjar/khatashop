"""Activity log: who did what, in which restaurant, when and from where. Super admins read it.

One `after_request` hook records every request that changes data (and every Excel export and
sign-in), so new screens are covered without extra code. What is stored:

  ks_activity_log   time, user (id, name, email, role), restaurant, action, what it touched,
                    details, ok / failed, IP address and browser

Details come from what the request already has: the page's own success / error message,
bill numbers and totals from JSON replies, and a short safe summary of the submitted
fields. Passwords, CSRF tokens and other secrets are never stored.
Recording never breaks a request: any problem is logged and skipped.
"""
import logging
import threading

from flask import g, request, session

from . import db
from .utils import Pagination, now

log = logging.getLogger("khatashop.activity")

# endpoint -> (category, label). Only these are recorded.
ACTIONS = {
    "auth.login": ("Sign-in", "Signed in"),
    "auth.logout": ("Sign-in", "Signed out"),
    # super admin
    "admin.restaurant_new": ("Restaurants", "Registered restaurant"),
    "admin.restaurant": ("Restaurants", "Updated restaurant / modules"),
    "admin.restaurant_delete": ("Restaurants", "Deleted restaurant"),
    "admin.restaurant_open": ("Restaurants", "Entered restaurant"),
    "admin.exit_outlet": ("Restaurants", "Left restaurant"),
    "admin.payment_mode_add": ("Restaurants", "Added payment option"),
    "admin.payment_mode_delete": ("Restaurants", "Removed payment option"),
    "admin.staff_new": ("Staff", "Added staff login"),
    "admin.staff_edit": ("Staff", "Updated staff login"),
    "admin.super_admins": ("Super admins", "Added super admin"),
    "admin.super_admin_toggle": ("Super admins", "Activated / deactivated super admin"),
    "admin.super_admin_password": ("Super admins", "Changed super admin password"),
    # billing & tables
    "bills.store": ("Billing", "Generated bill"),
    "bills.setup_payment_options": ("Billing", "Set up payment options"),
    "bills.export": ("Export", "Exported bills to Excel"),
    "tables.kot": ("Tables", "Saved order / KOT"),
    "tables.void": ("Tables", "Removed item from order"),
    "tables.bill": ("Billing", "Printed table bill"),
    "tables.settle": ("Billing", "Settled bill"),
    "tables.part_payment_add": ("Billing", "Took part payment"),
    "tables.part_payment_remove": ("Billing", "Removed part payment"),
    "tables.move": ("Tables", "Moved order to another table"),
    "tables.cancel": ("Tables", "Cancelled table order"),
    "tables.kitchen_ready": ("Kitchen", "Marked KOT ready / not ready"),
    "tables.manage": ("Tables", "Added tables"),
    "tables.manage_update": ("Tables", "Updated table"),
    "tables.manage_delete": ("Tables", "Deleted table"),
    # menu
    "menu.create": ("Menu", "Added menu item"),
    "menu.edit": ("Menu", "Edited menu item"),
    "menu.delete": ("Menu", "Deleted menu item"),
    "menu.toggle": ("Menu", "Showed / hid menu item"),
    "menu.import_file": ("Menu", "Imported menu from Excel"),
    "menu.export": ("Export", "Exported menu to Excel"),
    "menu_category.create": ("Menu", "Added menu category"),
    "menu_category.edit": ("Menu", "Edited menu category"),
    "menu_category.delete": ("Menu", "Deleted menu category"),
    "menu_category.toggle": ("Menu", "Showed / hid menu category"),
    "menu_category.import_file": ("Menu", "Imported categories from Excel"),
    "menu_category.export": ("Export", "Exported categories to Excel"),
    "menu_pdf.index": ("Menu", "Uploaded menu PDF"),
    # staff, inventory, online, settings
    "staff.create": ("Staff", "Added staff login"),
    "staff.edit": ("Staff", "Updated staff login"),
    "notes.create": ("Inventory", "Added inventory note"),
    "notes.edit": ("Inventory", "Edited inventory note"),
    "notes.delete": ("Inventory", "Deleted inventory note"),
    "formula.edit": ("Inventory", "Saved consumption formula"),
    "online.update": ("Online orders", "Updated online order"),
    "crud.create": ("Settings", "Added record"),
    "crud.edit": ("Settings", "Edited record"),
    "crud.delete": ("Settings", "Deleted record"),
    "reports.export": ("Export", "Exported report to Excel"),
}
CATEGORIES = sorted({c for c, _l in ACTIONS.values()})
EXPORTS = {e for e, (c, _l) in ACTIONS.items() if c == "Export"}

SECRET_WORDS = ("password", "csrf", "token", "secret", "otp", "pin")
SAFE_FIELDS = ("name", "item_name", "menu_category_name", "email", "payment_mode", "area", "names", "status",
               "abbreviation", "location", "type", "date_from", "date_to", "range", "source", "period",
               "payment_mode_id", "amount", "to_table_id", "line_id", "payment_id", "ready", "confirmed", "driver_id", "print", "paid")

_schema_ready = False
_schema_lock = threading.Lock()


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        db.execute(
            "CREATE TABLE IF NOT EXISTS ks_activity_log ("
            " id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
            " created DATETIME NOT NULL,"
            " user_id INT NULL, user_name VARCHAR(120) NULL, user_email VARCHAR(190) NULL,"
            " role VARCHAR(10) NULL, user_type INT NULL,"
            " company_detail_id INT NULL,"
            " action VARCHAR(60) NOT NULL, category VARCHAR(30) NOT NULL, label VARCHAR(120) NOT NULL,"
            " target VARCHAR(190) NULL, details TEXT NULL,"
            " status VARCHAR(10) NOT NULL DEFAULT 'ok',"
            " ip VARCHAR(45) NULL, user_agent VARCHAR(255) NULL,"
            " KEY idx_created (created), KEY idx_company (company_detail_id, created), KEY idx_user (user_email, created)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
        _schema_ready = True


# ---------------------------------------------------------------- recording ---

def _clip(text, size):
    text = "" if text is None else str(text)
    return text if len(text) <= size else text[: size - 1] + "…"


def _summary(data: dict) -> str:
    """Short 'key: value' list of safe fields (never secrets)."""
    parts = []
    for key, value in data.items():
        k = str(key).lower()
        if any(w in k for w in SECRET_WORDS):
            continue
        if key == "items" and isinstance(value, list):
            parts.append(f"items: {len(value)} line(s)")
        elif key == "payments" and isinstance(value, list):
            parts.append("split: " + ", ".join(f"{p.get('amount')}" for p in value if isinstance(p, dict)))
        elif key in SAFE_FIELDS and value not in (None, "", [], {}):
            parts.append(f"{key}: {_clip(value, 60)}")
    return "; ".join(parts)


def mark() -> None:
    """before_request: remember how many messages were already waiting, so only this request's count."""
    g._ks_flash_start = len(session.get("_flashes", []))


def _flashes(category: str) -> list[str]:
    start = g.get("_ks_flash_start", 0)
    return [m for c, m in session.get("_flashes", [])[start:] if c == category]


def _table_name(company_id, table_id):
    try:
        name = db.fetch_value("SELECT serving_table_name FROM serving_table WHERE serving_table_id = %s", (table_id,))
        return f"Table {name}" if name else f"Table #{table_id}"
    except Exception:  # noqa: BLE001
        return f"Table #{table_id}"


def _target_and_details(endpoint: str, response) -> tuple[str, list[str]]:
    view = request.view_args or {}
    target = ""
    details = []
    if "table_id" in view:
        target = _table_name(None, view["table_id"])
    elif "resource" in view:
        target = str(view["resource"]).replace("_", " ").title() + (f" #{view['record_id']}" if "record_id" in view else
                                                                   f" #{view['note_id']}" if "note_id" in view else "")
    else:
        for key, label in (("item_id", "Item"), ("category_id", "Category"), ("staff_id", "Staff"), ("company_id", "Restaurant"),
                           ("order_id", "Order"), ("kot_id", "KOT"), ("admin_id", "Super admin"), ("mode_id", "Payment option")):
            if key in view:
                target = f"{label} #{view[key]}"
                break
    # JSON replies carry bill numbers and totals
    if response.is_json:
        body = response.get_json(silent=True) or {}
        bill = body.get("bill") if isinstance(body.get("bill"), dict) else body if "bill_no" in body else None
        if bill and bill.get("bill_no"):
            extra = f"Bill {bill['bill_no']}"
            if bill.get("total") is not None:
                extra += f" · ₹{float(bill['total']):,.2f}"
            if bill.get("part"):
                extra += " · part payment"
            details.append(extra)
        if body.get("moved_to_name"):
            details.append(f"moved to table {body['moved_to_name']}")
        if not body.get("ok", True) and body.get("error"):
            details.append(_clip(body["error"], 200))
    # what was submitted (safe fields only)
    if request.method == "POST":
        data = request.get_json(silent=True) if request.is_json else request.form.to_dict()
        if isinstance(data, dict):
            s = _summary(data)
            if s:
                details.append(s)
    elif endpoint in EXPORTS:
        s = _summary(request.args.to_dict())
        if s:
            details.append(s)
    if endpoint == "auth.login" and not target:
        target = (request.form.get("email") or "").strip().lower()
    return target, details


def record(response):
    """after_request hook. Never raises."""
    try:
        endpoint = request.endpoint or ""
        if endpoint not in ACTIONS:
            return response
        is_export = endpoint in EXPORTS
        if request.method != "POST" and not is_export:
            return response   # opening a form is not an action
        code = response.status_code
        if endpoint == "auth.login":
            status = "ok" if code in (301, 302, 303) else "failed"
        elif code == 403:
            status = "denied"
        elif code >= 400:
            if code in (400, 422) and not request.is_json:
                return response  # a form shown again with field errors: nothing changed
            status = "failed"
        else:
            status = "failed" if _flashes("error") else "ok"
        # who: the signed-in user (for sign-out, the one who was signed in)
        user = g.get("user") or session.get("user") or {}
        category, label = ACTIONS[endpoint]
        target, details = _target_and_details(endpoint, response)
        if status != "ok" and endpoint == "auth.login":
            label = "Failed sign-in"
        for message in (_flashes("error") if status != "ok" else _flashes("success"))[-1:]:
            details.insert(0, _clip(message, 200))
        ensure_schema()
        db.execute(
            "INSERT INTO ks_activity_log (created, user_id, user_name, user_email, role, user_type, company_detail_id, "
            "action, category, label, target, details, status, ip, user_agent) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (now(), user.get("id"), _clip(user.get("name"), 120) or None, _clip(user.get("email") or (target if endpoint == "auth.login" else ""), 190) or None,
             user.get("role"), user.get("user_type"), user.get("company_id") or (request.view_args or {}).get("company_id"),
             endpoint, category, label, _clip(target, 190) or None, _clip(" | ".join(details), 2000) or None, status,
             _clip(request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip(), 45),
             _clip(request.headers.get("User-Agent"), 255)),
        )
    except Exception:  # noqa: BLE001 - the log must never break the page
        log.exception("Could not record activity for %s", request.path)
    return response


# ------------------------------------------------------------------ reading ---

def _where(filters: dict):
    sql = " FROM ks_activity_log A LEFT JOIN company_detail C ON C.company_detail_id = A.company_detail_id WHERE A.created BETWEEN %s AND %s"
    params = [filters["start_dt"], filters["end_dt"]]
    if filters.get("company_id") == "none":
        sql += " AND A.company_detail_id IS NULL"
    elif filters.get("company_id"):
        sql += " AND A.company_detail_id = %s"
        params.append(int(filters["company_id"]))
    if filters.get("category") in CATEGORIES:
        sql += " AND A.category = %s"
        params.append(filters["category"])
    if filters.get("status") in ("ok", "failed", "denied"):
        sql += " AND A.status = %s"
        params.append(filters["status"])
    if filters.get("q"):
        like = "%" + filters["q"].replace("%", r"\%").replace("_", r"\_") + "%"
        sql += " AND (A.user_name LIKE %s OR A.user_email LIKE %s OR A.label LIKE %s OR A.target LIKE %s OR A.details LIKE %s OR A.ip LIKE %s)"
        params += [like] * 6
    return sql, params


FIELDS = ("A.id, A.created, A.user_id, A.user_name, A.user_email, A.role, A.user_type, A.company_detail_id, "
          "COALESCE(C.company_name_l1, '') AS restaurant, A.action, A.category, A.label, A.target, A.details, A.status, A.ip, A.user_agent")


def search(filters: dict, page: int, per_page: int = 50):
    ensure_schema()
    where, params = _where(filters)
    total = db.fetch_value(f"SELECT COUNT(*) AS n {where}", params, 0)
    pager = Pagination(page, per_page, total)
    rows = db.fetch_all(f"SELECT {FIELDS} {where} ORDER BY A.id DESC LIMIT %s OFFSET %s", params + [pager.per_page, pager.offset])
    return rows, pager


def export_rows(filters: dict, limit: int = 50_000) -> list[dict]:
    ensure_schema()
    where, params = _where(filters)
    return db.fetch_all(f"SELECT {FIELDS} {where} ORDER BY A.id DESC LIMIT %s", params + [limit])


def summary(filters: dict) -> dict:
    ensure_schema()
    where, params = _where(filters)
    row = db.fetch_one(
        f"SELECT COUNT(*) AS total, SUM(A.status <> 'ok') AS problems, COUNT(DISTINCT A.user_email) AS users, "
        f"SUM(A.action = 'auth.login' AND A.status <> 'ok') AS failed_logins {where}", params)
    return {k: int(v or 0) for k, v in row.items()}
