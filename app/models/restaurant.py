"""Restaurants / outlets (table `company_detail`), managed by super admins."""
from __future__ import annotations
import re
from datetime import time

from .. import db
from ..errors import NotFoundError, ValidationError
from . import payment

PLACEHOLDER_GST = "XXXXXXXXXXX"  # what the PHP data uses when there is no GST number
GST_RE = re.compile(r"^[0-9A-Z]{15}$")


def list_all() -> list[dict]:
    return db.fetch_all(
        "SELECT C.company_detail_id AS id, C.company_name_l1 AS name, C.company_name_l2 AS location, C.abbreviation, "
        "C.gst_no, C.status, "
        "(SELECT COUNT(*) FROM bill_payment_mode P WHERE P.company_detail_id = C.company_detail_id) AS payment_modes, "
        "(SELECT COUNT(*) FROM aspirant_registration A WHERE A.company_detail_id = C.company_detail_id) AS staff, "
        "(SELECT COUNT(*) FROM menu_category MC WHERE MC.company_detail_id = C.company_detail_id) AS categories, "
        "(SELECT COUNT(*) FROM menu M JOIN menu_category MC ON MC.menu_category_id = M.menu_category_id "
        "   WHERE MC.company_detail_id = C.company_detail_id) AS item_count, "
        "(SELECT COUNT(*) FROM bill B WHERE B.company_detail_id = C.company_detail_id AND B.payment_mode_id <> 0) AS bills, "
        "(SELECT MAX(B.billing_date) FROM bill B WHERE B.company_detail_id = C.company_detail_id) AS last_bill "
        "FROM company_detail C ORDER BY C.company_name_l1"
    )


def get(company_id: int) -> dict:
    row = db.fetch_one(
        "SELECT company_detail_id AS id, company_name_l1 AS name, company_name_l2 AS location, abbreviation, gst_no, "
        "state, address, open_time, close_time, status FROM company_detail WHERE company_detail_id = %s",
        (company_id,),
    )
    if not row:
        raise NotFoundError("That restaurant does not exist.")
    if row["gst_no"] == PLACEHOLDER_GST:
        row["gst_no"] = ""
    for key in ("open_time", "close_time"):
        value = row[key]
        if value is not None and not isinstance(value, time):  # MySQL TIME comes back as timedelta
            seconds = int(value.total_seconds())
            row[key] = time(seconds // 3600 % 24, seconds // 60 % 60)
    return row


def _parse_time(value: str):
    value = (value or "").strip()
    if not value:
        return None, None
    m = re.match(r"^(\d{1,2}):(\d{2})$", value)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None, "Use the HH:MM format."
    return f"{int(m.group(1)):02d}:{m.group(2)}:00", None


def validate(form, company_id: int | None = None) -> dict:
    errors = {}
    name = (form.get("name") or "").strip()
    location = (form.get("location") or "").strip()
    abbreviation = (form.get("abbreviation") or "").strip()
    gst = (form.get("gst_no") or "").strip().upper()
    if not name:
        errors["name"] = "Enter the restaurant name."
    elif len(name) > 150:
        errors["name"] = "Keep the name under 150 characters."
    if not location:
        errors["location"] = "Enter the location (shown under the name on bills)."
    elif len(location) > 75:
        errors["location"] = "Keep this under 75 characters."
    if not re.match(r"^[A-Za-z]{2}$", abbreviation):
        errors["abbreviation"] = "Use exactly 2 letters, e.g. Kp. Bill numbers start with these."
    else:
        sql = "SELECT company_name_l1 FROM company_detail WHERE BINARY abbreviation = %s"
        params = [abbreviation]
        if company_id:
            sql += " AND company_detail_id <> %s"
            params.append(company_id)
        clash = db.fetch_value(sql + " LIMIT 1", params)
        if clash:
            current = get(company_id)["abbreviation"] if company_id else None
            if current != abbreviation:  # existing duplicates (e.g. two "Op" outlets) are left alone
                errors["abbreviation"] = f"'{abbreviation}' is already used by {clash}. Pick another so bill numbers stay unique."
    if gst and not GST_RE.match(gst):
        errors["gst_no"] = "A GSTIN has 15 letters/digits. Leave empty if not registered."
    open_time, err = _parse_time(form.get("open_time"))
    if err:
        errors["open_time"] = err
    close_time, err = _parse_time(form.get("close_time"))
    if err:
        errors["close_time"] = err
    address = (form.get("address") or "").strip()
    if len(address) > 555:
        errors["address"] = "Keep the address under 555 characters."
    state = (form.get("state") or "").strip()
    if len(state) > 150:
        errors["state"] = "Keep this under 150 characters."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", errors=errors)
    return {
        "name": name,
        "location": location,
        "abbreviation": abbreviation,
        "gst_no": gst or PLACEHOLDER_GST,
        "state": state or None,
        "address": address or None,
        "open_time": open_time,
        "close_time": close_time,
        "status": "open" if form.get("status", "open") == "open" else "closed",
    }


def create(data: dict, add_default_payments: bool = True) -> int:
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO company_detail (company_name_l1, company_name_l2, abbreviation, gst_no, state, address, "
            "open_time, close_time, status) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (data["name"], data["location"], data["abbreviation"], data["gst_no"], data["state"], data["address"],
             data["open_time"], data["close_time"], data["status"]),
        )
        new_id = cur.lastrowid
    if add_default_payments:
        payment.add_defaults(new_id)
    return new_id


def update(company_id: int, data: dict) -> None:
    get(company_id)
    db.execute(
        "UPDATE company_detail SET company_name_l1 = %s, company_name_l2 = %s, abbreviation = %s, gst_no = %s, "
        "state = %s, address = %s, open_time = %s, close_time = %s, status = %s WHERE company_detail_id = %s",
        (data["name"], data["location"], data["abbreviation"], data["gst_no"], data["state"], data["address"],
         data["open_time"], data["close_time"], data["status"], company_id),
    )


def delete(company_id: int) -> None:
    r = get(company_id)
    used = db.fetch_one(
        "SELECT (SELECT COUNT(*) FROM bill WHERE company_detail_id = %s) AS bills, "
        "(SELECT COUNT(*) FROM menu_category WHERE company_detail_id = %s) AS categories, "
        "(SELECT COUNT(*) FROM aspirant_registration WHERE company_detail_id = %s) AS staff",
        (company_id,) * 3,
    )
    if any(used.values()):
        raise ValidationError(
            f"'{r['name']}' has {used['bills']} bills, {used['categories']} categories and {used['staff']} staff accounts, "
            "so it cannot be deleted. Set its status to Closed instead."
        )
    with db.transaction() as cur:
        cur.execute("DELETE FROM bill_payment_mode WHERE company_detail_id = %s", (company_id,))
        cur.execute("DELETE FROM bill_service_mode WHERE company_detail_id = %s", (company_id,))
        cur.execute("DELETE FROM company_detail WHERE company_detail_id = %s", (company_id,))
