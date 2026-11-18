"""Sales reports. Each report returns the same shape so one view and one
Excel exporter handle all of them:

    {"title", "columns": [(key, label, kind)], "rows": [...], "totals": {...}}

kind is "text", "int" or "money".
"""
from datetime import date

from .. import db

REPORTS = {
    "daily": "Daily sales",
    "daily_sale": "Daily sale (item-wise)",
    "daily_consolidated": "Daily sale consolidated",
    "items": "Item-wise sales",
    "categories": "Category-wise sales",
    "payments": "Payment mode summary",
    "order_types": "Dine-in / Pickup / Delivery",
    "counters": "Counter / staff-wise sales",
    "online_orders": "Online orders",
    "ingredient_stock": "Ingredient purchase & stock",
    "item_consumption": "Item consumption (by recipe)",
    "gst_monthly": "Monthly GST (CGST / SGST)",
    "period_sales": "Quarterly / yearly sales",
}
PERIODS = {"quarter": "Quarterly", "year": "Yearly"}
QUARTER_MONTHS = {1: "Apr-Jun", 2: "Jul-Sep", 3: "Oct-Dec", 4: "Jan-Mar"}
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fy_start(day: date) -> date:
    """1 April of the Indian financial year that `day` falls in."""
    return date(day.year if day.month >= 4 else day.year - 1, 4, 1)


def default_start(kind: str, end: date) -> date | None:
    """Reports over a longer span open on a longer range."""
    if kind == "period_sales":
        return fy_start(end).replace(year=fy_start(end).year - 2)  # this FY and the two before
    return None

# kind "qty" = quantity with up to 3 decimals (ingredients are measured in KG, LTR, ...)
COUNTER_NAME = ("COALESCE(AR.name, IF(B.counter_no = 0, 'Super admin', CONCAT('Counter ', B.counter_no)))")

BILLS = "bill B WHERE B.company_detail_id = %s AND B.payment_mode_id <> 0 AND B.billing_date BETWEEN %s AND %s"
GST_SUM = "CAST(B.cgst AS DECIMAL(10,2)) + CAST(B.sgst AS DECIMAL(10,2))"


def _bill_range(company_id: int, start: date, end: date):
    row = db.fetch_one(f"SELECT MIN(B.bill_id) AS lo, MAX(B.bill_id) AS hi FROM {BILLS}", (company_id, start, end))
    return row["lo"], row["hi"]


def _totals(rows, columns):
    totals = {}
    for key, _label, kind in columns:
        if kind in ("int", "money"):
            totals[key] = sum((r[key] or 0) for r in rows)
    return totals


SOURCES = {"": "Offline + Online", "offline": "Offline (counter / tables)", "online": "Online (customer app)"}
ONLINE_OK = ("O.payment_status = 1 AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(O.status, '$[0].status')), '') <> 'cancelled'")


def _online_vendor(company_id):
    from . import online_orders
    return online_orders.vendor_for(company_id)


def _day_range(start, end):
    from datetime import datetime, time
    return datetime.combine(start, time.min), datetime.combine(end, time.max)


