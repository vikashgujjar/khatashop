"""Part payment: one bill paid with several payment modes (e.g. ₹300 UPI + ₹200 Cash).

The PHP schema keeps a single `bill.payment_mode_id`, so the split lives in our own table:

  ks_bill_payment   one row per amount received (bill, payment mode, amount, who, when)

Rules
  * A bill with no rows here is paid by `bill.payment_mode_id` in full (every old bill).
  * Amounts can be taken while the bill is still unpaid (payment_mode_id = 0); the table
    stays "Bill printed" until the amounts add up to the bill total and it is settled.
  * When settled, `bill.payment_mode_id` is set to the mode that took the largest share,
    so the old app and old reports still see a paid bill. Our reports split it by mode.
"""
from __future__ import annotations
import logging
import threading
from decimal import Decimal

from .. import db
from ..errors import NotFoundError, ValidationError
from ..utils import now, to_int
from . import payment

log = logging.getLogger("khatashop.models.part_payment")

MAX_PARTS = 10
_schema_ready = False
_schema_lock = threading.Lock()


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        db.create_table(
            "CREATE TABLE IF NOT EXISTS ks_bill_payment ("
            " id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,"
            " bill_id INT NOT NULL,"
            " company_detail_id INT NOT NULL,"
            " payment_mode_id INT NOT NULL,"
            " amount DECIMAL(10,2) NOT NULL,"
            " created_by INT NOT NULL DEFAULT 0,"
            " created DATETIME NOT NULL,"
            " KEY idx_bill (bill_id)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
        _schema_ready = True


def _money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def parse_amount(raw) -> Decimal:
    try:
        amount = Decimal(str(raw).strip())
    except Exception:
        raise ValidationError("Enter the amount received, e.g. 250.")
    if not amount.is_finite() or amount <= 0:
        raise ValidationError("The amount must be more than zero.")
    if amount != amount.quantize(Decimal("0.01")):
        raise ValidationError("Use at most 2 decimal places (paise).")
    return amount


def for_bill(bill_id: int) -> list[dict]:
    ensure_schema()
    rows = db.fetch_all(
        "SELECT P.id, P.payment_mode_id AS mode_id, P.amount, P.created, "
        "COALESCE(PM.payment_mode, 'Other') AS mode "
        "FROM ks_bill_payment P LEFT JOIN bill_payment_mode PM ON PM.payment_mode_id = P.payment_mode_id "
        "WHERE P.bill_id = %s ORDER BY P.id",
        (bill_id,),
    )
    return [{"id": r["id"], "mode_id": r["mode_id"], "mode": r["mode"], "amount": float(r["amount"]),
             "time": r["created"].strftime("%d %b, %H:%M") if r["created"] else ""} for r in rows]


def for_bills(bill_ids: list[int]) -> dict[int, list[dict]]:
    """Split payments of many bills at once (bill list, export)."""
    ensure_schema()
    if not bill_ids:
        return {}
    marks = ",".join(["%s"] * len(bill_ids))
    out: dict[int, list[dict]] = {}
    for r in db.fetch_all(
        "SELECT P.bill_id, P.amount, COALESCE(PM.payment_mode, 'Other') AS mode "
        "FROM ks_bill_payment P LEFT JOIN bill_payment_mode PM ON PM.payment_mode_id = P.payment_mode_id "
        f"WHERE P.bill_id IN ({marks}) ORDER BY P.id",
        list(bill_ids),
    ):
        out.setdefault(r["bill_id"], []).append({"mode": r["mode"], "amount": float(r["amount"])})
    return out


def label(parts: list[dict]) -> str:
    """'Part: UPI ₹300.00 + Cash ₹200.00' (amounts per mode added up)."""
    totals: dict[str, Decimal] = {}
    for p in parts:
        totals[p["mode"]] = totals.get(p["mode"], Decimal("0")) + _money(p["amount"])
    return "Part: " + " + ".join(f"{mode} ₹{amount:,.2f}" for mode, amount in totals.items())


def apply_labels(bills: list[dict]) -> None:
    """Replaces payment_mode with the split for part-paid bills (rows need 'id' and 'payment_mode')."""
    splits = for_bills([b["id"] for b in bills])
    for b in bills:
        parts = splits.get(b["id"])
        b["payments"] = parts or []
        if parts:
            b["payment_mode"] = label(parts)


def summary(bill_id: int, bill_total) -> dict:
    parts = for_bill(bill_id)
    paid = sum((_money(p["amount"]) for p in parts), Decimal("0"))
    total = _money(bill_total)
    return {"payments": parts, "paid": float(paid), "due": float(total - paid)}


def _unpaid_bill(cur, company_id: int, bill_id: int) -> dict:
    cur.execute("SELECT bill_id, total, payment_mode_id FROM bill WHERE bill_id = %s AND company_detail_id = %s FOR UPDATE",
                (bill_id, company_id))
    bill = cur.fetchone()
    if not bill:
        raise NotFoundError("That bill does not exist.")
    if bill["payment_mode_id"]:
        raise ValidationError("This bill is already settled.")
    return bill


def add(company_id: int, bill_id: int, mode_id, raw_amount, user_id: int) -> None:
    ensure_schema()
    modes = {m["id"] for m in payment.payment_modes(company_id)}
    mode_id = to_int(mode_id)
    if mode_id not in modes:
        raise ValidationError("Choose how this amount was paid (Cash, Card, UPI…).")
    amount = parse_amount(raw_amount)
    with db.transaction() as cur:
        bill = _unpaid_bill(cur, company_id, bill_id)
        cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS paid FROM ks_bill_payment WHERE bill_id = %s", (bill_id,))
        row = cur.fetchone()
        due = _money(bill["total"]) - _money(row["paid"])
        if due <= 0:
            raise ValidationError("Nothing is due on this bill. Settle it now.")
        if amount > due:
            raise ValidationError(f"Only ₹{due:,.2f} is due. Enter ₹{due:,.2f} or less.")
        if row["n"] >= MAX_PARTS:
            raise ValidationError(f"A bill can be split into at most {MAX_PARTS} payments.")
        cur.execute(
            "INSERT INTO ks_bill_payment (bill_id, company_detail_id, payment_mode_id, amount, created_by, created) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (bill_id, company_id, mode_id, amount, user_id, now()),
        )
    log.info("Bill %s: part payment %s (mode %s) by user %s", bill_id, amount, mode_id, user_id)


def remove(company_id: int, bill_id: int, payment_id) -> None:
    ensure_schema()
    payment_id = to_int(payment_id)
    with db.transaction() as cur:
        _unpaid_bill(cur, company_id, bill_id)
        cur.execute("DELETE FROM ks_bill_payment WHERE id = %s AND bill_id = %s", (payment_id, bill_id))
        if not cur.rowcount:
            raise NotFoundError("That payment was already removed.")


def settle_split(cur, company_id: int, bill_id: int, extra_mode_id: int | None, user_id: int) -> int | None:
    """Inside a settle transaction. Returns the mode id to store on the bill, or None when the bill
    has no split payments (normal single-mode settle).

    With split payments, whatever is still due is taken in `extra_mode_id` (the mode chosen on Settle)."""
    ensure_schema()
    bill = _unpaid_bill(cur, company_id, bill_id)
    cur.execute("SELECT payment_mode_id, amount FROM ks_bill_payment WHERE bill_id = %s", (bill_id,))
    parts = cur.fetchall()
    if not parts:
        return None
    paid = sum((_money(p["amount"]) for p in parts), Decimal("0"))
    due = _money(bill["total"]) - paid
    if due < 0:
        raise ValidationError(f"₹{paid:,.2f} was taken but the bill is only ₹{_money(bill['total']):,.2f}. "
                              "Remove a payment and enter the right amount.")
    if due > 0:
        if not extra_mode_id:
            raise ValidationError(f"₹{due:,.2f} is still due. Take the rest before settling.")
        cur.execute(
            "INSERT INTO ks_bill_payment (bill_id, company_detail_id, payment_mode_id, amount, created_by, created) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (bill_id, company_id, extra_mode_id, due, user_id, now()),
        )
        parts = [*parts, {"payment_mode_id": extra_mode_id, "amount": due}]
    by_mode: dict[int, Decimal] = {}
    for p in parts:
        by_mode[p["payment_mode_id"]] = by_mode.get(p["payment_mode_id"], Decimal("0")) + _money(p["amount"])
    return max(by_mode, key=by_mode.get)


def save_for_new_bill(cur, company_id: int, bill_id: int, parts: list[tuple[int, Decimal]], user_id: int) -> None:
    """Quick bill (Generate Bill): the split is known up front and already validated."""
    ensure_schema()
    moment = now()
    cur.executemany(
        "INSERT INTO ks_bill_payment (bill_id, company_detail_id, payment_mode_id, amount, created_by, created) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        [(bill_id, company_id, mode_id, amount, user_id, moment) for mode_id, amount in parts],
    )


def delete_for_bill(cur, bill_id: int) -> None:
    ensure_schema()
    cur.execute("DELETE FROM ks_bill_payment WHERE bill_id = %s", (bill_id,))


# ---------------------------------------------------------------- reports ---

def mode_lines(bill_where: str) -> str:
    """A derived table of (bill_id, mode_id, amount, gst): one row per bill, or one row per split
    payment for part-paid bills. `bill_where` is a condition on alias B and its params must be
    passed twice. GST is shared out in proportion to each amount."""
    ensure_schema()
    gst = "(CAST(B.cgst AS DECIMAL(10,2)) + CAST(B.sgst AS DECIMAL(10,2)))"
    return (
        f"(SELECT B.bill_id, B.payment_mode_id AS mode_id, B.total AS amount, {gst} AS gst "
        f"FROM bill B WHERE {bill_where} AND NOT EXISTS (SELECT 1 FROM ks_bill_payment P WHERE P.bill_id = B.bill_id) "
        f"UNION ALL SELECT B.bill_id, P.payment_mode_id, P.amount, IF(B.total > 0, {gst} * P.amount / B.total, 0) "
        f"FROM bill B JOIN ks_bill_payment P ON P.bill_id = B.bill_id WHERE {bill_where})"
    )


def mode_columns_join(mode_ids: list[int]) -> tuple[str, dict[int, str]]:
    """For per-mode columns grouped by bill attributes: a LEFT JOIN (alias S) with each part-paid
    bill's amount per mode, and for each mode the SQL expression to SUM."""
    ensure_schema()
    pivots = "".join(f"SUM(CASE WHEN payment_mode_id = {int(m)} THEN amount ELSE 0 END) AS m_{int(m)}, " for m in mode_ids)
    join = f"LEFT JOIN (SELECT bill_id, {pivots}1 AS split FROM ks_bill_payment GROUP BY bill_id) S ON S.bill_id = B.bill_id"
    exprs = {int(m): f"IF(S.split IS NULL, IF(B.payment_mode_id = {int(m)}, B.total, 0), S.m_{int(m)})" for m in mode_ids}
    return join, exprs
