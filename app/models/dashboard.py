"""Sales figures for the dashboard."""
from datetime import date, timedelta
from decimal import Decimal

from .. import db

RANGES = {
    "today": "Today",
    "yesterday": "Yesterday",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "month": "This month",
    "year": "This year",
}


def resolve_range(key: str, today: date) -> tuple[str, date, date]:
    if key not in RANGES:
        key = "today"
    if key == "today":
        start = end = today
    elif key == "yesterday":
        start = end = today - timedelta(days=1)
    elif key == "7d":
        start, end = today - timedelta(days=6), today
    elif key == "30d":
        start, end = today - timedelta(days=29), today
    elif key == "month":
        start, end = today.replace(day=1), today
    else:
        start, end = today.replace(month=1, day=1), today
    return key, start, end


BASE = "FROM bill B WHERE B.company_detail_id = %s AND B.payment_mode_id <> 0 AND B.billing_date BETWEEN %s AND %s"


def summary(company_id: int, start: date, end: date) -> dict:
    row = db.fetch_one(
        "SELECT COUNT(*) AS bills, COALESCE(SUM(B.total), 0) AS sales, "
        "COALESCE(SUM(CAST(B.total_quantity AS DECIMAL(10,0))), 0) AS quantity, "
        "COALESCE(SUM(CAST(B.cgst AS DECIMAL(10,2)) + CAST(B.sgst AS DECIMAL(10,2))), 0) AS gst, "
        "MIN(B.bill_id) AS min_id, MAX(B.bill_id) AS max_id " + BASE,
        (company_id, start, end),
    )
    row["average"] = (row["sales"] / row["bills"]) if row["bills"] else Decimal("0")
    return row


def change_percent(current, previous) -> float | None:
    if not previous:
        return None
    return float((Decimal(current) - Decimal(previous)) * 100 / Decimal(previous))


def by_payment_mode(company_id: int, start: date, end: date) -> list[dict]:
    from . import part_payment  # split (part) payments count towards each of their modes

    lines = part_payment.mode_lines("B.company_detail_id = %s AND B.payment_mode_id <> 0 AND B.billing_date BETWEEN %s AND %s")
    return db.fetch_all(
        "SELECT COALESCE(PM.payment_mode, 'Other') AS name, COUNT(DISTINCT L.bill_id) AS bills, SUM(L.amount) AS amount "
        f"FROM {lines} L LEFT JOIN bill_payment_mode PM ON PM.payment_mode_id = L.mode_id "
        "GROUP BY name ORDER BY amount DESC",
        (company_id, start, end) * 2,
    )


def sales_series(company_id: int, start: date, end: date) -> list[dict]:
    """Daily points for short ranges, monthly for long ones (gaps filled with 0)."""
    span = (end - start).days
    if span < 1:
        start = end - timedelta(days=6)  # a single day plots the surrounding week
        span = 6
    if span <= 62:
        rows = db.fetch_all(
            "SELECT B.billing_date AS d, SUM(B.total) AS amount, COUNT(*) AS bills " + BASE + " GROUP BY B.billing_date",
            (company_id, start, end),
        )
        found = {r["d"]: r for r in rows}
        points = []
        day = start
        while day <= end:
            r = found.get(day)
            points.append({"label": day.strftime("%d %b"), "amount": float(r["amount"]) if r else 0.0, "bills": r["bills"] if r else 0})
            day += timedelta(days=1)
        return points
    rows = db.fetch_all(
        "SELECT DATE_FORMAT(B.billing_date, '%%Y-%%m') AS m, SUM(B.total) AS amount, COUNT(*) AS bills "
        + BASE + " GROUP BY m",
        (company_id, start, end),
    )
    found = {r["m"]: r for r in rows}
    points = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        key = f"{y:04d}-{m:02d}"
        r = found.get(key)
        points.append({"label": date(y, m, 1).strftime("%b %Y"), "amount": float(r["amount"]) if r else 0.0, "bills": r["bills"] if r else 0})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return points


def top_items(company_id: int, start: date, end: date, min_id, max_id, limit: int = 8) -> list[dict]:
    if not min_id:
        return []
    return db.fetch_all(
        "SELECT BD.item_name AS name, SUM(BD.quantity) AS quantity, SUM(BD.rate * BD.quantity) AS amount "
        "FROM bill_detail BD JOIN bill B ON B.bill_id = BD.bill_id "
        "WHERE BD.bill_id BETWEEN %s AND %s AND B.company_detail_id = %s AND B.payment_mode_id <> 0 "
        "AND B.billing_date BETWEEN %s AND %s "
        "GROUP BY BD.item_name ORDER BY quantity DESC LIMIT %s",
        (min_id, max_id, company_id, start, end, limit),
    )


def recent_bills(company_id: int, limit: int = 6) -> list[dict]:
    return db.fetch_all(
        "SELECT B.bill_id AS id, B.bill_no, B.billing_date, B.total, B.customer_name, "
        "(SELECT PM.payment_mode FROM bill_payment_mode PM WHERE PM.payment_mode_id = B.payment_mode_id) AS payment_mode "
        "FROM bill B WHERE B.company_detail_id = %s AND B.payment_mode_id <> 0 ORDER BY B.bill_id DESC LIMIT %s",
        (company_id, limit),
    )


def menu_counts(company_id: int) -> dict:
    return db.fetch_one(
        "SELECT "
        "(SELECT COUNT(*) FROM menu_category WHERE company_detail_id = %s) AS categories, "
        "(SELECT COUNT(*) FROM menu_category WHERE company_detail_id = %s AND active = 1) AS active_categories, "
        "(SELECT COUNT(*) FROM menu M JOIN menu_category MC ON MC.menu_category_id = M.menu_category_id WHERE MC.company_detail_id = %s) AS item_count, "
        "(SELECT COUNT(*) FROM menu M JOIN menu_category MC ON MC.menu_category_id = M.menu_category_id WHERE MC.company_detail_id = %s AND M.active = 1 AND MC.active = 1) AS active_items",
        (company_id,) * 4,
    )
