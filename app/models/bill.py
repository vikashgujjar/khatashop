"""Bills (`bill`) and their lines (`bill_detail`).

Note: `bill_detail` has no index on bill_id in the current database, so line
items are fetched with one range scan (bill_id BETWEEN min AND max) and
grouped in Python rather than joined row by row.
"""
import logging
import re
from collections import defaultdict
from decimal import Decimal

from .. import db
from ..errors import NotFoundError, ValidationError
from ..services import billing
from ..utils import Pagination, financial_period, now, parse_date, to_decimal, to_int
from . import company as company_model
from . import menu_item, part_payment, payment

log = logging.getLogger("khatashop.models.bill")

ITEM_NAME_MAX = 40  # bill_detail.item_name is varchar(40)
PHONE_RE = re.compile(r"^\+?\d{10,14}$")
QUARTERS = ("Q1", "Q2", "Q3", "Q4")

# Stored in bill.order_placement_type. Every bill made before this feature has 1,
# so existing bills count as dine-in.
ORDER_TYPES = {"dine_in": 1, "pickup": 2, "delivery": 3}
ORDER_TYPE_LABELS = {1: "Dine-in", 2: "Pickup", 3: "Delivery"}
DEFAULT_CONTAINER_PERCENT = 10


# ---------------------------------------------------------------- listing ---

def _where(company_id: int, filters: dict):
    sql = " FROM bill B WHERE B.company_detail_id = %s AND B.payment_mode_id <> 0"
    params: list = [company_id]
    q = (filters.get("q") or "").strip()
    if q:
        like = f"%{q}%"
        sql += (
            " AND (B.bill_no LIKE %s OR B.customer_name LIKE %s OR B.customer_mb_no LIKE %s"
            " OR B.bill_id IN (SELECT bill_id FROM bill_detail WHERE item_name LIKE %s))"
        )
        params += [f"{q}%", like, f"{q}%", like]
    session = to_int(filters.get("session"))
    if session:
        sql += " AND B.financial_session = %s"
        params.append(session)
    quarter = filters.get("quarter")
    if quarter in QUARTERS:
        sql += " AND B.financial_quarter = %s"
        params.append(quarter)
    order_type = to_int(filters.get("order_type"))
    if order_type in ORDER_TYPE_LABELS:
        sql += " AND B.order_placement_type = %s"
        params.append(order_type)
    payment_mode_id = to_int(filters.get("payment_mode_id"))
    if payment_mode_id:
        part_payment.ensure_schema()
        sql += (" AND (B.payment_mode_id = %s OR EXISTS (SELECT 1 FROM ks_bill_payment P "
                "WHERE P.bill_id = B.bill_id AND P.payment_mode_id = %s))")
        params += [payment_mode_id, payment_mode_id]
    date_from, date_to = parse_date(filters.get("date_from")), parse_date(filters.get("date_to"))
    if date_from:
        sql += " AND B.billing_date >= %s"
        params.append(date_from)
    if date_to:
        sql += " AND B.billing_date <= %s"
        params.append(date_to)
    return sql, params


BILL_FIELDS = (
    "B.bill_id AS id, B.bill_no, B.billing_date, B.created, B.total_quantity, B.total, B.cgst, B.sgst, "
    "B.service_cost, B.financial_session, B.financial_quarter, B.customer_name, B.customer_mb_no, "
    "B.payment_mode_id, B.counter_no, B.order_placement_type AS order_type, "
    "(SELECT PM.payment_mode FROM bill_payment_mode PM WHERE PM.payment_mode_id = B.payment_mode_id) AS payment_mode, "
    "(SELECT AR.name FROM aspirant_registration AR WHERE AR.aspirant_registration_id = B.counter_no) AS counter_name"
)


