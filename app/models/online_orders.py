"""Online orders from the customer app (tables `orders`, `order_items`, shared with the
customer and driver apps).

Status changes are written exactly like the PHP app does (Crud_model::update_order):
  * status JSON  [{"id":"1","status":"<status>"}]
  * a history entry appended to `notes`: {"status":"1","value":"Order <status>","time":"Nov 14, 2026 1:36 PM"}
  * isReady = 1 when the status becomes "prepared"
The PHP code sends no notifications on these changes, so neither do we.
"""
from __future__ import annotations
import json
from datetime import date, datetime, time

from ..core import current_app

from .. import db
from ..errors import NotFoundError, ValidationError
from ..utils import now, to_int

STATUSES = {
    "created": "Created", "pending": "Pending", "accepted": "Accepted", "preparing": "Preparing", "prepared": "Prepared",
    "pickedup": "Picked Up", "outfordelivery": "Out For Delivery", "delivered": "Delivered", "cancelled": "Cancelled",
}
SETTABLE = ["accepted", "preparing", "prepared", "pickedup", "outfordelivery", "delivered", "cancelled"]
PAYMENT = {1: "Success", 2: "Pending", 3: "Cancelled", 0: "Failed"}


def vendor_for(company_id: int) -> int | None:
    """orders.vendor_id for a restaurant. Configured in db_config.json -> online_orders.vendor_by_company;
    by default the restaurant id itself, unless that id is mapped to another restaurant."""
    mapping = {int(k): int(v) for k, v in current_app.config["KS"].get("online_orders", {}).get("vendor_by_company", {}).items()}
    if company_id in mapping:
        return mapping[company_id]
    if company_id in mapping.values():
        return None
    return company_id


def _status_of(raw) -> str:
    try:
        data = json.loads(raw or "[]")
        return (data[0].get("status") or "").strip().lower() if data else ""
    except (ValueError, AttributeError, IndexError, TypeError):
        return ""


def _history(raw) -> list:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def _enrich(o: dict) -> dict:
    # orders.isReady is a TEXT column ('0'/'1'); '0' would count as true in templates.
    o["isReady"] = 1 if str(o.get("isReady") or "0").strip() == "1" else 0
    o["is_confirmed"] = 1 if str(o.get("is_confirmed") or "0").strip() == "1" else 0
    o["status_key"] = _status_of(o.get("status"))
    o["status_label"] = STATUSES.get(o["status_key"], o["status_key"].title() or "Unknown")
    hist = _history(o.get("notes"))
    o["history"] = hist
    o["status_time"] = hist[-1].get("time") if hist else ""
    o["payment_label"] = PAYMENT.get(o.get("payment_status"), "Unknown")
    return o


ORDER_SQL = (
    "SELECT O.order_id AS id, O.created_at, O.status, O.notes, O.isReady, O.is_confirmed, O.payment_status, O.paid_method, "
    "O.total, O.discount, O.tax, O.delivery_charge, O.container_charges, O.grand_total, O.driver_id, "
    "COALESCE(NULLIF(CA.name, ''), O.name, C.customer_name) AS customer_name, "
    "COALESCE(NULLIF(C.customer_mobile_no, ''), O.mobile, CA.mobile) AS mobile, "
    "CONCAT_WS(', ', CA.customer_address, CA.landmark, CA.city, CA.state) AS address, "
    "TRIM(CONCAT(COALESCE(D.first_name, ''), ' ', COALESCE(D.last_name, ''))) AS driver_name, D.mobile AS driver_mobile "
    "FROM orders O LEFT JOIN customers C ON C.customer_id = O.uid "
    "LEFT JOIN customer_address CA ON CA.customer_address_id = O.address "
    "LEFT JOIN drivers D ON D.id = O.driver_id "
)