def _daily_sale(company_id, start, end, source=""):
    """Old 'Daily Sale': quantity and value of every item per day, for counter/table bills (offline)
    and paid customer-app orders (online)."""
    columns = [("day", "Billing Date", "text"), ("source", "Source", "text"), ("counter", "Counter", "text"), ("name", "Item Name", "text"),
               ("quantity", "Sold", "int"), ("amount", "Sale Amount", "money"), ("gst", "GST", "money"), ("total", "Total", "money")]
    rows = []
    if source in ("", "offline"):
        lo, hi = _bill_range(company_id, start, end)
        if lo is not None:
            rows += db.fetch_all(
                f"SELECT B.billing_date AS day, 'Offline' AS source, {COUNTER_NAME} AS counter, BD.item_name AS name, SUM(BD.quantity) AS quantity, "
                "SUM(BD.quantity * BD.rate) AS amount, SUM(ROUND(BD.quantity * BD.rate * BD.item_gst / 100, 2)) AS gst, "
                "SUM(BD.quantity * BD.rate) + SUM(ROUND(BD.quantity * BD.rate * BD.item_gst / 100, 2)) AS total "
                "FROM bill_detail BD JOIN bill B ON B.bill_id = BD.bill_id "
                "LEFT JOIN aspirant_registration AR ON AR.aspirant_registration_id = B.counter_no "
                "WHERE BD.bill_id BETWEEN %s AND %s AND B.company_detail_id = %s AND B.payment_mode_id <> 0 "
                "AND B.billing_date BETWEEN %s AND %s GROUP BY B.billing_date, B.counter_no, AR.name, BD.item_name",
                (lo, hi, company_id, start, end))
    vendor = _online_vendor(company_id)
    if source in ("", "online") and vendor is not None:
        rows += db.fetch_all(
            "SELECT DATE(O.created_at) AS day, 'Online' AS source, 'Customer app' AS counter, OI.item_name AS name, "
            "SUM(OI.quantity) AS quantity, SUM(OI.rate * OI.quantity) AS amount, SUM(OI.total - OI.rate * OI.quantity) AS gst, "
            "SUM(OI.total) AS total FROM order_items OI JOIN orders O ON O.order_id = OI.order_id "
            "WHERE O.vendor_id = %s AND " + ONLINE_OK + " AND O.created_at BETWEEN %s AND %s GROUP BY DATE(O.created_at), OI.item_name",
            (vendor, *_day_range(start, end)))
    rows.sort(key=lambda r: (r["source"] == "Online", r["counter"] or "", r["name"] or ""))
    rows.sort(key=lambda r: r["day"], reverse=True)
    for r in rows:
        r["day"] = r["day"].strftime("%Y-%m-%d") if r["day"] else ""
    return columns, rows


def _daily_consolidated(company_id, start, end, source=""):
    """Old 'Daily Sale Consolidated': totals per day and counter, one column per payment mode, plus a
    row per day for paid customer-app orders. (The PHP version had Cash/UPI ids of one restaurant hard-coded.)"""
    from . import payment

    from . import part_payment

    modes = payment.payment_modes(company_id)
    split_join, split_expr = part_payment.mode_columns_join([m["id"] for m in modes])
    columns = [("day", "Billing Date", "text"), ("source", "Source", "text"), ("counter", "Counter", "text"), ("bills", "Bills / Orders", "int")]
    cases = []
    for m in modes:
        key = f"mode_{m['id']}"
        columns.append((key, m["name"], "money"))
        cases.append(f"SUM({split_expr[int(m['id'])]}) AS {key}")
    columns += [("online_pay", "Online (app)", "money"), ("discount", "Discount", "money"), ("gst", "GST", "money"), ("total", "Total Sales", "money")]
    rows = []
    if source in ("", "offline"):
        rows += db.fetch_all(
            f"SELECT B.billing_date AS day, 'Offline' AS source, {COUNTER_NAME} AS counter, COUNT(*) AS bills, "
            + "".join(c + ", " for c in cases)
            + f"0 AS online_pay, 0 AS discount, SUM({GST_SUM}) AS gst, SUM(B.total) AS total "
            f"FROM bill B LEFT JOIN aspirant_registration AR ON AR.aspirant_registration_id = B.counter_no {split_join} "
            f"WHERE {BILLS.split('WHERE ', 1)[1]} GROUP BY B.billing_date, B.counter_no, AR.name",
            (company_id, start, end))
    vendor = _online_vendor(company_id)
    if source in ("", "online") and vendor is not None:
        zero_modes = "".join(f"0 AS mode_{int(m['id'])}, " for m in modes)
        rows += db.fetch_all(
            "SELECT DATE(O.created_at) AS day, 'Online' AS source, 'Customer app' AS counter, COUNT(*) AS bills, " + zero_modes
            + "SUM(O.grand_total) AS online_pay, SUM(O.discount) AS discount, SUM(O.tax) AS gst, SUM(O.grand_total) AS total "
            "FROM orders O WHERE O.vendor_id = %s AND " + ONLINE_OK + " AND O.created_at BETWEEN %s AND %s GROUP BY DATE(O.created_at)",
            (vendor, *_day_range(start, end)))
    rows.sort(key=lambda r: (r["day"], r["source"] == "Offline"), reverse=True)
    for r in rows:
        r["day"] = r["day"].strftime("%Y-%m-%d") if r["day"] else ""
    return columns, rows