def _attach_items(bills: list[dict]) -> None:
    if not bills:
        return
    ids = [b["id"] for b in bills]
    rows = db.fetch_all(
        "SELECT bill_id, item_id, item_name, rate, quantity, item_gst FROM bill_detail "
        "WHERE bill_id BETWEEN %s AND %s ORDER BY bill_detail_id",
        (min(ids), max(ids)),
    )
    wanted = set(ids)
    grouped = defaultdict(list)
    for row in rows:
        if row["bill_id"] in wanted:
            grouped[row["bill_id"]].append(row)
    for bill in bills:
        bill["items"] = grouped.get(bill["id"], [])
        bill["order_type_label"] = ORDER_TYPE_LABELS.get(bill.get("order_type"), "Dine-in")


def paginate(company_id: int, filters: dict, page: int, per_page: int):
    where, params = _where(company_id, filters)
    summary = db.fetch_one(f"SELECT COUNT(*) AS bills, COALESCE(SUM(B.total), 0) AS amount {where}", params)
    pager = Pagination(page, per_page, summary["bills"])
    bills = db.fetch_all(
        f"SELECT {BILL_FIELDS} {where} ORDER BY B.bill_id DESC LIMIT %s OFFSET %s",
        params + [pager.per_page, pager.offset],
    )
    _attach_items(bills)
    part_payment.apply_labels(bills)
    return bills, pager, summary


def export_rows(company_id: int, filters: dict, limit: int = 100_000) -> list[dict]:
    where, params = _where(company_id, filters)
    bills = db.fetch_all(f"SELECT {BILL_FIELDS} {where} ORDER BY B.bill_id DESC LIMIT %s", params + [limit])
    _attach_items(bills)
    part_payment.apply_labels(bills)
    return bills


def sessions(company_id: int) -> list[int]:
    rows = db.fetch_all(
        "SELECT DISTINCT financial_session AS s FROM bill WHERE company_detail_id = %s AND financial_session > 0 ORDER BY s DESC",
        (company_id,),
    )
    return [r["s"] for r in rows]


def get(company_id: int, bill_id: int) -> dict:
    extra = ", B.container_charges" if db.column_exists("bill", "container_charges") else ", 0 AS container_charges"
    bill = db.fetch_one(
        f"SELECT {BILL_FIELDS}{extra} FROM bill B WHERE B.bill_id = %s AND B.company_detail_id = %s",
        (bill_id, company_id),
    )
    if not bill:
        raise NotFoundError("That bill does not exist.")
    bill["items"] = db.fetch_all(
        "SELECT item_id, item_name, rate, quantity, item_gst FROM bill_detail WHERE bill_id = %s ORDER BY bill_detail_id",
        (bill_id,),
    )
    subtotal = Decimal("0")
    for item in bill["items"]:
        item["amount"] = item["rate"] * item["quantity"]
        subtotal += item["amount"]
    bill["subtotal"] = subtotal
    bill["order_type_label"] = ORDER_TYPE_LABELS.get(bill.get("order_type"), "Dine-in")
    bill["payments"] = part_payment.for_bill(bill_id)
    if bill["payments"] and bill["payment_mode_id"]:
        bill["payment_mode"] = part_payment.label(bill["payments"])
    return bill


# --------------------------------------------------------------- creating ---

def preview_bill_no(company_id: int) -> str:
    company = company_model.get(company_id)
    return next_bill_no(company_id, company["abbreviation"])


def bill_no_lock(company_id: int):
    """Hold this while allocating and inserting a bill number."""
    return db.named_lock(f"khatashop_bill_no_{company_id}")


def next_bill_no(company_id: int, abbreviation: str) -> str:
    """Next number after the highest one this restaurant has used with its prefix.

    Uses the highest number (not the newest row) because table bills get their
    number when printed, so rows are not always inserted in number order.
    """
    n = len(abbreviation)
    highest = db.fetch_value(
        "SELECT MAX(CAST(SUBSTRING(bill_no, %s) AS UNSIGNED)) AS m FROM bill "
        "WHERE company_detail_id = %s AND LEFT(bill_no, %s) = BINARY %s AND SUBSTRING(bill_no, %s) REGEXP '^[0-9]+$'",
        (n + 1, company_id, n, abbreviation, n + 1),
    )
    return billing.next_bill_number(f"{abbreviation}{highest}" if highest else None, abbreviation)


