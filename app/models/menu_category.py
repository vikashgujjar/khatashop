"""Menu categories (table `menu_category`)."""
from .. import db
from ..errors import NotFoundError, ValidationError
from ..utils import to_int

# The PHP app always stores revision 1; kept the same for compatibility.
DEFAULT_REVISION_ID = 1


def list_for_company(company_id: int, search: str = "", status: str = "") -> list[dict]:
    sql = (
        "SELECT MC.menu_category_id AS id, MC.menu_category_name AS name, MC.active, MC.`order` AS sort_order, "
        "  (SELECT COUNT(*) FROM menu M WHERE M.menu_category_id = MC.menu_category_id) AS item_count, "
        "  (SELECT COUNT(*) FROM menu M WHERE M.menu_category_id = MC.menu_category_id AND M.active = 1) AS active_items "
        "FROM menu_category MC WHERE MC.company_detail_id = %s"
    )
    params: list = [company_id]
    if search:
        sql += " AND MC.menu_category_name LIKE %s"
        params.append(f"%{search}%")
    if status in ("1", "0"):
        sql += " AND MC.active = %s"
        params.append(int(status))
    sql += " ORDER BY MC.`order`, MC.menu_category_name"
    return db.fetch_all(sql, params)


def options(company_id: int, only_active: bool = False) -> list[dict]:
    sql = "SELECT menu_category_id AS id, menu_category_name AS name, active FROM menu_category WHERE company_detail_id = %s"
    if only_active:
        sql += " AND active = 1"
    return db.fetch_all(sql + " ORDER BY `order`, menu_category_name", (company_id,))


def get(company_id: int, category_id: int) -> dict:
    row = db.fetch_one(
        "SELECT menu_category_id AS id, menu_category_name AS name, active, `order` AS sort_order "
        "FROM menu_category WHERE menu_category_id = %s AND company_detail_id = %s",
        (category_id, company_id),
    )
    if not row:
        raise NotFoundError("That menu category does not exist.")
    return row


def validate(form) -> dict:
    errors = {}
    name = (form.get("name") or "").strip()
    if not name:
        errors["name"] = "Enter a category name."
    elif len(name) > 150:
        errors["name"] = "Keep the name under 150 characters."
    sort_order = to_int(form.get("sort_order") or 0)
    if sort_order is None or not (0 <= sort_order <= 99999):
        errors["sort_order"] = "Display order must be a whole number from 0 to 99999."
    active = 1 if str(form.get("active", "1")) == "1" else 0
    if errors:
        raise ValidationError("Please fix the highlighted fields.", errors=errors)
    return {"name": name, "sort_order": sort_order, "active": active}


def _ensure_unique(company_id: int, name: str, exclude_id: int | None = None) -> None:
    sql = "SELECT COUNT(*) AS c FROM menu_category WHERE company_detail_id = %s AND menu_category_name = %s"
    params: list = [company_id, name]
    if exclude_id:
        sql += " AND menu_category_id <> %s"
        params.append(exclude_id)
    if db.fetch_value(sql, params, 0):
        raise ValidationError("Please fix the highlighted fields.", errors={"name": "A category with this name already exists."})


def create(company_id: int, data: dict) -> int:
    _ensure_unique(company_id, data["name"])
    _, new_id = db.execute(
        "INSERT INTO menu_category (company_detail_id, menu_category_name, menu_revision_id, active, `order`) "
        "VALUES (%s, %s, %s, %s, %s)",
        (company_id, data["name"], DEFAULT_REVISION_ID, data["active"], data["sort_order"]),
    )
    return new_id


def update(company_id: int, category_id: int, data: dict) -> None:
    current = get(company_id, category_id)
    _ensure_unique(company_id, data["name"], exclude_id=category_id)
    with db.transaction() as cur:
        cur.execute(
            "UPDATE menu_category SET menu_category_name = %s, active = %s, `order` = %s "
            "WHERE menu_category_id = %s AND company_detail_id = %s",
            (data["name"], data["active"], data["sort_order"], category_id, company_id),
        )
        # Same rule as the PHP app: showing/hiding a category shows/hides its items.
        if int(current["active"]) != data["active"]:
            cur.execute("UPDATE menu SET active = %s WHERE menu_category_id = %s", (data["active"], category_id))


def toggle(company_id: int, category_id: int) -> int:
    current = get(company_id, category_id)
    new_state = 0 if int(current["active"]) == 1 else 1
    update(company_id, category_id, {"name": current["name"], "active": new_state, "sort_order": current["sort_order"]})
    return new_state


def delete(company_id: int, category_id: int) -> None:
    category = get(company_id, category_id)
    items = db.fetch_value("SELECT COUNT(*) AS c FROM menu WHERE menu_category_id = %s", (category_id,), 0)
    if items:
        raise ValidationError(
            f"'{category['name']}' still has {items} menu item(s). Move or delete them first, or hide the category instead."
        )
    db.execute("DELETE FROM menu_category WHERE menu_category_id = %s AND company_detail_id = %s", (category_id, company_id))


# ------------------------------------------------------------------ import --

def import_rows(company_id: int, rows: list[dict]) -> dict:
    """Adds or updates categories from spreadsheet rows. All-or-nothing."""
    from ..services.menu_io import parse_status

    current_rows = list_for_company(company_id)
    existing = {c["name"].strip().lower(): c for c in current_rows}
    errors, plan, seen = [], [], {}
    for row in rows:
        n = row["_row"]
        name = (row.get("name") or "").strip()
        if not name:
            errors.append(f"Row {n}: Category Name is empty.")
            continue
        if len(name) > 150:
            errors.append(f"Row {n}: Category Name is longer than 150 characters.")
            continue
        key = name.lower()
        if key in seen:
            errors.append(f"Row {n}: '{name}' is listed twice (also on row {seen[key]}).")
            continue
        seen[key] = n
        current = existing.get(key)
        order_text = (row.get("sort_order") or "").strip()
        if order_text:
            try:
                order = int(float(order_text))
                if not 0 <= order <= 99999:
                    raise ValueError
            except ValueError:
                errors.append(f"Row {n}: Display Order must be a whole number from 0 to 99999 (got '{order_text}').")
                continue
        else:
            order = current["sort_order"] if current else 0
        try:
            active = parse_status(row.get("status"), int(current["active"]) if current else 1)
        except ValueError as exc:
            errors.append(f"Row {n}: {exc} (got '{row.get('status')}').")
            continue
        plan.append((current, name, order, active))

    if errors:
        raise ValidationError(f"{len(errors)} problem(s) found. Nothing was imported.", errors={"rows": errors})

    added = updated = unchanged = 0
    with db.transaction() as cur:
        for current, name, order, active in plan:
            if current is None:
                cur.execute(
                    "INSERT INTO menu_category (company_detail_id, menu_category_name, menu_revision_id, active, `order`) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (company_id, name, DEFAULT_REVISION_ID, active, order),
                )
                added += 1
            elif (current["name"], int(current["active"]), current["sort_order"]) == (name, active, order):
                unchanged += 1
            else:
                cur.execute(
                    "UPDATE menu_category SET menu_category_name = %s, active = %s, `order` = %s WHERE menu_category_id = %s",
                    (name, active, order, current["id"]),
                )
                if int(current["active"]) != active:
                    cur.execute("UPDATE menu SET active = %s WHERE menu_category_id = %s", (active, current["id"]))
                updated += 1
    return {"added": added, "updated": updated, "unchanged": unchanged}