def _ingredient_stock(company_id, start, end):
    """Old 'Ingredient Purchase': opening stock + purchased - issued = balance, for any period
    (the PHP version was fixed to July 2023)."""
    columns = [("name", "Ingredient", "text"), ("category", "Category", "text"), ("unit", "Unit", "text"),
               ("opening", "Opening Stock", "qty"), ("purchased", "Purchased", "qty"), ("issued", "Issued", "qty"),
               ("balance", "Balance", "qty"), ("cost", "Purchase Cost", "money"), ("gst", "GST", "money")]
    rows = db.fetch_all(
        "SELECT I.ingredient_name AS name, IC.ingredient_category_name AS category, I.ingredient_unit AS unit, "
        "COALESCE(OS.opening_stock, 0) + COALESCE(PB.qty, 0) - COALESCE(IB.qty, 0) AS opening, "
        "COALESCE(P.qty, 0) AS purchased, COALESCE(S.qty, 0) AS issued, "
        "COALESCE(OS.opening_stock, 0) + COALESCE(PB.qty, 0) - COALESCE(IB.qty, 0) + COALESCE(P.qty, 0) - COALESCE(S.qty, 0) AS balance, "
        "COALESCE(P.cost, 0) AS cost, COALESCE(P.gst, 0) AS gst "
        "FROM ingredient I JOIN ingredient_category IC ON IC.ingredient_category_id = I.ingredient_category_id "
        # opening stock entered most recently on or before the start date
        "LEFT JOIN (SELECT O.ingredient_id, O.opening_stock, O.effective_from FROM ingredient_opening_stock O "
        "  JOIN (SELECT ingredient_id, MAX(effective_from) AS ef FROM ingredient_opening_stock "
        "        WHERE company_detail_id = %s AND effective_from <= %s GROUP BY ingredient_id) L "
        "    ON L.ingredient_id = O.ingredient_id AND L.ef = O.effective_from WHERE O.company_detail_id = %s) OS "
        "  ON OS.ingredient_id = I.ingredient_id "
        # movements between that opening stock and the start date
        "LEFT JOIN (SELECT D.ingredient_id, SUM(D.quantity) AS qty FROM procurement_note_detail D "
        "  JOIN procurement_note N ON N.procurement_note_id = D.procurement_note_id "
        "  WHERE N.company_detail_id = %s AND N.procurement_date < %s AND N.procurement_date > '1000-01-01' GROUP BY D.ingredient_id) PB "
        "  ON PB.ingredient_id = I.ingredient_id "
        "LEFT JOIN (SELECT D.ingredient_id, SUM(D.quantity) AS qty FROM issuing_note_detail D "
        "  JOIN issuing_note N ON N.issuing_note_id = D.issuing_note_id "
        "  WHERE N.company_detail_id = %s AND N.issuing_date < %s AND N.issuing_date > '1000-01-01' GROUP BY D.ingredient_id) IB "
        "  ON IB.ingredient_id = I.ingredient_id "
        # movements inside the period
        "LEFT JOIN (SELECT D.ingredient_id, SUM(D.quantity) AS qty, SUM(D.quantity * D.rate) AS cost, "
        "  SUM(ROUND(D.quantity * D.rate * D.ingredient_gst / 100, 2)) AS gst FROM procurement_note_detail D "
        "  JOIN procurement_note N ON N.procurement_note_id = D.procurement_note_id "
        "  WHERE N.company_detail_id = %s AND N.procurement_date BETWEEN %s AND %s GROUP BY D.ingredient_id) P "
        "  ON P.ingredient_id = I.ingredient_id "
        "LEFT JOIN (SELECT D.ingredient_id, SUM(D.quantity) AS qty FROM issuing_note_detail D "
        "  JOIN issuing_note N ON N.issuing_note_id = D.issuing_note_id "
        "  WHERE N.company_detail_id = %s AND N.issuing_date BETWEEN %s AND %s GROUP BY D.ingredient_id) S "
        "  ON S.ingredient_id = I.ingredient_id "
        "WHERE IC.company_detail_id = %s "
        "AND (OS.opening_stock IS NOT NULL OR PB.qty IS NOT NULL OR IB.qty IS NOT NULL OR P.qty IS NOT NULL OR S.qty IS NOT NULL) "
        "ORDER BY I.ingredient_name",
        (company_id, start, company_id, company_id, start, company_id, start,
         company_id, start, end, company_id, start, end, company_id),
    )
    return columns, rows


