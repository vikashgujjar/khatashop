"""Restaurant staff logins (table `aspirant_registration`, shared with the PHP app)."""
import re

from .. import db
from ..errors import NotFoundError, ValidationError
from ..utils import now
from .super_admin import check_password_rules, hash_password

# user_type codes as the PHP app's login understands them.
ROLES = {
    8: "Coordinator (outlet admin)",
    1: "Admin",
    7: "Manager",
    2: "Counter Manager",
    3: "Store Manager",
    6: "Executive",
    4: "Waiter",
    5: "Kitchen",
    0: "Employee",
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?\d{10,14}$")


def list_for_company(company_id: int) -> list[dict]:
    rows = db.fetch_all(
        "SELECT aspirant_registration_id AS id, name, email, contact_no, user_type, active, created "
        "FROM aspirant_registration WHERE company_detail_id = %s ORDER BY active DESC, name",
        (company_id,),
    )
    for row in rows:
        row["role"] = ROLES.get(row["user_type"], f"Type {row['user_type']}")
    return rows


def get(company_id: int, staff_id: int) -> dict:
    row = db.fetch_one(
        "SELECT aspirant_registration_id AS id, name, email, contact_no, user_type, active, employee_id "
        "FROM aspirant_registration WHERE aspirant_registration_id = %s AND company_detail_id = %s",
        (staff_id, company_id),
    )
    if not row:
        raise NotFoundError("That staff account does not exist.")
    return row


def validate(form, staff_id: int | None = None, require_password: bool = True) -> dict:
    errors = {}
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    phone = re.sub(r"[\s-]", "", form.get("contact_no") or "")
    try:
        user_type = int(form.get("user_type", 8))
    except (TypeError, ValueError):
        user_type = -1
    if not name:
        errors["name"] = "Enter the person's name."
    elif len(name) > 50:
        errors["name"] = "Keep the name under 50 characters."
    if not EMAIL_RE.match(email) or len(email) > 100:
        errors["email"] = "Enter a valid email address."
    else:
        sql = "SELECT COUNT(*) AS c FROM aspirant_registration WHERE email = %s"
        params = [email]
        if staff_id:
            sql += " AND aspirant_registration_id <> %s"
            params.append(staff_id)
        if db.fetch_value(sql, params, 0):
            errors["email"] = "Another account already uses this email. Each login needs its own email."
    if phone and not PHONE_RE.match(phone):
        errors["contact_no"] = "Enter a valid mobile number."
    if user_type not in ROLES:
        errors["user_type"] = "Choose a role."
    employee_id = (form.get("employee_id") or "").strip()
    if employee_id and (not employee_id.isdigit() or int(employee_id) > 99999999):
        errors["employee_id"] = "Use the number from the attendance machine (digits only)."
    password = form.get("password") or ""
    if require_password or password:
        try:
            check_password_rules(password)
        except ValidationError as exc:
            errors.update(exc.errors)
    if errors:
        raise ValidationError("Please fix the highlighted fields.", errors=errors)
    return {
        "name": name,
        "email": email,
        "contact_no": phone,  # column is NOT NULL; empty string when not given
        "user_type": user_type,
        "active": 1 if form.get("active", "1") == "1" else 0,
        "employee_id": int(employee_id) if employee_id else 0,
        "password": password or None,
    }


def create(company_id: int, data: dict) -> int:
    moment = now()
    _, new_id = db.execute(
        "INSERT INTO aspirant_registration (company_detail_id, employee_id, password, name, email, contact_no, "
        "active, user_type, created, updated) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (company_id, data["employee_id"], hash_password(data["password"]), data["name"], data["email"], data["contact_no"],
         data["active"], data["user_type"], moment, moment),
    )
    return new_id


def update(company_id: int, staff_id: int, data: dict) -> None:
    get(company_id, staff_id)
    db.execute(
        "UPDATE aspirant_registration SET name = %s, email = %s, contact_no = %s, user_type = %s, active = %s, employee_id = %s, updated = %s "
        "WHERE aspirant_registration_id = %s",
        (data["name"], data["email"], data["contact_no"], data["user_type"], data["active"], data["employee_id"], now(), staff_id),
    )
    if data.get("password"):
        db.execute("UPDATE aspirant_registration SET password = %s WHERE aspirant_registration_id = %s",
                   (hash_password(data["password"]), staff_id))
