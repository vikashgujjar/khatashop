"""Menu items (table `menu`). Items belong to a company through their category."""
from __future__ import annotations
from decimal import Decimal

from .. import db
from ..errors import NotFoundError, ValidationError
from ..utils import Pagination, to_decimal, to_int
from . import menu_category

BASE_FROM = "FROM menu M JOIN menu_category MC ON MC.menu_category_id = M.menu_category_id WHERE MC.company_detail_id = %s"
FIELDS = (
    "M.item_id AS id, M.item_name AS name, M.menu_category_id AS category_id, MC.menu_category_name AS category_name, "
    "M.rate, M.item_gst AS gst, M.item_description AS description, M.veg, M.active"
)


def _filters(company_id: int, category_id=None, search="", veg="", status=""):
    sql, params = BASE_FROM, [company_id]
    if category_id:
        sql += " AND M.menu_category_id = %s"
        params.append(category_id)
    if search:
        sql += " AND M.item_name LIKE %s"
        params.append(f"%{search}%")
    if veg == "1":
        sql += " AND M.veg = '1'"
    elif veg == "0":
        sql += " AND (M.veg IS NULL OR M.veg <> '1')"
    if status in ("1", "0"):
        sql += " AND M.active = %s"
        params.append(int(status))
    return sql, params


def paginate(company_id: int, page: int, per_page: int, **filters) -> tuple[list[dict], Pagination]:
    where, params = _filters(company_id, **filters)
    total = db.fetch_value("SELECT COUNT(*) AS c " + where, params, 0)
    pager = Pagination(page, per_page, total)
    rows = db.fetch_all(
        f"SELECT {FIELDS} {where} ORDER BY MC.`order`, MC.menu_category_name, M.item_name LIMIT %s OFFSET %s",
        params + [pager.per_page, pager.offset],
    )
    return rows, pager


def get(company_id: int, item_id: int) -> dict:
    row = db.fetch_one(f"SELECT {FIELDS} {BASE_FROM} AND M.item_id = %s", (company_id, item_id))
    if not row:
        raise NotFoundError("That menu item does not exist.")
    return row


def billing_menu(company_id: int) -> list[dict]:
    """Active categories with their active items, for the billing screen."""
    rows = db.fetch_all(
        f"SELECT {FIELDS}, MC.`order` AS cat_order {BASE_FROM} AND MC.active = 1 AND M.active = 1 "
        "ORDER BY MC.`order`, MC.menu_category_name, M.item_name",
        (company_id,),
    )
    categories: dict[int, dict] = {}
    for row in rows:
        cat = categories.setdefault(row["category_id"], {"id": row["category_id"], "name": row["category_name"], "items": []})
        cat["items"].append(
            {
                "id": row["id"],
                "name": row["name"],
                "rate": float(row["rate"]),
                "gst": float(row["gst"]),
                "veg": str(row["veg"]) == "1",
                "description": row["description"] or "",
            }
        )
    return list(categories.values())


def for_billing(company_id: int, item_ids: list[int]) -> dict[int, dict]:
    """Current price and GST for the given items. Only active items can be billed."""
    if not item_ids:
        return {}
    marks = ",".join(["%s"] * len(item_ids))
    rows = db.fetch_all(
        f"SELECT M.item_id AS id, M.item_name AS name, M.rate, M.item_gst AS gst {BASE_FROM} "
        f"AND M.active = 1 AND MC.active = 1 AND M.item_id IN ({marks})",
        [company_id, *item_ids],
    )
    return {row["id"]: row for row in rows}


