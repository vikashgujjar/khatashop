"""Inventory notes (procurement / issuing / consumption) and consumption formulas (recipes).

Uses the PHP tables: procurement_note(+_detail), issuing_note(+_detail),
consumption_note(+_detail) and consumption_formula.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .. import db
from ..errors import NotFoundError, ValidationError
from ..utils import Pagination, financial_period, now, parse_date, round2, to_decimal, to_int
from . import company as company_model

NAME_MAX = 40  # *_detail.ingredient_name is varchar(40)


@dataclass
class NoteType:
    key: str
    title: str
    table: str
    detail: str
    pk: str
    ref: str
    date: str
    status_col: str
    qty_decimals: int
    priced: bool = False   # procurement: rate + GST per line, vendor, invoice, payment status


NOTES = {
    "procurement": NoteType("procurement", "Procurement", "procurement_note", "procurement_note_detail", "procurement_note_id",
                            "procurement_ref_no", "procurement_date", "procurement_note_status", 0, priced=True),
    "issuing": NoteType("issuing", "Issuing", "issuing_note", "issuing_note_detail", "issuing_note_id",
                        "issuing_ref_no", "issuing_date", "issuing_note_status", 2),
    "consumption": NoteType("consumption", "Consumption", "consumption_note", "consumption_note_detail", "consumption_note_id",
                            "consumption_ref_no", "consumption_date", "consumption_note_status", 3),
}
PAYMENT = {0: "Unpaid", 1: "Paid"}


def note_type(key: str) -> NoteType:
    if key not in NOTES:
        raise NotFoundError("That page does not exist.")
    return NOTES[key]


def ingredients(company_id: int, active_only: bool = True) -> list[dict]:
    sql = ("SELECT I.ingredient_id AS id, I.ingredient_name AS name, I.ingredient_unit AS unit, I.ingredient_gst AS gst, "
           "I.ingredient_description AS description, IC.ingredient_category_name AS category FROM ingredient I "
           "JOIN ingredient_category IC ON IC.ingredient_category_id = I.ingredient_category_id WHERE IC.company_detail_id = %s")
    if active_only:
        sql += " AND I.active = 1"
    rows = db.fetch_all(sql + " ORDER BY I.ingredient_name", (company_id,))
    for r in rows:
        r["gst"] = float(r["gst"] or 0)
    return rows


def vendors(company_id: int) -> list[dict]:
    return db.fetch_all("SELECT vendor_id AS id, vendor_name AS name FROM vendor WHERE company_detail_id = %s ORDER BY vendor_name", (company_id,))


# ------------------------------------------------------------------ listing --

def paginate(nt: NoteType, company_id: int, filters: dict, page: int, per_page: int):
    where = f" FROM {nt.table} N WHERE N.company_detail_id = %s AND N.active = 1"
    params: list = [company_id]
    q = (filters.get("q") or "").strip()
    if q:
        where += (f" AND (N.{nt.ref} LIKE %s OR N.{nt.pk} IN (SELECT {nt.pk} FROM {nt.detail} WHERE ingredient_name LIKE %s)"
                  + (" OR N.invoice_no LIKE %s OR N.vendor_id IN (SELECT vendor_id FROM vendor WHERE vendor_name LIKE %s)" if nt.priced else "") + ")")
        params += [f"%{q}%", f"%{q}%"] + ([f"%{q}%", f"%{q}%"] if nt.priced else [])
    df, dt = parse_date(filters.get("date_from")), parse_date(filters.get("date_to"))
    if df:
        where += f" AND N.{nt.date} >= %s"
        params.append(df)
    if dt:
        where += f" AND N.{nt.date} <= %s"
        params.append(dt)
    total = db.fetch_value("SELECT COUNT(*) AS c" + where, params, 0)
    pager = Pagination(page, per_page, total)
    extra = (", N.invoice_no, N.total, N.cgst, N.sgst, N.igst, N.payment_status, "
             "(SELECT vendor_name FROM vendor V WHERE V.vendor_id = N.vendor_id) AS vendor") if nt.priced else ""
    rows = db.fetch_all(
        f"SELECT N.{nt.pk} AS id, N.{nt.ref} AS ref, N.{nt.date} AS note_date, N.total_quantity, N.counter_no, "
        f"(SELECT name FROM aspirant_registration A WHERE A.aspirant_registration_id = N.counter_no) AS counter{extra}"
        + where + f" ORDER BY N.{nt.pk} DESC LIMIT %s OFFSET %s",
        params + [pager.per_page, pager.offset],
    )
    if rows:
        ids = [r["id"] for r in rows]
        marks = ",".join(["%s"] * len(ids))
        lines = db.fetch_all(f"SELECT {nt.pk} AS nid, ingredient_name, quantity, ingredient_unit FROM {nt.detail} "
                             f"WHERE {nt.pk} IN ({marks}) ORDER BY {nt.detail}_id", ids)
        grouped = defaultdict(list)
        for l in lines:
            grouped[l["nid"]].append(l)
        for r in rows:
            r["lines"] = grouped.get(r["id"], [])
    return rows, pager


def get(nt: NoteType, company_id: int, note_id: int) -> dict:
    extra = ", N.invoice_no, N.vendor_id, N.total, N.cgst, N.sgst, N.igst, N.payment_status, (SELECT vendor_name FROM vendor V WHERE V.vendor_id = N.vendor_id) AS vendor" if nt.priced else ""
    note = db.fetch_one(
        f"SELECT N.{nt.pk} AS id, N.{nt.ref} AS ref, N.{nt.date} AS note_date, N.total_quantity, N.created, "
        f"(SELECT name FROM aspirant_registration A WHERE A.aspirant_registration_id = N.counter_no) AS counter{extra} "
        f"FROM {nt.table} N WHERE N.{nt.pk} = %s AND N.company_detail_id = %s AND N.active = 1",
        (note_id, company_id),
    )
    if not note:
        raise NotFoundError(f"That {nt.title.lower()} note does not exist.")
    price_cols = ", rate, ingredient_gst, total" if nt.priced else ""
    note["lines"] = db.fetch_all(
        f"SELECT ingredient_id, ingredient_name, quantity, ingredient_unit, ingredient_description{price_cols} "
        f"FROM {nt.detail} WHERE {nt.pk} = %s ORDER BY {nt.detail}_id", (note_id,))
    return note


# ------------------------------------------------------------------- saving --

def _parse(nt: NoteType, company_id: int, form) -> dict:
    errors = {}
    note_date = parse_date(form.get("note_date"))
    if not note_date:
        errors["note_date"] = "Choose the date."
    data = {"note_date": note_date}
    if nt.priced:
        vendor_id = to_int(form.get("vendor_id"))
        if vendor_id not in {v["id"] for v in vendors(company_id)}:
            errors["vendor_id"] = "Choose the vendor."
        invoice = (form.get("invoice_no") or "").strip()
        if len(invoice) > 30:
            errors["invoice_no"] = "Keep the invoice number under 30 characters."
        data.update(vendor_id=vendor_id, invoice_no=invoice, payment_status=1 if form.get("payment_status") == "1" else 0)

    known = {i["id"]: i for i in ingredients(company_id, active_only=False)}
    ids, qtys = form.getlist("ingredient_id"), form.getlist("quantity")
    rates, gsts = form.getlist("rate"), form.getlist("gst")
    lines, line_errors = [], []
    step = Decimal(1).scaleb(-nt.qty_decimals)
    for i, raw_id in enumerate(ids):
        if not raw_id and not (qtys[i] if i < len(qtys) else ""):
            continue  # empty row
        n = i + 1
        ing = known.get(to_int(raw_id))
        qty = to_decimal(qtys[i] if i < len(qtys) else None)
        if not ing:
            line_errors.append(f"Line {n}: choose an ingredient.")
            continue
        if qty is None or qty <= 0 or qty > 999999:
            line_errors.append(f"Line {n} ({ing['name']}): enter a quantity above 0.")
            continue
        if qty != qty.quantize(step):
            line_errors.append(f"Line {n} ({ing['name']}): use {'whole numbers' if nt.qty_decimals == 0 else f'at most {nt.qty_decimals} decimals'}.")
            continue
        line = {"ingredient_id": ing["id"], "ingredient_name": ing["name"][:NAME_MAX], "quantity": qty,
                "ingredient_unit": ing["unit"], "ingredient_description": (ing["description"] or "")[:500]}
        if nt.priced:
            rate = to_decimal(rates[i] if i < len(rates) else None)
            gst = to_decimal(gsts[i] if i < len(gsts) else None, Decimal(str(ing["gst"])))
            if rate is None or rate < 0 or rate > 999999:
                line_errors.append(f"Line {n} ({ing['name']}): enter the rate.")
                continue
            if gst is None or gst < 0 or gst > 28:
                line_errors.append(f"Line {n} ({ing['name']}): GST must be 0 to 28%.")
                continue
            line.update(rate=round2(rate), ingredient_gst=gst, total=round2(rate * qty))
        lines.append(line)
    if not lines and not line_errors:
        line_errors.append("Add at least one ingredient line.")
    if line_errors:
        errors["lines"] = line_errors
    if errors:
        first = next(iter(errors.values()))
        raise ValidationError(first[0] if isinstance(first, list) else first, errors=errors)
    data["lines"] = lines
    return data


def _next_ref(nt: NoteType, company_id: int) -> str:
    abbr = company_model.get(company_id)["abbreviation"] or "N"
    n = len(abbr)
    highest = db.fetch_value(
        f"SELECT MAX(CAST(SUBSTRING({nt.ref}, %s) AS UNSIGNED)) AS m FROM {nt.table} "
        f"WHERE company_detail_id = %s AND LEFT({nt.ref}, %s) = BINARY %s AND SUBSTRING({nt.ref}, %s) REGEXP '^[0-9]+$'",
        (n + 1, company_id, n, abbr, n + 1))
    return f"{abbr}{(highest or 0) + 1:02d}"


def save(nt: NoteType, company_id: int, user_id: int, form, note_id: int | None = None) -> dict:
    data = _parse(nt, company_id, form)
    moment = now()
    session_year, quarter = financial_period(data["note_date"])
    qty_total = sum(l["quantity"] for l in data["lines"])
    header = {nt.date: data["note_date"], "total_quantity": str(qty_total) if nt.key != "issuing" else qty_total,
              "financial_quarter": quarter, "financial_session": session_year, "updated": moment}
    if nt.priced:
        base = sum(l["total"] for l in data["lines"])
        gst = sum(round2(l["total"] * l["ingredient_gst"] / 100) for l in data["lines"])
        half = round2(gst / 2)
        header.update(vendor_id=data["vendor_id"], invoice_no=data["invoice_no"], payment_status=data["payment_status"],
                      cgst=half, sgst=half, igst=0, total=round2(base + half + half))
    with db.named_lock(f"khatashop_{nt.key}_ref_{company_id}"):
        with db.transaction() as cur:
            if note_id:
                get(nt, company_id, note_id)
                sets = ", ".join(f"`{k}` = %s" for k in header)
                cur.execute(f"UPDATE {nt.table} SET {sets} WHERE {nt.pk} = %s", [*header.values(), note_id])
                cur.execute(f"DELETE FROM {nt.detail} WHERE {nt.pk} = %s", (note_id,))
                ref = get(nt, company_id, note_id)["ref"]
            else:
                ref = _next_ref(nt, company_id)
                cols = {nt.ref: ref, "company_detail_id": company_id, "active": 1, "counter_no": user_id, nt.status_col: 1,
                        "created": moment, **header}
                cur.execute(f"INSERT INTO {nt.table} ({', '.join(f'`{c}`' for c in cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                            list(cols.values()))
                note_id = cur.lastrowid
            for l in data["lines"]:
                row = {nt.pk: note_id, **l, "created": moment, "updated": moment}
                cur.execute(f"INSERT INTO {nt.detail} ({', '.join(f'`{c}`' for c in row)}) VALUES ({', '.join(['%s'] * len(row))})",
                            list(row.values()))
    return {"id": note_id, "ref": ref}


def delete(nt: NoteType, company_id: int, note_id: int) -> None:
    get(nt, company_id, note_id)
    with db.transaction() as cur:
        cur.execute(f"DELETE FROM {nt.detail} WHERE {nt.pk} = %s", (note_id,))
        cur.execute(f"DELETE FROM {nt.table} WHERE {nt.pk} = %s", (note_id,))


# ---------------------------------------------------- consumption formulas --

def formula_items(company_id: int, q: str = "") -> list[dict]:
    sql = ("SELECT M.item_id AS id, M.item_name AS name, MC.menu_category_name AS category, "
           "(SELECT COUNT(*) FROM consumption_formula F WHERE F.item_id = M.item_id) AS line_count, "
           "(SELECT GROUP_CONCAT(CONCAT(F.ingredient_name, ' ', 0 + F.quantity, ' ', COALESCE(F.ingredient_unit, '')) SEPARATOR ', ') "
           " FROM consumption_formula F WHERE F.item_id = M.item_id) AS recipe "
           "FROM menu M JOIN menu_category MC ON MC.menu_category_id = M.menu_category_id WHERE MC.company_detail_id = %s")
    params: list = [company_id]
    if q:
        sql += " AND M.item_name LIKE %s"
        params.append(f"%{q}%")
    return db.fetch_all(sql + " ORDER BY (line_count = 0), MC.menu_category_name, M.item_name", params)


def formula(company_id: int, item_id: int) -> dict:
    item = db.fetch_one("SELECT M.item_id AS id, M.item_name AS name FROM menu M JOIN menu_category MC ON MC.menu_category_id = M.menu_category_id "
                        "WHERE M.item_id = %s AND MC.company_detail_id = %s", (item_id, company_id))
    if not item:
        raise NotFoundError("That menu item does not exist.")
    item["lines"] = db.fetch_all("SELECT ingredient_id, ingredient_name, quantity, ingredient_unit FROM consumption_formula "
                                 "WHERE item_id = %s ORDER BY consumption_formula_id", (item_id,))
    return item


def save_formula(company_id: int, item_id: int, form) -> int:
    formula(company_id, item_id)
    nt = NoteType("formula", "Formula", "", "", "", "", "", "", 3)
    form_dict = form.to_dict()
    form_dict["note_date"] = "2000-01-01"

    class _F(dict):
        def getlist(self, key):
            return form.getlist(key)
    lines = _parse(nt, company_id, _F(form_dict))["lines"] if any(form.getlist("ingredient_id")) else []
    moment = now()
    with db.transaction() as cur:
        cur.execute("DELETE FROM consumption_formula WHERE item_id = %s", (item_id,))
        for l in lines:
            cur.execute("INSERT INTO consumption_formula (item_id, ingredient_id, ingredient_name, quantity, ingredient_unit, created, updated) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (item_id, l["ingredient_id"], l["ingredient_name"], l["quantity"], l["ingredient_unit"], moment, moment))
    return len(lines)
