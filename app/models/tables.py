"""Table service (dine-in by table): tables, running orders, KOTs, bills, settlement.

Uses the PHP app's tables so the data stays shared:
  serving_table          the tables (name, company, active)
  table_order            one row per KOT (kitchen order ticket)
  table_order_detail     the items on each KOT
  bill / bill_detail     the bill, created when the bill is printed

How table_order columns are used here:
  active      1 = order still open on the table, 0 = settled/closed
  kot_status  1 = KOT printed for the kitchen, 0 = saved but not printed yet
              An open order is always active = 1 AND kot_status IN (0, 1). Old PHP rows
              use kot_status 2 (and active 1 or 2), so they never show up as running orders.
  bill_id     the bill this KOT belongs to once a bill has been printed
  create_bill 1 = included in the bill as last printed, 0 = added/changed after that

Two small tables of our own add what the PHP schema lacks:
  ks_table_area   dining area of each table (AC, Garden, ...)
  ks_kot_note     a kitchen note on a KOT line ("less spicy")
"""
import logging
import re
import threading
from collections import OrderedDict
from datetime import timedelta
from decimal import Decimal

from .. import db
from ..errors import NotFoundError, ValidationError
from ..services import billing
from ..utils import financial_period, now, to_int
from . import bill as bill_model
from . import company as company_model
from . import menu_item, part_payment, payment

log = logging.getLogger("khatashop.models.tables")