def validate(company_id: int, form) -> dict:
    errors = {}
    name = (form.get("name") or "").strip()
    if not name:
        errors["name"] = "Enter the item name."
    elif len(name) > 150:
        errors["name"] = "Keep the name under 150 characters."

    category_id = to_int(form.get("category_id"))
    if not category_id:
        errors["category_id"] = "Choose a category."
    else:
        try:
            menu_category.get(company_id, category_id)
        except NotFoundError:
            errors["category_id"] = "Choose a valid category."

    rate = to_decimal(form.get("rate"))
    if rate is None:
        errors["rate"] = "Enter the price."
    elif rate < 0 or rate > Decimal("999999.99"):
        errors["rate"] = "Price must be between 0 and 9,99,999.99."
    elif rate != rate.quantize(Decimal("0.01")):
        errors["rate"] = "Use at most 2 decimal places."

    gst = to_decimal(form.get("gst"), Decimal("0"))
    if gst is None or gst < 0 or gst > 28:
        errors["gst"] = "GST must be between 0 and 28%."

    description = (form.get("description") or "").strip()
    if len(description) > 2000:
        errors["description"] = "Keep the description under 2000 characters."

    if errors:
        raise ValidationError("Please fix the highlighted fields.", errors=errors)
    return {
        "name": name,
        "category_id": category_id,
        "rate": rate,
        "gst": gst,
        "description": description or None,
        "veg": "1" if str(form.get("veg", "1")) == "1" else "0",
        "active": 1 if str(form.get("active", "1")) == "1" else 0,
    }


def create(company_id: int, data: dict) -> int:
    _, new_id = db.execute(
        "INSERT INTO menu (item_name, menu_category_id, rate, item_gst, item_description, veg, active) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (data["name"], data["category_id"], data["rate"], data["gst"], data["description"], data["veg"], data["active"]),
    )
    return new_id


def update(company_id: int, item_id: int, data: dict) -> None:
    get(company_id, item_id)  # ensures the item belongs to this company
    db.execute(
        "UPDATE menu SET item_name = %s, menu_category_id = %s, rate = %s, item_gst = %s, "
        "item_description = %s, veg = %s, active = %s WHERE item_id = %s",
        (data["name"], data["category_id"], data["rate"], data["gst"], data["description"], data["veg"], data["active"], item_id),
    )


def toggle(company_id: int, item_id: int) -> int:
    item = get(company_id, item_id)
    new_state = 0 if int(item["active"]) == 1 else 1
    db.execute("UPDATE menu SET active = %s WHERE item_id = %s", (new_state, item_id))
    return new_state


def delete(company_id: int, item_id: int) -> None:
    item = get(company_id, item_id)
    billed = db.fetch_value("SELECT COUNT(*) AS c FROM bill_detail WHERE item_id = %s", (item_id,), 0)
    if billed:
        raise ValidationError(
            f"'{item['name']}' appears on {billed} bill(s) and cannot be deleted. Hide it instead so it no longer shows for billing."
        )
    db.execute("DELETE FROM menu WHERE item_id = %s", (item_id,))


# ------------------------------------------------------------------ import --

def export_all(company_id: int) -> list[dict]:
    return db.fetch_all(f"SELECT {FIELDS} {BASE_FROM} ORDER BY MC.`order`, MC.menu_category_name, M.item_name", (company_id,))


def _check_row(row: dict, categories: dict, by_id: dict, by_name: dict, create_categories: bool, parse_status, parse_veg):
    """Validates one spreadsheet row. Returns (plan entry or None, list of problems)."""
    problems = []
    name = (row.get("name") or "").strip()
    cat_name = (row.get("category") or "").strip()
    if not name:
        problems.append("Item Name is empty")
    elif len(name) > 150:
        problems.append("Item Name is longer than 150 characters")
    if not cat_name:
        problems.append("Category is empty")
    elif len(cat_name) > 150:
        problems.append("Category is longer than 150 characters")
    elif cat_name.lower() not in categories and not create_categories:
        problems.append(f"Category '{cat_name}' does not exist (tick 'Create missing categories' to add it)")

    rate = to_decimal(row.get("rate"))
    if rate is None:
        problems.append(f"Price must be a number (got '{row.get('rate', '')}')")
    elif rate < 0 or rate > Decimal("999999.99") or rate != rate.quantize(Decimal("0.01")):
        problems.append("Price must be between 0 and 999999.99 with at most 2 decimals")

    gst = to_decimal(str(row.get("gst") or "").replace("%", ""))
    if gst is None:
        problems.append(f"GST % must be a number (got '{row.get('gst', '')}')")
    elif gst < 0 or gst > 28:
        problems.append("GST % must be between 0 and 28")

    target = None
    id_text = (row.get("item_id") or "").strip()
    if id_text:
        target = by_id.get(to_int(id_text))
        if target is None:
            problems.append(f"Item ID {id_text} does not belong to this restaurant (leave it empty for new items)")
    elif name and cat_name.lower() in categories:
        matches = by_name.get((categories[cat_name.lower()], name.lower()), [])
        if len(matches) > 1:
            problems.append(f"{len(matches)} items called '{name}' exist in this category; add the Item ID column to say which one")
        elif matches:
            target = matches[0]

    veg = active = None
    try:
        veg = parse_veg(row.get("veg"), "1" if not target or str(target["veg"]) == "1" else "0")
    except ValueError as exc:
        problems.append(f"{exc} (got '{row.get('veg')}')")
    try:
        active = parse_status(row.get("status"), int(target["active"]) if target else 1)
    except ValueError as exc:
        problems.append(f"{exc} (got '{row.get('status')}')")

    description = (row.get("description") or "").strip()
    if len(description) > 2000:
        problems.append("Description is longer than 2000 characters")
    if not description and target:
        description = target["description"] or ""  # an empty cell keeps the current description

    if problems:
        return None, problems
    return {"target": target, "name": name, "cat_name": cat_name, "rate": rate, "gst": gst, "veg": veg,
            "active": active, "description": description or None}, []