def _parse_payload(company_id: int, payload) -> dict:
    if not isinstance(payload, dict):
        raise ValidationError("The bill data was not sent correctly. Please reload the page.")
    errors = {}

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValidationError("Add at least one item to the bill.")
    if len(raw_items) > billing.MAX_LINES:
        raise ValidationError(f"A bill can have at most {billing.MAX_LINES} different items.")
    quantities: dict[int, int] = {}
    for entry in raw_items:
        item_id = to_int(entry.get("item_id")) if isinstance(entry, dict) else None
        qty = to_int(entry.get("quantity")) if isinstance(entry, dict) else None
        if not item_id or qty is None:
            raise ValidationError("One of the bill lines is invalid. Please reload the page.")
        if qty < 1:
            raise ValidationError("Quantity cannot be zero.")
        quantities[item_id] = quantities.get(item_id, 0) + qty
        if quantities[item_id] > billing.MAX_QUANTITY:
            raise ValidationError(f"Quantity per item cannot be more than {billing.MAX_QUANTITY}.")

    modes = {m["id"]: m for m in payment.payment_modes(company_id)}
    if not modes:
        raise ValidationError("No payment modes are set up for your company. Ask the administrator to add them.")
    parts = []
    if payload.get("part"):
        raw_parts = payload.get("payments")
        if not isinstance(raw_parts, list) or not raw_parts:
            errors["payments"] = "Add the amounts paid by each mode."
        elif len(raw_parts) > part_payment.MAX_PARTS:
            errors["payments"] = f"A bill can be split into at most {part_payment.MAX_PARTS} payments."
        else:
            for entry in raw_parts:
                mode = to_int(entry.get("payment_mode_id")) if isinstance(entry, dict) else None
                if mode not in modes:
                    errors["payments"] = "One of the part payments has no valid payment mode."
                    break
                parts.append((mode, part_payment.parse_amount(entry.get("amount"))))
        payment_mode_id = None
    else:
        payment_mode_id = to_int(payload.get("payment_mode_id"))
        if payment_mode_id not in modes:
            errors["payment_mode_id"] = "Choose a payment mode."

    service_percent = Decimal("0")
    service_mode_id = to_int(payload.get("service_mode_id"))
    if service_mode_id:
        services = {s["id"]: s for s in payment.service_modes(company_id)}
        if service_mode_id not in services:
            errors["service_mode_id"] = "Choose a valid service mode."
        else:
            service_percent = Decimal(str(services[service_mode_id]["percent"]))

    order_type = ORDER_TYPES.get(str(payload.get("order_type") or "dine_in"))
    if order_type is None:
        errors["order_type"] = "Choose Dine-in, Pickup or Delivery."

    container = payload.get("container") or {}
    container_mode, container_value = None, Decimal("0")
    if order_type == ORDER_TYPES["dine_in"]:
        container = {}  # dine-in never has container charges
    if isinstance(container, dict) and container.get("enabled"):
        container_mode = container.get("mode")
        container_value = to_decimal(container.get("value"))
        if container_mode not in ("percent", "amount"):
            errors["container"] = "Choose % or a fixed amount for container charges."
        elif container_value is None or container_value < 0:
            errors["container"] = "Container charge must be zero or more."
        elif container_mode == "percent" and container_value > 100:
            errors["container"] = "Container charge cannot be more than 100%."
        elif container_mode == "amount" and container_value > 100000:
            errors["container"] = "Container charge is too large."

    customer_name = (payload.get("customer_name") or "").strip()
    if len(customer_name) > 100:
        errors["customer_name"] = "Keep the customer name under 100 characters."
    phone = re.sub(r"[\s-]", "", str(payload.get("customer_phone") or ""))
    if phone and not PHONE_RE.match(phone):
        errors["customer_phone"] = "Enter a valid mobile number (10 digits)."

    if errors:
        raise ValidationError(next(iter(errors.values())), errors=errors)
    return {
        "quantities": quantities,
        "payment_mode_id": payment_mode_id,
        "parts": parts,
        "service_percent": service_percent,
        "container_mode": container_mode,
        "container_value": container_value or Decimal("0"),
        "customer_name": customer_name,
        "customer_phone": phone,
        "order_type": order_type,
    }