def _online_orders(company_id, start, end):
    """Old 'Online Orders' report: one row per customer-app order."""
    from datetime import datetime, time
    from . import online_orders as oo

    columns = [("order", "Order No", "text"), ("mobile", "Customer Mobile", "text"), ("day", "Billing Date", "text"),
               ("quantity", "Total Qty", "int"), ("items", "Items", "text"), ("status", "Status", "text"),
               ("payment", "Payment Status", "text"), ("amount", "Sale Amount", "money"), ("discount", "Discount", "money"),
               ("gst", "GST", "money"), ("delivery", "Delivery Charges", "money"), ("net", "Net Sales", "money")]
    vendor = oo.vendor_for(company_id)
    if vendor is None:
        return columns, []
    rows = db.fetch_all(
        "SELECT O.order_id, O.created_at, O.status, O.payment_status, O.total, O.discount, O.tax, O.delivery_charge, O.grand_total, "
        "COALESCE(NULLIF(C.customer_mobile_no, ''), O.mobile) AS mobile, "
        "(SELECT SUM(quantity) FROM order_items I WHERE I.order_id = O.order_id) AS quantity, "
        "(SELECT GROUP_CONCAT(CONCAT(item_name, ' (Qty: ', quantity, ', Rate: ', rate, ')') ORDER BY order_item_id SEPARATOR '; ') "
        "   FROM order_items I WHERE I.order_id = O.order_id) AS items "
        "FROM orders O LEFT JOIN customers C ON C.customer_id = O.uid "
        "WHERE O.vendor_id = %s AND O.created_at BETWEEN %s AND %s ORDER BY O.order_id DESC",
        (vendor, datetime.combine(start, time.min), datetime.combine(end, time.max)),
    )
    out = []
    for r in rows:
        status = oo._status_of(r["status"])
        out.append({"order": f"#{r['order_id']}", "mobile": r["mobile"] or "", "day": r["created_at"].strftime("%Y-%m-%d") if r["created_at"] else "",
                    "quantity": int(r["quantity"] or 0), "items": r["items"] or "", "status": oo.STATUSES.get(status, status.title()),
                    "payment": oo.PAYMENT.get(r["payment_status"], "Unknown"), "amount": r["total"] or 0, "discount": r["discount"] or 0,
                    "gst": r["tax"] or 0, "delivery": r["delivery_charge"] or 0, "net": r["grand_total"] or 0})
    return columns, out


def _item_consumption(company_id, start, end):
    """Ingredients used, worked out from items sold x their recipe (Consumption Formula).
    The old menu listed this report but it was never built."""
    lo, hi = _bill_range(company_id, start, end)
    columns = [("name", "Ingredient", "text"), ("unit", "Unit", "text"), ("used", "Used (by recipe)", "qty"),
               ("items", "Made from", "text")]
    rows = [] if lo is None else db.fetch_all(
        "SELECT CF.ingredient_name AS name, CF.ingredient_unit AS unit, SUM(BD.quantity * CF.quantity) AS used, "
        "GROUP_CONCAT(DISTINCT BD.item_name ORDER BY BD.item_name SEPARATOR ', ') AS items "
        "FROM bill_detail BD JOIN bill B ON B.bill_id = BD.bill_id "
        "JOIN consumption_formula CF ON CF.item_id = BD.item_id "
        "WHERE BD.bill_id BETWEEN %s AND %s AND B.company_detail_id = %s AND B.payment_mode_id <> 0 "
        "AND B.billing_date BETWEEN %s AND %s GROUP BY CF.ingredient_id, CF.ingredient_name, CF.ingredient_unit ORDER BY CF.ingredient_name",
        (lo, hi, company_id, start, end),
    )
    return columns, rows