def import_rows(company_id: int, rows: list[dict], create_categories: bool = False) -> dict:
    """Adds or updates menu items from spreadsheet rows. All-or-nothing."""
    from ..services.menu_io import parse_status, parse_veg

    categories = {c["name"].strip().lower(): c["id"] for c in menu_category.options(company_id)}
    items = export_all(company_id)
    by_id = {i["id"]: i for i in items}
    by_name: dict[tuple, list] = {}
    for i in items:
        by_name.setdefault((i["category_id"], i["name"].strip().lower()), []).append(i)

    errors, plan, seen = [], [], {}
    for row in rows:
        entry, problems = _check_row(row, categories, by_id, by_name, create_categories, parse_status, parse_veg)
        if entry:
            t = entry["target"]
            key = ("id", t["id"]) if t else ("new", entry["cat_name"].lower(), entry["name"].lower())
            if key in seen:
                problems = [f"the same item is also on row {seen[key]}"]
            seen[key] = row["_row"]
        if problems:
            errors.append(f"Row {row['_row']}: " + "; ".join(problems) + ".")
        else:
            plan.append(entry)

    if errors:
        raise ValidationError(f"{len(errors)} problem(s) found. Nothing was imported.", errors={"rows": errors})

    new_categories = {p["cat_name"].lower(): p["cat_name"] for p in plan if p["cat_name"].lower() not in categories}
    added = updated = unchanged = 0
    with db.transaction() as cur:
        for key, cat_name in new_categories.items():
            cur.execute(
                "INSERT INTO menu_category (company_detail_id, menu_category_name, menu_revision_id, active, `order`) "
                "VALUES (%s, %s, %s, 1, 0)",
                (company_id, cat_name, menu_category.DEFAULT_REVISION_ID),
            )
            categories[key] = cur.lastrowid
        for p in plan:
            values = (p["name"], categories[p["cat_name"].lower()], p["rate"], p["gst"], p["description"], p["veg"], p["active"])
            t = p["target"]
            if t is None:
                cur.execute(
                    "INSERT INTO menu (item_name, menu_category_id, rate, item_gst, item_description, veg, active) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    values,
                )
                added += 1
            elif (t["name"], t["category_id"], Decimal(t["rate"]), Decimal(t["gst"]), t["description"],
                  "1" if str(t["veg"]) == "1" else "0", int(t["active"])) == values:
                unchanged += 1
            else:
                cur.execute(
                    "UPDATE menu SET item_name = %s, menu_category_id = %s, rate = %s, item_gst = %s, item_description = %s, "
                    "veg = %s, active = %s WHERE item_id = %s",
                    values + (t["id"],),
                )
                updated += 1
    return {"added": added, "updated": updated, "unchanged": unchanged, "categories_added": len(new_categories)}