def list_orders(company_id: int, start: date, end: date, status: str = "", q: str = "") -> dict:
    vendor = vendor_for(company_id)
    if vendor is None:
        return {"orders": [], "count": 0, "overall": 0, "vendor": None}
    where = " WHERE O.vendor_id = %s AND O.created_at BETWEEN %s AND %s"
    params: list = [vendor, datetime.combine(start, time.min), datetime.combine(end, time.max)]
    if q:
        where += " AND (O.order_id = %s OR C.customer_mobile_no LIKE %s OR O.mobile LIKE %s OR CA.name LIKE %s)"
        params += [to_int(q.lstrip("#"), 0), f"%{q}%", f"%{q}%", f"%{q}%"]
    rows = [_enrich(o) for o in db.fetch_all(ORDER_SQL + where + " ORDER BY O.order_id DESC LIMIT 500", params)]
    if status:
        rows = [o for o in rows if o["status_key"] == status]
    if rows:
        ids = [o["id"] for o in rows]
        marks = ",".join(["%s"] * len(ids))
        items = db.fetch_all(f"SELECT order_id, item_name, quantity, rate, item_gst, total FROM order_items WHERE order_id IN ({marks}) "
                             "ORDER BY order_item_id", ids)
        for o in rows:
            o["lines"] = [i for i in items if i["order_id"] == o["id"]]
    overall = db.fetch_value("SELECT COUNT(*) AS c FROM orders WHERE vendor_id = %s", (vendor,), 0)
    return {"orders": rows, "count": len(rows), "overall": overall, "vendor": vendor}


def get(company_id: int, order_id: int) -> dict:
    vendor = vendor_for(company_id)
    o = db.fetch_one(ORDER_SQL + " WHERE O.order_id = %s AND O.vendor_id = %s", (order_id, vendor))
    if not o:
        raise NotFoundError("That order does not exist for this restaurant.")
    o = _enrich(o)
    o["lines"] = db.fetch_all("SELECT item_name, quantity, rate, item_gst, total FROM order_items WHERE order_id = %s ORDER BY order_item_id", (order_id,))
    return o


def drivers(company_id: int) -> list[dict]:
    """This restaurant's delivery drivers."""
    from . import driver_links
    return driver_links.drivers_for(company_id)


def update(company_id: int, order_id: int, status: str | None, driver_id, confirm: bool | None = None, ready: bool | None = None) -> dict:
    order = get(company_id, order_id)
    changes, history = {}, order["history"]
    moment = now()
    stamp = moment.strftime("%b %d, %Y ") + str(int(moment.strftime("%I"))) + moment.strftime(":%M %p")  # "Nov 14, 2026 1:36 PM"
    if status is not None and status != order["status_key"]:
        if status not in SETTABLE:
            raise ValidationError("Choose a valid order status.")
        try:
            data = json.loads(order["status"] or "[]")
        except ValueError:
            data = []
        if data and isinstance(data[0], dict):
            data[0]["status"] = status
        else:
            data = [{"id": "1", "status": status}]
        changes["status"] = json.dumps(data, separators=(",", ":"))
        history.append({"status": "1", "value": f"Order {status}", "time": stamp})
        if status == "prepared":
            changes["isReady"] = 1
    if driver_id is not None:
        d = to_int(driver_id)
        if d and d not in {x["id"] for x in drivers(company_id)}:
            raise ValidationError("Choose a valid driver.")
        if d != order["driver_id"]:
            changes["driver_id"] = d or None
            if d:
                history.append({"status": "1", "value": "Driver assigned", "time": stamp})
    if confirm is not None:
        changes["is_confirmed"] = 1 if confirm else 0
    if ready is not None:
        changes["isReady"] = 1 if ready else 0
    if not changes:
        return order
    changes["notes"] = json.dumps(history, separators=(",", ":"))
    changes["updated_at"] = now()
    sets = ", ".join(f"`{k}` = %s" for k in changes)
    db.execute(f"UPDATE orders SET {sets} WHERE order_id = %s", [*changes.values(), order_id])
    return get(company_id, order_id)


def item_summary(company_id: int, start: date, end: date) -> list[dict]:
    """Old 'Item Summary': quantities of items in paid, delivered online orders."""
    vendor = vendor_for(company_id)
    if vendor is None:
        return []
    return db.fetch_all(
        "SELECT OI.item_name AS name, SUM(OI.quantity) AS quantity, SUM(OI.total) AS amount FROM order_items OI "
        "JOIN orders O ON O.order_id = OI.order_id WHERE O.vendor_id = %s AND O.payment_status = 1 "
        "AND JSON_UNQUOTE(JSON_EXTRACT(O.status, '$[0].status')) = 'delivered' AND O.created_at BETWEEN %s AND %s "
        "GROUP BY OI.item_name ORDER BY quantity DESC",
        (vendor, datetime.combine(start, time.min), datetime.combine(end, time.max)),
    )