def _gst_monthly(company_id, start, end, source=""):
    """GST per month and tax rate, ready for GSTR-1 / GSTR-3B. Counter and table bills are taken
    line by line (each item carries its own GST %); customer-app orders by order."""
    from decimal import ROUND_HALF_UP, Decimal

    columns = [("month", "Month", "text"), ("source", "Source", "text"), ("rate", "GST rate", "text"), ("bills", "Bills / Orders", "int"),
               ("taxable", "Taxable value", "money"), ("cgst", "CGST", "money"), ("sgst", "SGST", "money"),
               ("gst", "Total GST", "money"), ("total", "Invoice value", "money")]
    rows = []
    if source in ("", "offline"):
        lo, hi = _bill_range(company_id, start, end)
        if lo is not None:
            # The CGST / SGST printed on each bill are what was charged; they are shared across the
            # bill's tax rates in proportion to each rate's line GST, so the report matches the invoices.
            line_gst = "ROUND(BD.rate * BD.quantity * BD.item_gst / 100, 2)"
            rows += db.fetch_all(
                "SELECT y, m, 'Offline' AS source, pct, COUNT(DISTINCT bill_id) AS bills, SUM(taxable) AS taxable, "
                "SUM(IF(all_gst > 0, hc * gst / all_gst, IF(rates = 1, hc, 0))) AS cgst, "
                "SUM(IF(all_gst > 0, hs * gst / all_gst, IF(rates = 1, hs, 0))) AS sgst FROM ("
                " SELECT B.bill_id, YEAR(B.billing_date) AS y, MONTH(B.billing_date) AS m, BD.item_gst AS pct, "
                f" SUM(BD.rate * BD.quantity) AS taxable, SUM({line_gst}) AS gst, T.all_gst, T.rates, "
                " CAST(B.cgst AS DECIMAL(12,2)) AS hc, CAST(B.sgst AS DECIMAL(12,2)) AS hs "
                " FROM bill_detail BD JOIN bill B ON B.bill_id = BD.bill_id "
                f" JOIN (SELECT BD.bill_id, SUM({line_gst}) AS all_gst, COUNT(DISTINCT BD.item_gst) AS rates FROM bill_detail BD "
                "       WHERE BD.bill_id BETWEEN %s AND %s GROUP BY BD.bill_id) T ON T.bill_id = B.bill_id "
                " WHERE BD.bill_id BETWEEN %s AND %s AND B.company_detail_id = %s AND B.payment_mode_id <> 0 "
                " AND B.billing_date BETWEEN %s AND %s GROUP BY B.bill_id, BD.item_gst"
                ") X GROUP BY y, m, pct",
                (lo, hi, lo, hi, company_id, start, end))
    vendor = _online_vendor(company_id)
    if source in ("", "online") and vendor is not None:
        # Online: taxable = items total - discount, GST = tax; the rate is worked out per order.
        rows += db.fetch_all(
            "SELECT y, m, 'Online' AS source, pct, COUNT(*) AS bills, SUM(taxable) AS taxable, SUM(gst) AS gst FROM ("
            " SELECT YEAR(O.created_at) AS y, MONTH(O.created_at) AS m, COALESCE(O.total, 0) - COALESCE(O.discount, 0) AS taxable, "
            " COALESCE(O.tax, 0) AS gst, IF(COALESCE(O.total, 0) - COALESCE(O.discount, 0) > 0, "
            "    ROUND(COALESCE(O.tax, 0) * 100 / (COALESCE(O.total, 0) - COALESCE(O.discount, 0))), 0) AS pct "
            " FROM orders O WHERE O.vendor_id = %s AND " + ONLINE_OK + " AND O.created_at BETWEEN %s AND %s"
            ") X GROUP BY y, m, pct",
            (vendor, *_day_range(start, end)))
    two = Decimal("0.01")
    for r in rows:
        taxable = Decimal(str(r["taxable"] or 0)).quantize(two)
        if "cgst" in r:   # offline: from the bills
            r["cgst"] = Decimal(str(r["cgst"] or 0)).quantize(two, rounding=ROUND_HALF_UP)
            r["sgst"] = Decimal(str(r["sgst"] or 0)).quantize(two, rounding=ROUND_HALF_UP)
        else:             # online: the order's tax, split half and half
            tax = Decimal(str(r["gst"] or 0)).quantize(two)
            r["cgst"] = (tax / 2).quantize(two, rounding=ROUND_HALF_UP)
            r["sgst"] = tax - r["cgst"]
        r["gst"] = r["cgst"] + r["sgst"]
        r["taxable"], r["total"] = taxable, taxable + r["gst"]
        r["_key"] = (r["y"], r["m"], r["source"] == "Online", float(r["pct"] or 0))
        r["month"] = f"{MONTH_NAMES[r['m'] - 1]} {r['y']}"
        r["rate"] = "{:g}%".format(float(r["pct"] or 0))
    rows.sort(key=lambda r: r["_key"])
    return columns, rows