def create(company_id: int, user_id: int, payload) -> dict:
    data = _parse_payload(company_id, payload)
    company = company_model.get(company_id)
    if not company["abbreviation"]:
        raise ValidationError("Your company has no bill abbreviation set, so bill numbers cannot be created.")

    # Prices always come from the database, never from the browser.
    priced = menu_item.for_billing(company_id, list(data["quantities"]))
    missing = [i for i in data["quantities"] if i not in priced]
    if missing:
        raise ValidationError(
            "Some items are no longer available (hidden or removed). Reload the page to refresh the menu.",
            errors={"items": missing},
        )
    lines = [
        billing.BillLine(
            item_id=item_id,
            name=priced[item_id]["name"],
            rate=priced[item_id]["rate"],
            quantity=qty,
            gst_percent=priced[item_id]["gst"],
        )
        for item_id, qty in data["quantities"].items()
    ]
    totals = billing.calculate(lines, data["service_percent"], data["container_mode"], data["container_value"])

    if data["parts"]:
        paid = sum((amount for _mode, amount in data["parts"]), Decimal("0"))
        if paid != totals.grand_total:
            raise ValidationError(
                f"The part payments add up to ₹{paid:,.2f} but the bill is ₹{totals.grand_total:,.2f}. "
                "Make them equal to the bill total.", errors={"payments": "Mismatch."})
        by_mode: dict[int, Decimal] = {}
        for mode, amount in data["parts"]:
            by_mode[mode] = by_mode.get(mode, Decimal("0")) + amount
        data["payment_mode_id"] = max(by_mode, key=by_mode.get)

    moment = now()
    session_year, quarter = financial_period(moment.date())
    has_container_col = db.column_exists("bill", "container_charges")
    service_cost = totals.service_cost
    if totals.container_charges and not has_container_col:
        # Older databases have no container_charges column; keep the money on the bill.
        service_cost += totals.container_charges
        log.warning("bill.container_charges column missing; container charge added to service_cost")

    with bill_no_lock(company_id):
        bill_no = next_bill_no(company_id, company["abbreviation"])
        columns = {
            "company_detail_id": company_id,
            "bill_no": bill_no,
            "cgst": str(totals.cgst),
            "sgst": str(totals.sgst),
            "total_quantity": str(totals.total_quantity),
            "total": totals.grand_total,
            "service_cost": service_cost,
            "billing_date": moment.date(),
            "customer_name": data["customer_name"],
            "customer_mb_no": data["customer_phone"],
            "counter_no": user_id,
            "financial_quarter": quarter,
            "financial_session": session_year,
            "payment_mode_id": data["payment_mode_id"],
            "serving_table_id": 0,
            "order_placement_type": data["order_type"],
            "active": 1,
            "created": moment,
            "updated": moment,
        }
        if has_container_col:
            columns["container_charges"] = totals.container_charges
        names = ", ".join(f"`{c}`" for c in columns)
        marks = ", ".join(["%s"] * len(columns))
        with db.transaction() as cur:
            cur.execute(f"INSERT INTO bill ({names}) VALUES ({marks})", list(columns.values()))
            bill_id = cur.lastrowid
            cur.executemany(
                "INSERT INTO bill_detail (bill_id, item_id, item_name, rate, quantity, item_gst, created, updated) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (bill_id, l.item_id, l.name[:ITEM_NAME_MAX], l.rate, l.quantity, l.gst_percent, moment, moment)
                    for l in lines
                ],
            )
            if data["parts"]:
                part_payment.save_for_new_bill(cur, company_id, bill_id, data["parts"], user_id)
    log.info("Bill %s (id %s) saved by user %s: total %s", bill_no, bill_id, user_id, totals.grand_total)
    return {"bill_id": bill_id, "bill_no": bill_no, "total": float(totals.grand_total)}