DEFAULT_AREA = "Other"
NAME_MAX = 20          # serving_table.serving_table_name is varchar(20)
NOTE_MAX = 120
MAX_TABLES_AT_ONCE = 100

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
            "CREATE TABLE IF NOT EXISTS ks_table_area ("
            " serving_table_id INT NOT NULL PRIMARY KEY,"
            " area VARCHAR(40) NOT NULL,"
            " sort_order INT NOT NULL DEFAULT 0"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS ks_kot_ready ("
            " table_order_id INT NOT NULL PRIMARY KEY,"
            " ready_at DATETIME NOT NULL"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS ks_kot_note ("
            " table_order_detail_id INT NOT NULL PRIMARY KEY,"
            " note VARCHAR(200) NOT NULL"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
        _schema_ready = True


def _natural(text: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", text or "")]


# ------------------------------------------------------------------ tables --

def list_tables(company_id: int, include_inactive: bool = False) -> list[dict]:
    ensure_schema()
    sql = (
        "SELECT T.serving_table_id AS id, T.serving_table_name AS name, T.active, "
        f"COALESCE(A.area, '{DEFAULT_AREA}') AS area, COALESCE(A.sort_order, 0) AS sort_order "
        "FROM serving_table T LEFT JOIN ks_table_area A ON A.serving_table_id = T.serving_table_id "
        "WHERE T.company_detail_id = %s"
    )
    if not include_inactive:
        sql += " AND T.active = 1"
    rows = db.fetch_all(sql, (company_id,))
    rows.sort(key=lambda r: (r["area"] == DEFAULT_AREA, r["area"].lower(), _natural(r["name"])))
    return rows


def get_table(company_id: int, table_id: int, active_only: bool = True) -> dict:
    for t in list_tables(company_id, include_inactive=not active_only):
        if t["id"] == table_id:
            return t
    raise NotFoundError("That table does not exist.")


def parse_table_names(spec: str) -> list[str]:
    """'AC1-AC6' -> AC1..AC6, '1-20' -> 1..20, 'T1, T2, Garden' -> as listed."""
    names: list[str] = []
    for part in [p.strip() for p in re.split(r"[,\n]", spec or "") if p.strip()]:
        m = re.fullmatch(r"([A-Za-z ]*?)\s*(\d+)\s*-\s*(?:\1)?\s*(\d+)", part)
        if m:
            prefix, start, end = m.group(1).strip(), int(m.group(2)), int(m.group(3))
            if end < start:
                start, end = end, start
            if end - start + 1 > MAX_TABLES_AT_ONCE:
                raise ValidationError(f"Add at most {MAX_TABLES_AT_ONCE} tables at once.")
            names += [f"{prefix}{n}" for n in range(start, end + 1)]
        else:
            names.append(part)
    if not names:
        raise ValidationError("Enter table names, e.g. AC1-AC6 or 1-20 or T1, T2.", errors={"names": "Required."})
    if len(names) > MAX_TABLES_AT_ONCE:
        raise ValidationError(f"Add at most {MAX_TABLES_AT_ONCE} tables at once.")
    for n in names:
        if len(n) > NAME_MAX:
            raise ValidationError(f"'{n}' is longer than {NAME_MAX} characters.", errors={"names": "Name too long."})
    return names


def _clean_area(area: str) -> str:
    area = (area or "").strip() or DEFAULT_AREA
    if len(area) > 40:
        raise ValidationError("Keep the area name under 40 characters.", errors={"area": "Too long."})
    return area


def create_tables(company_id: int, area: str, spec: str) -> list[str]:
    ensure_schema()
    area = _clean_area(area)
    names = parse_table_names(spec)
    existing = {t["name"].lower() for t in list_tables(company_id, include_inactive=True)}
    clash = [n for n in names if n.lower() in existing]
    if clash:
        raise ValidationError(f"These tables already exist: {', '.join(clash[:10])}.", errors={"names": "Already exists."})
    if len({n.lower() for n in names}) != len(names):
        raise ValidationError("The same table name is listed twice.", errors={"names": "Duplicate."})
    moment = now()
    with db.transaction() as cur:
        for name in names:
            cur.execute(
                "INSERT INTO serving_table (serving_table_name, company_detail_id, billing_status, active, created, updated) "
                "VALUES (%s, %s, 2, 1, %s, %s)",
                (name, company_id, moment, moment),
            )
            cur.execute("INSERT INTO ks_table_area (serving_table_id, area) VALUES (%s, %s)", (cur.lastrowid, area))
    return names


def update_table(company_id: int, table_id: int, name: str, area: str, active: bool) -> None:
    get_table(company_id, table_id, active_only=False)
    name = (name or "").strip()
    if not name or len(name) > NAME_MAX:
        raise ValidationError(f"Table name must be 1 to {NAME_MAX} characters.")
    area = _clean_area(area)
    others = {t["name"].lower() for t in list_tables(company_id, include_inactive=True) if t["id"] != table_id}
    if name.lower() in others:
        raise ValidationError(f"Another table is already called '{name}'.")
    if not active and _open_orders(company_id, table_id):
        raise ValidationError("This table has a running order. Settle or move it before turning the table off.")
    with db.transaction() as cur:
        cur.execute("UPDATE serving_table SET serving_table_name = %s, active = %s, updated = %s WHERE serving_table_id = %s",
                    (name, 1 if active else 0, now(), table_id))
        cur.execute("INSERT INTO ks_table_area (serving_table_id, area) VALUES (%s, %s) ON DUPLICATE KEY UPDATE area = VALUES(area)",
                    (table_id, area))


def delete_table(company_id: int, table_id: int) -> None:
    t = get_table(company_id, table_id, active_only=False)
    used = db.fetch_value(
        "SELECT (SELECT COUNT(*) FROM table_order WHERE serving_table_id = %s) + "
        "(SELECT COUNT(*) FROM bill WHERE serving_table_id = %s AND company_detail_id = %s) AS n",
        (table_id, table_id, company_id), 0,
    )
    if used:
        raise ValidationError(f"Table {t['name']} has order history, so it cannot be deleted. Turn it off instead.")
    with db.transaction() as cur:
        cur.execute("DELETE FROM ks_table_area WHERE serving_table_id = %s", (table_id,))
        cur.execute("DELETE FROM serving_table WHERE serving_table_id = %s AND company_detail_id = %s", (table_id, company_id))


# ------------------------------------------------------------------- board --

def board(company_id: int) -> list[dict]:
    """Areas with their tables and live status, for the Table View."""
    ensure_schema()
    tables = list_tables(company_id)
    if not tables:
        return []
    orders = {r["sid"]: r for r in db.fetch_all(
        "SELECT K.serving_table_id AS sid, MIN(K.created) AS since, COUNT(*) AS kots, "
        "SUM(K.kot_status = 0) AS unsent, SUM(K.create_bill = 0) AS unbilled, MAX(K.bill_id) AS bill_id, "
        "SUM(K.kot_status = 1 AND R.ready_at IS NULL) AS waiting "
        "FROM table_order K LEFT JOIN ks_kot_ready R ON R.table_order_id = K.table_order_id "
        "WHERE K.company_detail_id = %s AND K.active = 1 AND K.kot_status IN (0, 1) GROUP BY K.serving_table_id",
        (company_id,),
    )}
    amounts = {}
    if orders:
        for r in db.fetch_all(
            "SELECT K.serving_table_id AS sid, SUM(D.quantity) AS qty, "
            "SUM(D.rate * D.quantity * (1 + COALESCE(M.item_gst, 0) / 100)) AS amount "
            "FROM table_order K JOIN table_order_detail D ON D.table_order_id = K.table_order_id "
            "LEFT JOIN menu M ON M.item_id = D.item_id "
            "WHERE K.company_detail_id = %s AND K.active = 1 AND K.kot_status IN (0, 1) GROUP BY K.serving_table_id",
            (company_id,),
        ):
            amounts[r["sid"]] = r
    moment = now()
    areas: "OrderedDict[str, list]" = OrderedDict()
    for t in tables:
        o = orders.get(t["id"])
        tile = {"id": t["id"], "name": t["name"], "status": "blank", "minutes": None, "amount": 0.0, "items": 0}
        if o:
            tile["status"] = _status(o)
            tile["minutes"] = max(0, int((moment - o["since"]).total_seconds() // 60)) if o["since"] else None
            a = amounts.get(t["id"])
            if a:
                tile["amount"] = float(round(a["amount"] or 0, 2))
                tile["items"] = int(a["qty"] or 0)
        areas.setdefault(t["area"], []).append(tile)
    return [{"name": name, "tables": tiles} for name, tiles in areas.items()]


def _status(o: dict) -> str:
    if o["bill_id"] and not o["unbilled"]:
        return "printed"   # bill printed, waiting for payment
    if o["unsent"]:
        return "running"   # items saved, KOT not printed yet
    if not o.get("waiting"):
        return "ready"     # every KOT marked ready by the kitchen
    return "kot"           # sent to the kitchen, still being prepared


# ------------------------------------------------------------------ orders --

def _open_orders(company_id: int, table_id: int) -> list[dict]:
    return db.fetch_all(
        "SELECT table_order_id AS id, table_order_no AS no, kot_status, bill_id, create_bill, created, "
        "(SELECT ready_at FROM ks_kot_ready R WHERE R.table_order_id = table_order.table_order_id) AS ready_at "
        "FROM table_order WHERE company_detail_id = %s AND serving_table_id = %s AND active = 1 AND kot_status IN (0, 1) ORDER BY table_order_id",
        (company_id, table_id),
    )


def order_state(company_id: int, table_id: int) -> dict:
    """Everything the table screen needs: KOTs with their lines, the bill (if printed) and totals."""
    ensure_schema()
    table = get_table(company_id, table_id)
    kots = _open_orders(company_id, table_id)
    lines = []
    if kots:
        ids = [k["id"] for k in kots]
        marks = ",".join(["%s"] * len(ids))
        lines = db.fetch_all(
            "SELECT D.table_order_detail_id AS id, D.table_order_id AS kot_id, D.item_id, D.item_name AS name, D.rate, "
            "D.quantity, COALESCE(M.item_gst, 0) AS gst, N.note "
            "FROM table_order_detail D LEFT JOIN menu M ON M.item_id = D.item_id "
            "LEFT JOIN ks_kot_note N ON N.table_order_detail_id = D.table_order_detail_id "
            f"WHERE D.table_order_id IN ({marks}) ORDER BY D.table_order_detail_id",
            ids,
        )
    by_kot = {k["id"]: [] for k in kots}
    for line in lines:
        by_kot[line["kot_id"]].append({
            "id": line["id"], "item_id": line["item_id"], "name": line["name"], "rate": float(line["rate"]),
            "quantity": line["quantity"], "gst": float(line["gst"]), "note": line["note"] or "",
        })
    bill_id = max([k["bill_id"] for k in kots] + [0])
    bill = None
    if bill_id:
        bill = db.fetch_one("SELECT bill_id AS id, bill_no, total, customer_name, customer_mb_no FROM bill WHERE bill_id = %s", (bill_id,))
        if bill:
            bill["total"] = float(bill["total"])
            bill.update(part_payment.summary(bill["id"], bill["total"]))
    o = {"bill_id": bill_id, "unbilled": sum(1 for k in kots if not k["create_bill"]), "unsent": sum(1 for k in kots if not k["kot_status"]),
         "waiting": sum(1 for k in kots if k["kot_status"] and not k["ready_at"])}
    moment = now()
    return {
        "table": {"id": table["id"], "name": table["name"], "area": table["area"]},
        "status": _status(o) if kots else "blank",
        "since_minutes": int((moment - kots[0]["created"]).total_seconds() // 60) if kots and kots[0]["created"] else None,
        "kots": [
            {"id": k["id"], "no": k["no"], "printed": bool(k["kot_status"]), "billed": bool(k["create_bill"]), "ready": bool(k["ready_at"]),
             "time": k["created"].strftime("%H:%M") if k["created"] else "", "lines": by_kot[k["id"]]}
            for k in kots
        ],
        "bill": bill,
    }


def _parse_new_items(company_id: int, raw) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValidationError("The items were not sent correctly. Please reload the page.")
    if len(raw) > billing.MAX_LINES:
        raise ValidationError(f"Send at most {billing.MAX_LINES} lines at once.")
    wanted = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValidationError("One of the lines is invalid. Please reload the page.")
        item_id, qty = to_int(entry.get("item_id")), to_int(entry.get("quantity"))
        note = str(entry.get("note") or "").strip()
        if not item_id or qty is None:
            raise ValidationError("One of the lines is invalid. Please reload the page.")
        if not 1 <= qty <= billing.MAX_QUANTITY:
            raise ValidationError(f"Quantity must be between 1 and {billing.MAX_QUANTITY}.")
        if len(note) > NOTE_MAX:
            raise ValidationError(f"Keep kitchen notes under {NOTE_MAX} characters.")
        wanted.append({"item_id": item_id, "quantity": qty, "note": note})
    priced = menu_item.for_billing(company_id, list({w["item_id"] for w in wanted}))
    missing = [w["item_id"] for w in wanted if w["item_id"] not in priced]
    if missing:
        raise ValidationError("Some items are no longer available (hidden or removed). Reload the page to refresh the menu.")
    for w in wanted:
        w["name"] = priced[w["item_id"]]["name"]
        w["rate"] = priced[w["item_id"]]["rate"]
    return wanted


def _next_kot_no(company_id: int) -> str:
    highest = db.fetch_value(
        "SELECT MAX(CAST(table_order_no AS UNSIGNED)) AS m FROM table_order "
        "WHERE company_detail_id = %s AND active IN (0, 1) AND DATE(created) = %s AND table_order_no REGEXP '^[0-9]+$'",
        (company_id, now().date()),
    )
    return str((highest or 0) + 1)


def add_kot(company_id: int, table_id: int, user_id: int, raw_items, print_kot: bool) -> dict:
    """Saves new items as a KOT. With print_kot, also marks any saved-but-unprinted KOTs as printed.
    Returns the KOT ids to print (empty when only saving)."""
    ensure_schema()
    get_table(company_id, table_id)
    items = _parse_new_items(company_id, raw_items)
    to_print: list[int] = []
    with db.named_lock(f"khatashop_kot_{company_id}"):
        unsent = [k["id"] for k in _open_orders(company_id, table_id) if not k["kot_status"]]
        if not items and not (print_kot and unsent):
            raise ValidationError("Add items first." if not print_kot else "Nothing new to send to the kitchen.")
        moment = now()
        with db.transaction() as cur:
            if items:
                kot_no = _next_kot_no(company_id)
                qty = sum(i["quantity"] for i in items)
                total = sum(Decimal(i["rate"]) * i["quantity"] for i in items)
                cur.execute(
                    "INSERT INTO table_order (company_detail_id, serving_table_id, bill_id, table_order_no, total_quantity, total, "
                    "ordering_date, counter_no, witer_id, kot_status, create_bill, active, created, updated) "
                    "VALUES (%s, %s, 0, %s, %s, %s, %s, %s, %s, %s, 0, 1, %s, %s)",
                    (company_id, table_id, kot_no, str(qty), total, moment.date(), user_id, user_id,
                     1 if print_kot else 0, moment, moment),
                )
                kot_id = cur.lastrowid
                for i in items:
                    cur.execute(
                        "INSERT INTO table_order_detail (table_order_id, item_id, item_name, rate, quantity, created, updated) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (kot_id, i["item_id"], i["name"][:bill_model.ITEM_NAME_MAX], i["rate"], i["quantity"], moment, moment),
                    )
                    if i["note"]:
                        cur.execute("INSERT INTO ks_kot_note (table_order_detail_id, note) VALUES (%s, %s)", (cur.lastrowid, i["note"]))
                if print_kot:
                    to_print.append(kot_id)
            if print_kot and unsent:
                marks = ",".join(["%s"] * len(unsent))
                cur.execute(f"UPDATE table_order SET kot_status = 1, updated = %s WHERE table_order_id IN ({marks})", [moment, *unsent])
                to_print = unsent + to_print
    return {"print_kot_ids": to_print}


def void_line(company_id: int, table_id: int, detail_id: int) -> None:
    """Removes one line from a running order (before the bill is settled)."""
    open_ids = {k["id"]: k for k in _open_orders(company_id, table_id)}
    row = db.fetch_one("SELECT table_order_id FROM table_order_detail WHERE table_order_detail_id = %s", (detail_id,))
    if not row or row["table_order_id"] not in open_ids:
        raise NotFoundError("That line is not on this table's running order.")
    kot = open_ids[row["table_order_id"]]
    with db.transaction() as cur:
        cur.execute("DELETE FROM ks_kot_note WHERE table_order_detail_id = %s", (detail_id,))
        cur.execute("DELETE FROM table_order_detail WHERE table_order_detail_id = %s", (detail_id,))
        cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(quantity), 0) AS q, COALESCE(SUM(rate * quantity), 0) AS t "
                    "FROM table_order_detail WHERE table_order_id = %s", (kot["id"],))
        left = cur.fetchone()
        if left["n"]:
            # create_bill = 0: the printed bill (if any) no longer matches and must be printed again.
            cur.execute("UPDATE table_order SET total_quantity = %s, total = %s, create_bill = 0, updated = %s WHERE table_order_id = %s",
                        (str(left["q"]), left["t"], now(), kot["id"]))
        else:
            cur.execute("DELETE FROM table_order WHERE table_order_id = %s", (kot["id"],))
            cur.execute("UPDATE table_order SET create_bill = 0 WHERE company_detail_id = %s AND serving_table_id = %s AND active = 1 AND kot_status IN (0, 1)",
                        (company_id, table_id))
        _drop_bill_if_empty(cur, company_id, table_id, kot["bill_id"])


def _drop_bill_if_empty(cur, company_id: int, table_id: int, bill_id: int) -> None:
    """An unpaid bill whose order became empty is cancelled."""
    if not bill_id:
        return
    cur.execute("SELECT COUNT(*) AS n FROM table_order WHERE company_detail_id = %s AND serving_table_id = %s AND active = 1 AND kot_status IN (0, 1)",
                (company_id, table_id))
    if cur.fetchone()["n"] == 0:
        cur.execute("SELECT payment_mode_id FROM bill WHERE bill_id = %s", (bill_id,))
        b = cur.fetchone()
        if b and b["payment_mode_id"] == 0:
            part_payment.delete_for_bill(cur, bill_id)
            cur.execute("DELETE FROM bill_detail WHERE bill_id = %s", (bill_id,))
            cur.execute("DELETE FROM bill WHERE bill_id = %s", (bill_id,))
            log.info("Unpaid table bill %s cancelled because its order is empty", bill_id)


def _customer(payload: dict) -> tuple[str, str]:
    name = str(payload.get("customer_name") or "").strip()
    phone = re.sub(r"[\s-]", "", str(payload.get("customer_phone") or ""))
    if len(name) > 100:
        raise ValidationError("Keep the customer name under 100 characters.")
    if phone and not bill_model.PHONE_RE.match(phone):
        raise ValidationError("Enter a valid mobile number (10 digits) or leave it empty.")
    return name, phone


def _payment_mode(company_id: int, mode_id) -> int:
    modes = {m["id"] for m in payment.payment_modes(company_id)}
    mode_id = to_int(mode_id)
    if not modes:
        raise ValidationError("This restaurant has no payment options yet. Set up Cash, Card and UPI first.")
    if mode_id not in modes:
        raise ValidationError("Choose how the customer paid.")
    return mode_id


def print_bill(company_id: int, table_id: int, user_id: int, payload) -> dict:
    """Creates (or updates, if already printed) the bill for everything on the table.
    With payload['paid'], the bill is settled at the same time."""
    if not isinstance(payload, dict):
        raise ValidationError("The bill data was not sent correctly. Please reload the page.")
    get_table(company_id, table_id)
    company = company_model.get(company_id)
    if not company["abbreviation"]:
        raise ValidationError("This restaurant has no bill prefix set, so bill numbers cannot be created.")
    customer_name, phone = _customer(payload)
    paid = bool(payload.get("paid"))
    mode_id = _payment_mode(company_id, payload.get("payment_mode_id")) if paid else 0
    service_percent = Decimal("0")
    service_id = to_int(payload.get("service_mode_id"))
    if service_id:
        services = {s["id"]: s for s in payment.service_modes(company_id)}
        if service_id not in services:
            raise ValidationError("Choose a valid service mode.")
        service_percent = Decimal(str(services[service_id]["percent"]))

    with db.named_lock(f"khatashop_table_{company_id}_{table_id}"):
        state = order_state(company_id, table_id)
        merged: "OrderedDict[tuple, dict]" = OrderedDict()
        for kot in state["kots"]:
            for line in kot["lines"]:
                key = (line["item_id"], line["rate"])
                if key in merged:
                    merged[key]["quantity"] += line["quantity"]
                else:
                    merged[key] = dict(line)
        if not merged:
            raise ValidationError("There is nothing on this table to bill.")
        lines = [billing.BillLine(item_id=l["item_id"], name=l["name"], rate=Decimal(str(l["rate"])), quantity=l["quantity"],
                                  gst_percent=Decimal(str(l["gst"]))) for l in merged.values()]
        totals = billing.calculate(lines, service_percent)
        moment = now()
        session_year, quarter = financial_period(moment.date())
        values = {
            "cgst": str(totals.cgst), "sgst": str(totals.sgst), "total_quantity": str(totals.total_quantity),
            "total": totals.grand_total, "service_cost": totals.service_cost, "billing_date": moment.date(),
            "customer_name": customer_name, "customer_mb_no": phone, "counter_no": user_id,
            "financial_quarter": quarter, "financial_session": session_year,
            "payment_mode_id": mode_id, "serving_table_id": table_id, "order_placement_type": 1,
            "active": 1 if paid else 2, "updated": moment,
        }
        existing = state["bill"]
        with bill_model.bill_no_lock(company_id):
            with db.transaction() as cur:
                if existing:
                    bill_id, bill_no = existing["id"], existing["bill_no"]
                    sets = ", ".join(f"`{k}` = %s" for k in values)
                    cur.execute(f"UPDATE bill SET {sets} WHERE bill_id = %s AND payment_mode_id = 0", [*values.values(), bill_id])
                    cur.execute("DELETE FROM bill_detail WHERE bill_id = %s", (bill_id,))
                else:
                    bill_no = bill_model.next_bill_no(company_id, company["abbreviation"])
                    columns = {"company_detail_id": company_id, "bill_no": bill_no, **values, "created": moment}
                    names = ", ".join(f"`{c}`" for c in columns)
                    cur.execute(f"INSERT INTO bill ({names}) VALUES ({', '.join(['%s'] * len(columns))})", list(columns.values()))
                    bill_id = cur.lastrowid
                cur.executemany(
                    "INSERT INTO bill_detail (bill_id, item_id, item_name, rate, quantity, item_gst, created, updated) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [(bill_id, l.item_id, l.name[:bill_model.ITEM_NAME_MAX], l.rate, l.quantity, l.gst_percent, moment, moment) for l in lines],
                )
                cur.execute(
                    "UPDATE table_order SET bill_id = %s, create_bill = 1, active = %s, updated = %s "
                    "WHERE company_detail_id = %s AND serving_table_id = %s AND active = 1 AND kot_status IN (0, 1)",
                    (bill_id, 0 if paid else 1, moment, company_id, table_id),
                )
    log.info("Table %s bill %s %s by user %s: %s", table_id, bill_no, "printed and settled" if paid else "printed", user_id, totals.grand_total)
    return {"bill_id": bill_id, "bill_no": bill_no, "total": float(totals.grand_total), "settled": paid}


def settle(company_id: int, table_id: int, payment_mode_id, user_id: int = 0) -> dict:
    """Marks the printed bill as paid. With part payments taken, whatever is still due is
    taken in the chosen mode (none needed when the parts already cover the bill)."""
    state = order_state(company_id, table_id)
    bill = state["bill"]
    if not bill:
        raise ValidationError("Print the bill before settling.")
    if state["status"] != "printed":
        raise ValidationError("Items were changed after the bill was printed. Print the bill again before settling.")
    has_parts = bool(bill.get("payments"))
    if has_parts and not to_int(payment_mode_id) and bill.get("due", 0) > 0:
        raise ValidationError(f"₹{bill['due']:,.2f} is still due. Take the rest before settling.")
    mode_id = _payment_mode(company_id, payment_mode_id) if (not has_parts or to_int(payment_mode_id)) else None
    moment = now()
    with db.named_lock(f"khatashop_table_{company_id}_{table_id}"):
        with db.transaction() as cur:
            split_mode = part_payment.settle_split(cur, company_id, bill["id"], mode_id, user_id)
            final_mode = split_mode or mode_id
            if not final_mode:
                raise ValidationError("Choose how the customer paid.")
            cur.execute("UPDATE bill SET payment_mode_id = %s, active = 1, updated = %s WHERE bill_id = %s AND payment_mode_id = 0",
                        (final_mode, moment, bill["id"]))
            if not cur.rowcount:
                raise ValidationError("This bill was already settled on another screen.")
            cur.execute("UPDATE table_order SET active = 0, updated = %s WHERE company_detail_id = %s AND serving_table_id = %s AND active = 1 AND kot_status IN (0, 1)",
                        (moment, company_id, table_id))
    log.info("Table %s bill %s settled%s", table_id, bill["bill_no"], " (part payment)" if split_mode else "")
    return {"bill_id": bill["id"], "bill_no": bill["bill_no"], "total": bill["total"], "part": bool(split_mode)}


def add_part_payment(company_id: int, table_id: int, payload: dict, user_id: int) -> None:
    bill = _printed_bill(company_id, table_id)
    part_payment.add(company_id, bill["id"], payload.get("payment_mode_id"), payload.get("amount"), user_id)


def remove_part_payment(company_id: int, table_id: int, payment_id) -> None:
    bill = _printed_bill(company_id, table_id)
    part_payment.remove(company_id, bill["id"], payment_id)


def _printed_bill(company_id: int, table_id: int) -> dict:
    state = order_state(company_id, table_id)
    if not state["bill"]:
        raise ValidationError("Print the bill first, then take part payments.")
    if state["status"] != "printed":
        raise ValidationError("Items were changed after the bill was printed. Print the bill again, then take the payment.")
    return state["bill"]


def move(company_id: int, from_id: int, to_id: int) -> str:
    source = get_table(company_id, from_id)
    target = get_table(company_id, to_id)
    if from_id == to_id:
        raise ValidationError("Choose a different table.")
    if not _open_orders(company_id, from_id):
        raise ValidationError(f"Table {source['name']} has no running order to move.")
    if _open_orders(company_id, to_id):
        raise ValidationError(f"Table {target['name']} already has a running order. Choose an empty table.")
    with db.transaction() as cur:
        cur.execute("SELECT DISTINCT bill_id FROM table_order WHERE company_detail_id = %s AND serving_table_id = %s AND active = 1 AND kot_status IN (0, 1) AND bill_id <> 0",
                    (company_id, from_id))
        for row in cur.fetchall():
            cur.execute("UPDATE bill SET serving_table_id = %s WHERE bill_id = %s AND payment_mode_id = 0", (to_id, row["bill_id"]))
        cur.execute("UPDATE table_order SET serving_table_id = %s, updated = %s WHERE company_detail_id = %s AND serving_table_id = %s AND active = 1 AND kot_status IN (0, 1)",
                    (to_id, now(), company_id, from_id))
    return target["name"]


def cancel(company_id: int, table_id: int) -> None:
    """Clears a running order that will not be billed (customer left). The unpaid bill, if any, is cancelled."""
    kots = _open_orders(company_id, table_id)
    if not kots:
        raise ValidationError("This table has no running order.")
    ids = [k["id"] for k in kots]
    marks = ",".join(["%s"] * len(ids))
    with db.transaction() as cur:
        cur.execute(f"DELETE N FROM ks_kot_note N JOIN table_order_detail D ON D.table_order_detail_id = N.table_order_detail_id "
                    f"WHERE D.table_order_id IN ({marks})", ids)
        cur.execute(f"DELETE FROM table_order_detail WHERE table_order_id IN ({marks})", ids)
        cur.execute(f"DELETE FROM table_order WHERE table_order_id IN ({marks})", ids)
        for bill_id in {k["bill_id"] for k in kots if k["bill_id"]}:
            _drop_bill_if_empty(cur, company_id, table_id, bill_id)
    log.info("Running order on table %s cancelled (%s KOTs)", table_id, len(ids))


def kitchen_board(company_id: int, ready_window_minutes: int = 30) -> dict:
    """KOTs sent to the kitchen and still open: waiting ones, and ones marked ready recently."""
    ensure_schema()
    moment = now()
    kots = db.fetch_all(
        "SELECT K.table_order_id AS id, K.table_order_no AS no, K.created, T.serving_table_name AS table_name, R.ready_at "
        "FROM table_order K LEFT JOIN serving_table T ON T.serving_table_id = K.serving_table_id "
        "LEFT JOIN ks_kot_ready R ON R.table_order_id = K.table_order_id "
        "WHERE K.company_detail_id = %s AND K.active = 1 AND K.kot_status = 1 "
        "AND (R.ready_at IS NULL OR R.ready_at >= %s) ORDER BY K.table_order_id",
        (company_id, moment - timedelta(minutes=ready_window_minutes)),
    )
    if kots:
        ids = [k["id"] for k in kots]
        marks = ",".join(["%s"] * len(ids))
        lines = db.fetch_all(
            "SELECT D.table_order_id AS kot_id, D.item_name AS name, D.quantity, N.note FROM table_order_detail D "
            "LEFT JOIN ks_kot_note N ON N.table_order_detail_id = D.table_order_detail_id "
            f"WHERE D.table_order_id IN ({marks}) ORDER BY D.table_order_detail_id", ids)
    else:
        lines = []
    out = {"waiting": [], "ready": []}
    for k in kots:
        card = {"id": k["id"], "no": k["no"], "table": k["table_name"] or "—",
                "time": k["created"].strftime("%H:%M") if k["created"] else "",
                "minutes": int((moment - k["created"]).total_seconds() // 60) if k["created"] else 0,
                "lines": [{"name": l["name"], "quantity": l["quantity"], "note": l["note"] or ""} for l in lines if l["kot_id"] == k["id"]]}
        (out["ready"] if k["ready_at"] else out["waiting"]).append(card)
    out["ready"].reverse()
    return out


def set_kot_ready(company_id: int, kot_id: int, ready: bool) -> None:
    ensure_schema()
    row = db.fetch_one("SELECT table_order_id FROM table_order WHERE table_order_id = %s AND company_detail_id = %s AND active = 1 AND kot_status = 1",
                       (kot_id, company_id))
    if not row:
        raise NotFoundError("That KOT is not open any more.")
    if ready:
        db.execute("INSERT INTO ks_kot_ready (table_order_id, ready_at) VALUES (%s, %s) ON DUPLICATE KEY UPDATE ready_at = VALUES(ready_at)", (kot_id, now()))
    else:
        db.execute("DELETE FROM ks_kot_ready WHERE table_order_id = %s", (kot_id,))


def kots_for_print(company_id: int, kot_ids: list[int]) -> list[dict]:
    ensure_schema()
    if not kot_ids:
        return []
    marks = ",".join(["%s"] * len(kot_ids))
    kots = db.fetch_all(
        "SELECT K.table_order_id AS id, K.table_order_no AS no, K.created, T.serving_table_name AS table_name, "
        "(SELECT AR.name FROM aspirant_registration AR WHERE AR.aspirant_registration_id = K.witer_id) AS staff "
        "FROM table_order K LEFT JOIN serving_table T ON T.serving_table_id = K.serving_table_id "
        f"WHERE K.company_detail_id = %s AND K.table_order_id IN ({marks}) ORDER BY K.table_order_id",
        [company_id, *kot_ids],
    )
    if not kots:
        raise NotFoundError("Those KOTs do not exist.")
    lines = db.fetch_all(
        "SELECT D.table_order_id AS kot_id, D.item_name AS name, D.quantity, N.note FROM table_order_detail D "
        "LEFT JOIN ks_kot_note N ON N.table_order_detail_id = D.table_order_detail_id "
        f"WHERE D.table_order_id IN ({marks}) ORDER BY D.table_order_detail_id",
        kot_ids,
    )
    for k in kots:
        k["lines"] = [l for l in lines if l["kot_id"] == k["id"]]
    return kots