def _period_sales(company_id, start, end, source="", period="quarter"):
    """Sales per financial-year quarter (Q1 = Apr-Jun) or per financial year, with growth
    against the previous period of the same source."""
    from decimal import Decimal

    container = "COALESCE(B.container_charges, 0)" if db.column_exists("bill", "container_charges") else "0"
    columns = [("period", "Quarter" if period == "quarter" else "Financial year", "text"), ("source", "Source", "text"),
               ("bills", "Bills / Orders", "int"), ("quantity", "Items sold", "int"), ("taxable", "Sales before GST", "money"),
               ("discount", "Discount", "money"), ("cgst", "CGST", "money"), ("sgst", "SGST", "money"),
               ("extra", "Other charges", "money"), ("total", "Total sales", "money"), ("growth", "Growth", "text")]
    fy = "YEAR({d}) - (MONTH({d}) < 4)"
    qtr = "MOD(MONTH({d}) + 8, 12) DIV 3 + 1"
    rows = []
    if source in ("", "offline"):
        d = "B.billing_date"
        q = qtr.format(d=d) if period == "quarter" else "0"
        rows += db.fetch_all(
            f"SELECT {fy.format(d=d)} AS fy, {q} AS q, 'Offline' AS source, COUNT(*) AS bills, "
            "SUM(CAST(B.total_quantity AS DECIMAL(10,0))) AS quantity, 0 AS discount, "
            "SUM(CAST(B.cgst AS DECIMAL(10,2))) AS cgst, SUM(CAST(B.sgst AS DECIMAL(10,2))) AS sgst, "
            f"SUM(B.service_cost + {container}) AS extra, SUM(B.total) AS total "
            f"FROM {BILLS} GROUP BY fy, q",
            (company_id, start, end))
    vendor = _online_vendor(company_id)
    if source in ("", "online") and vendor is not None:
        d = "O.created_at"
        q = qtr.format(d=d) if period == "quarter" else "0"
        rows += db.fetch_all(
            f"SELECT {fy.format(d=d)} AS fy, {q} AS q, 'Online' AS source, COUNT(*) AS bills, "
            "SUM((SELECT SUM(quantity) FROM order_items I WHERE I.order_id = O.order_id)) AS quantity, "
            "SUM(COALESCE(O.discount, 0)) AS discount, SUM(COALESCE(O.tax, 0)) / 2 AS cgst, SUM(COALESCE(O.tax, 0)) / 2 AS sgst, "
            "SUM(COALESCE(O.delivery_charge, 0) + COALESCE(O.container_charges, 0)) AS extra, SUM(COALESCE(O.grand_total, 0)) AS total "
            "FROM orders O WHERE O.vendor_id = %s AND " + ONLINE_OK + " AND O.created_at BETWEEN %s AND %s GROUP BY fy, q",
            (vendor, *_day_range(start, end)))
    two = Decimal("0.01")
    for r in rows:
        for k in ("discount", "cgst", "sgst", "extra", "total"):
            r[k] = Decimal(str(r[k] or 0)).quantize(two)
        r["fy"], r["q"] = int(r["fy"]), int(r["q"])
        r["taxable"] = r["total"] - r["cgst"] - r["sgst"] - r["extra"]
        label = f"FY {r['fy']}-{str(r['fy'] + 1)[-2:]}"
        # Quarters of the Indian financial year; Jan-Mar (Q4) falls in the next calendar year.
        r["period"] = (f"Q{r['q']} · {QUARTER_MONTHS[r['q']]} {r['fy'] + (1 if r['q'] == 4 else 0)}"
                       if period == "quarter" else label)
    rows.sort(key=lambda r: (r["fy"], r["q"], r["source"] == "Online"))
    last = {}
    for r in rows:
        prev = last.get(r["source"])
        r["growth"] = "{:+.1f}%".format(float((r["total"] - prev) * 100 / prev)) if prev else ""
        last[r["source"]] = r["total"]
    return columns, rows


SOURCE_REPORTS = {"daily_sale", "daily_consolidated", "gst_monthly", "period_sales"}


def build(kind: str, company_id: int, start: date, end: date, source: str = "", period: str = "quarter") -> dict:
    if kind not in REPORTS:
        kind = "daily"
    params = (company_id, start, end)
    source = source if source in SOURCES else ""
    period = period if period in PERIODS else "quarter"

    builders = {"daily_sale": _daily_sale, "daily_consolidated": _daily_consolidated, "gst_monthly": _gst_monthly,
                "ingredient_stock": _ingredient_stock, "item_consumption": _item_consumption, "online_orders": _online_orders}
    if kind == "period_sales":
        columns, rows = _period_sales(company_id, start, end, source, period)
    elif kind in SOURCE_REPORTS:
        columns, rows = builders[kind](company_id, start, end, source)
    elif kind in builders:
        columns, rows = builders[kind](company_id, start, end)

    elif kind == "daily":
        columns = [("day", "Date", "text"), ("bills", "Bills", "int"), ("quantity", "Items", "int"),
                   ("cgst", "CGST", "money"), ("sgst", "SGST", "money"), ("service", "Service", "money"), ("total", "Total", "money")]
        rows = db.fetch_all(
            "SELECT B.billing_date AS day, COUNT(*) AS bills, SUM(CAST(B.total_quantity AS DECIMAL(10,0))) AS quantity, "
            "SUM(CAST(B.cgst AS DECIMAL(10,2))) AS cgst, SUM(CAST(B.sgst AS DECIMAL(10,2))) AS sgst, "
            f"SUM(B.service_cost) AS service, SUM(B.total) AS total FROM {BILLS} GROUP BY B.billing_date ORDER BY B.billing_date",
            params,
        )
        for r in rows:
            r["day"] = r["day"].strftime("%Y-%m-%d") if r["day"] else ""

    elif kind in ("items", "categories"):
        lo, hi = _bill_range(company_id, start, end)
        if kind == "items":
            columns = [("name", "Item", "text"), ("category", "Category", "text"), ("quantity", "Quantity", "int"),
                       ("amount", "Sales (before GST)", "money"), ("gst", "GST", "money"), ("total", "Total", "money")]
            group, label = "BD.item_name, MC.menu_category_name", "BD.item_name AS name, COALESCE(MC.menu_category_name, '(deleted item)') AS category"
        else:
            columns = [("category", "Category", "text"), ("items", "Different items", "int"), ("quantity", "Quantity", "int"),
                       ("amount", "Sales (before GST)", "money"), ("gst", "GST", "money"), ("total", "Total", "money")]
            group, label = "MC.menu_category_name", "COALESCE(MC.menu_category_name, '(deleted items)') AS category, COUNT(DISTINCT BD.item_name) AS items"
        rows = [] if lo is None else db.fetch_all(
            f"SELECT {label}, SUM(BD.quantity) AS quantity, SUM(BD.rate * BD.quantity) AS amount, "
            "SUM(ROUND(BD.rate * BD.quantity * BD.item_gst / 100, 2)) AS gst, "
            "SUM(BD.rate * BD.quantity) + SUM(ROUND(BD.rate * BD.quantity * BD.item_gst / 100, 2)) AS total "
            "FROM bill_detail BD JOIN bill B ON B.bill_id = BD.bill_id "
            "LEFT JOIN menu M ON M.item_id = BD.item_id LEFT JOIN menu_category MC ON MC.menu_category_id = M.menu_category_id "
            "WHERE BD.bill_id BETWEEN %s AND %s AND B.company_detail_id = %s AND B.payment_mode_id <> 0 "
            f"AND B.billing_date BETWEEN %s AND %s GROUP BY {group} ORDER BY quantity DESC",
            (lo, hi, company_id, start, end),
        )

    elif kind == "payments":
        columns = [("name", "Payment mode", "text"), ("bills", "Bills", "int"), ("gst", "GST", "money"), ("total", "Total", "money")]
        from . import part_payment  # a part-paid bill counts under each of its modes

        lines = part_payment.mode_lines("B.company_detail_id = %s AND B.payment_mode_id <> 0 AND B.billing_date BETWEEN %s AND %s")
        rows = db.fetch_all(
            "SELECT COALESCE(PM.payment_mode, 'Other') AS name, COUNT(DISTINCT L.bill_id) AS bills, "
            f"SUM(L.gst) AS gst, SUM(L.amount) AS total FROM {lines} L "
            "LEFT JOIN bill_payment_mode PM ON PM.payment_mode_id = L.mode_id "
            "GROUP BY name ORDER BY total DESC",
            list(params) * 2,
        )

    elif kind == "order_types":
        columns = [("name", "Order type", "text"), ("bills", "Bills", "int"), ("quantity", "Items", "int"),
                   ("service", "Service + container", "money"), ("total", "Total", "money")]
        rows = db.fetch_all(
            "SELECT ELT(B.order_placement_type, 'Dine-in', 'Pickup', 'Delivery') AS name, COUNT(*) AS bills, "
            "SUM(CAST(B.total_quantity AS DECIMAL(10,0))) AS quantity, SUM(B.service_cost) AS service, SUM(B.total) AS total "
            f"FROM {BILLS} GROUP BY B.order_placement_type ORDER BY B.order_placement_type",
            params,
        )
        for r in rows:
            r["name"] = r["name"] or "Other"

    else:  # counters
        columns = [("name", "Counter / staff", "text"), ("bills", "Bills", "int"), ("quantity", "Items", "int"), ("total", "Total", "money")]
        rows = db.fetch_all(
            "SELECT COALESCE(AR.name, IF(B.counter_no = 0, 'Super admin', CONCAT('Counter ', B.counter_no))) AS name, "
            "COUNT(*) AS bills, SUM(CAST(B.total_quantity AS DECIMAL(10,0))) AS quantity, SUM(B.total) AS total "
            "FROM bill B LEFT JOIN aspirant_registration AR ON AR.aspirant_registration_id = B.counter_no "
            "WHERE B.company_detail_id = %s AND B.payment_mode_id <> 0 AND B.billing_date BETWEEN %s AND %s "
            "GROUP BY B.counter_no, AR.name ORDER BY total DESC",
            params,
        )

    if kind == "period_sales":
        title = f"{PERIODS[period]} sales" + (f" - {SOURCES[source]}" if source else "")
    else:
        title = REPORTS[kind] + (f" - {SOURCES[source]}" if kind in SOURCE_REPORTS and source else "")
    return {"kind": kind, "title": title, "columns": columns, "rows": rows, "totals": _totals(rows, columns),
            "source": source, "has_source": kind in SOURCE_REPORTS,
            "period": period if kind == "period_sales" else None}
