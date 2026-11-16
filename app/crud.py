"""Generic list / add / edit / delete pages for simple master tables of the PHP app.

Each Resource declares its table, fields and scope once; controllers/crud.py
serves every resource with the same validated pages.
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal

from . import db
from .errors import NotFoundError, ValidationError
from .models.super_admin import hash_password
from .utils import now, parse_date, to_decimal, to_int

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?\d{7,14}$")
UNITS = ["KG", "GM", "LTR", "ML", "PCS", "PKT", "BOX", "BTL", "CAN", "TRAY", "BAG", "ROLL", "UNIT"]


@dataclass
class Field:
    name: str
    label: str
    type: str = "text"          # text, textarea, number, select, email, phone, password, bool, date
    required: bool = False
    max_len: int = 0
    min_value: float | None = None
    max_value: float | None = None
    decimals: int = 2
    options: object = None       # callable(company_id) -> [(value, label)]
    suggestions: list = field(default_factory=list)
    default: object = ""
    in_list: bool = True
    hint: str = ""
    empty: object = ""           # value stored when an optional field is left empty


@dataclass
class Resource:
    key: str
    title: str
    singular: str
    table: str
    pk: str
    fields: list
    company_column: str | None = None    # column holding company_detail_id on this table
    scope_join: str = ""                 # join used when the company lives on a parent table
    scope_column: str = ""               # e.g. "S.company_detail_id"
    order_by: str = ""
    search: list = field(default_factory=list)
    unique: str = ""                     # field that must be unique within the scope
    in_use_sql: str = ""                 # "SELECT COUNT(*) ... WHERE x = %s" -> refuse delete when > 0
    in_use_message: str = ""
    shared_note: str = ""                # shown when the table is shared by all restaurants
    single_row: bool = False             # settings table with one row (discount)
    extra_insert: dict = field(default_factory=dict)
    timestamps: tuple = ()               # columns set to now() on insert/update
    super_only_write: bool = False       # app-wide settings: restaurants may view, only super admins change
    unique_global: bool = False          # uniqueness checked across all restaurants (e.g. driver mobile = app login)
    after_create: object = None          # callable(company_id, new_id)
    on_delete: object = None             # callable(company_id, record_id) -> True when it handled the delete itself
    super_sees_all: bool = False         # super admins inside a restaurant see every row (to attach existing ones)
    scope_sql: object = None             # callable(company_id) -> (where_sql, params) for custom ownership rules
    read_only: bool = False              # list + delete only (no add / edit), like the old feedback / users pages

    # ---------------------------------------------------------------- SQL --
    def _from(self, for_unique: bool = False) -> str:
        from flask import g
        if (for_unique and self.unique_global) or (self.super_sees_all and (g.get("user") or {}).get("role") == "super"):
            return f"{self.table} X"
        return f"{self.table} X {self.scope_join}"

    def _scope(self, company_id, for_unique: bool = False) -> tuple[str, list]:
        from flask import g
        if for_unique and self.unique_global:
            return "1 = 1", []
        if self.super_sees_all and (g.get("user") or {}).get("role") == "super":
            return "1 = 1", []
        if self.scope_sql:
            return self.scope_sql(company_id)
        if self.company_column:
            return f"X.{self.company_column} = %s", [company_id]
        if self.scope_column:
            return f"{self.scope_column} = %s", [company_id]
        return "1 = 1", []

    def list(self, company_id, q: str = "") -> list[dict]:
        where, params = self._scope(company_id)
        if q and self.search:
            where += " AND (" + " OR ".join(f"X.{c} LIKE %s" for c in self.search) + ")"
            params += [f"%{q}%"] * len(self.search)
        cols = ", ".join(f"X.`{f.name}`" for f in self.fields if f.type != "password")
        rows = db.fetch_all(f"SELECT DISTINCT X.`{self.pk}` AS _id, {cols} FROM {self._from()} WHERE {where} "
                            f"ORDER BY {self.order_by or 'X.' + self.pk + ' DESC'} LIMIT 2000", params)
        labels = {f.name: dict(f.options(company_id)) for f in self.fields if f.type == "select" and f.options}
        for r in rows:
            for name, mapping in labels.items():
                r[name + "__label"] = mapping.get(r[name], mapping.get(str(r[name]), r[name]))
        return rows

    def get(self, company_id, record_id) -> dict:
        where, params = self._scope(company_id)
        cols = ", ".join(f"X.`{f.name}`" for f in self.fields if f.type != "password")
        row = db.fetch_one(f"SELECT DISTINCT X.`{self.pk}` AS _id, {cols} FROM {self._from()} WHERE {where} AND X.`{self.pk}` = %s",
                           params + [record_id])
        if not row:
            raise NotFoundError(f"That {self.singular.lower()} does not exist.")
        return row

    # --------------------------------------------------------- validation --
    def validate(self, company_id, form, record_id=None) -> dict:
        data, errors = {}, {}
        for f in self.fields:
            raw = form.get(f.name)
            text = (raw or "").strip() if isinstance(raw, str) or raw is None else str(raw)
            if f.type == "bool":
                data[f.name] = 1 if form.get(f.name) in ("1", "on", "true") else 0
                continue
            if f.type == "password":
                if not text:
                    if record_id is None and f.required:
                        errors[f.name] = "Enter a password."
                    continue
                if len(text) < 6:
                    errors[f.name] = "Use at least 6 characters."
                    continue
                data[f.name] = hash_password(text)
                continue
            if not text:
                if f.required:
                    errors[f.name] = f"Enter {f.label.lower()}."
                else:
                    data[f.name] = f.empty
                continue
            if f.max_len and len(text) > f.max_len:
                errors[f.name] = f"Keep this under {f.max_len} characters."
            elif f.type == "number":
                n = to_decimal(text)
                if n is None:
                    errors[f.name] = "Enter a number."
                elif (f.min_value is not None and n < Decimal(str(f.min_value))) or (f.max_value is not None and n > Decimal(str(f.max_value))):
                    errors[f.name] = f"Must be between {f.min_value:g} and {f.max_value:g}."
                elif n != round(n, f.decimals):
                    errors[f.name] = f"Use at most {f.decimals} decimal places." if f.decimals else "Use a whole number."
                else:
                    data[f.name] = n
            elif f.type == "email" and not EMAIL_RE.match(text):
                errors[f.name] = "Enter a valid email address."
            elif f.type == "phone" and not PHONE_RE.match(re.sub(r"[\s-]", "", text)):
                errors[f.name] = "Enter a valid phone number."
            elif f.type == "date":
                d = parse_date(text)
                if not d:
                    errors[f.name] = "Enter a valid date."
                else:
                    data[f.name] = d
            elif f.type == "select":
                allowed = {str(v) for v, _ in f.options(company_id)}
                if text not in allowed:
                    errors[f.name] = f"Choose a valid {f.label.lower()}."
                else:
                    data[f.name] = to_int(text, text)
            else:
                data[f.name] = re.sub(r"[\s-]", "", text) if f.type == "phone" else text
        if self.unique and self.unique in data and not errors:
            where, params = self._scope(company_id, for_unique=True)
            sql = f"SELECT COUNT(*) AS c FROM {self._from(for_unique=True)} WHERE {where} AND X.`{self.unique}` = %s"
            params.append(data[self.unique])
            if record_id:
                sql += f" AND X.`{self.pk}` <> %s"
                params.append(record_id)
            if db.fetch_value(sql, params, 0):
                errors[self.unique] = "This already exists."
        if errors:
            raise ValidationError("Please fix the highlighted fields.", errors=errors)
        return data

    # ------------------------------------------------------------- writes --
    def create(self, company_id, data) -> int:
        values = dict(self.extra_insert, **data)
        if self.company_column:
            values[self.company_column] = company_id
        for col in self.timestamps:
            values[col] = now()
        names = ", ".join(f"`{k}`" for k in values)
        _, new_id = db.execute(f"INSERT INTO {self.table} ({names}) VALUES ({', '.join(['%s'] * len(values))})", list(values.values()))
        if self.after_create:
            self.after_create(company_id, new_id)
        return new_id

    def update(self, company_id, record_id, data) -> None:
        self.get(company_id, record_id)
        if self.after_create:  # e.g. a super admin editing a driver attaches it to this restaurant
            self.after_create(company_id, record_id)
        values = dict(data)
        for col in self.timestamps:
            if col != "created" and col != "created_at":
                values[col] = now()
        if not values:
            return
        sets = ", ".join(f"`{k}` = %s" for k in values)
        db.execute(f"UPDATE {self.table} SET {sets} WHERE `{self.pk}` = %s", list(values.values()) + [record_id])

    def delete(self, company_id, record_id) -> None:
        self.get(company_id, record_id)
        if self.on_delete and self.on_delete(company_id, record_id):
            return
        if self.in_use_sql:
            used = db.fetch_value(self.in_use_sql, (record_id,), 0)
            if used:
                raise ValidationError(self.in_use_message.format(n=used))
        db.execute(f"DELETE FROM {self.table} WHERE `{self.pk}` = %s", (record_id,))


# ------------------------------------------------------------- driver hooks --

def _link_driver(company_id, driver_id):
    from .models import driver_links
    driver_links.link(driver_id, company_id)


def _remove_driver(company_id, driver_id):
    """Drop the driver from this restaurant; delete the driver only when no restaurant uses it any more."""
    from .models import driver_links
    remaining = driver_links.unlink(driver_id, company_id)
    if remaining:
        return True
    if db.fetch_value("SELECT COUNT(*) AS c FROM orders WHERE driver_id = %s", (driver_id,), 0):
        return True  # keep history; the driver is just no longer listed for this restaurant
    db.execute("DELETE FROM drivers WHERE id = %s", (driver_id,))
    return True


def _driver_scope_join():
    return "JOIN ks_driver_company L ON L.driver_id = X.id"


# ----------------------------------------------------------------- options --

def _ingredient_categories(company_id):
    return [(r["id"], r["name"]) for r in db.fetch_all(
        "SELECT ingredient_category_id AS id, ingredient_category_name AS name FROM ingredient_category "
        "WHERE company_detail_id = %s ORDER BY ingredient_category_name", (company_id,))]


YES_NO = lambda _c: [(1, "Yes"), (0, "No")]  # noqa: E731
GENDERS = lambda _c: [("Male", "Male"), ("Female", "Female"), ("Other", "Other")]  # noqa: E731

SHARED = "App-wide setting used by the customer app for every restaurant. Only a super admin can change it."

RESOURCES = {
    "ingredient_categories": Resource(
        key="ingredient_categories", title="Ingredient Category", singular="Ingredient category",
        table="ingredient_category", pk="ingredient_category_id", company_column="company_detail_id",
        fields=[Field("ingredient_category_name", "Category name", required=True, max_len=150),
                Field("active", "Active", "bool", default=1)],
        order_by="X.ingredient_category_name", search=["ingredient_category_name"], unique="ingredient_category_name",
        in_use_sql="SELECT COUNT(*) AS c FROM ingredient WHERE ingredient_category_id = %s",
        in_use_message="This category has {n} ingredient(s). Move or delete them first.", timestamps=("created", "updated")),
    "ingredients": Resource(
        key="ingredients", title="Ingredient", singular="Ingredient", table="ingredient", pk="ingredient_id",
        scope_join="JOIN ingredient_category S ON S.ingredient_category_id = X.ingredient_category_id",
        scope_column="S.company_detail_id",
        fields=[Field("ingredient_name", "Ingredient name", required=True, max_len=150),
                Field("ingredient_category_id", "Category", "select", required=True, options=_ingredient_categories),
                Field("ingredient_unit", "Unit", required=True, max_len=20, suggestions=UNITS),
                Field("ingredient_gst", "GST %", "number", min_value=0, max_value=28, default=5, empty=0),
                Field("ingredient_description", "Description", "textarea", max_len=500, in_list=False),
                Field("active", "Active", "bool", default=1)],
        order_by="X.ingredient_name", search=["ingredient_name", "ingredient_unit"],
        in_use_sql="SELECT (SELECT COUNT(*) FROM procurement_note_detail WHERE ingredient_id = %s) AS c",
        in_use_message="This ingredient appears on {n} procurement line(s) and cannot be deleted. Mark it inactive instead.",
        extra_insert={"consumable": 0}, timestamps=("created", "updated")),
    "vendors": Resource(
        key="vendors", title="Vendor Detail", singular="Vendor", table="vendor", pk="vendor_id", company_column="company_detail_id",
        fields=[Field("vendor_name", "Vendor name", required=True, max_len=50),
                Field("vendor_contact", "Contact no.", "phone", required=True, max_len=15),
                Field("vendor_email", "Email", "email", max_len=70),
                Field("vendor_address", "Address", max_len=100, in_list=False),
                Field("vendor_city", "City", max_len=50),
                Field("vendor_state", "State", max_len=50, in_list=False),
                Field("vendor_country", "Country", max_len=50, default="India", in_list=False),
                Field("vendor_gst_no", "GST number", max_len=30),
                Field("vendor_pan", "PAN", max_len=20, in_list=False)],
        order_by="X.vendor_name", search=["vendor_name", "vendor_contact", "vendor_city", "vendor_gst_no"], unique="vendor_name",
        in_use_sql="SELECT COUNT(*) AS c FROM procurement_note WHERE vendor_id = %s",
        in_use_message="This vendor has {n} procurement note(s) and cannot be deleted.", timestamps=("created", "updated")),
    "drivers": Resource(
        key="drivers", title="Drivers", singular="Driver", table="drivers", pk="id",
        fields=[Field("first_name", "First name", required=True, max_len=100),
                Field("last_name", "Last name", max_len=100),
                Field("mobile", "Mobile", "phone", required=True, max_len=15),
                Field("alternate_mobile", "Alternate mobile", "phone", max_len=15, in_list=False, empty=None),
                Field("email", "Email", "email", max_len=150, empty=None),
                Field("password", "Driver app password", "password", in_list=False,
                      hint="Used to sign in to the driver app. Leave empty to keep the current password."),
                Field("gender", "Gender", "select", options=GENDERS, in_list=False, empty=None),
                Field("father_name", "Father's name", max_len=100, in_list=False, empty=None),
                Field("address", "Address", "textarea", max_len=255, in_list=False, empty=None),
                Field("city", "City", max_len=100, empty=None),
                Field("state", "State", max_len=100, in_list=False, empty=None),
                Field("country", "Country", max_len=100, in_list=False, empty=None)],
        order_by="X.first_name", search=["first_name", "last_name", "mobile", "email", "city"], unique="mobile",
        scope_join=_driver_scope_join(), scope_column="L.company_detail_id", unique_global=True, super_sees_all=True,
        after_create=_link_driver, on_delete=_remove_driver,
        shared_note="Drivers sign in to the driver app with their mobile number. Each restaurant sees only its own drivers.",
        extra_insert={"country_code": "+91"}, timestamps=("created_at", "updated_at")),
    "discounts": Resource(
        super_only_write=True,
        key="discounts", title="Discount", singular="Discount", table="discount", pk="id",
        fields=[Field("percentage", "Discount %", "number", required=True, min_value=0, max_value=100),
                Field("first_order_enable", "Only on the first order", "select", required=True,
                      options=lambda _c: [(1, "Yes, first order only"), (0, "No, every order")])],
        shared_note="Discount offered in the customer app. " + SHARED, single_row=True),
    "delivery_charges": Resource(
        super_only_write=True,
        key="delivery_charges", title="Delivery Charges", singular="Delivery charge", table="delivery_charges", pk="id",
        fields=[Field("from_km", "From km", "number", required=True, min_value=0, max_value=1000),
                Field("to_km", "To km", "number", required=True, min_value=0, max_value=1000),
                Field("charges", "Charge (₹)", "number", required=True, min_value=0, max_value=10000)],
        order_by="X.from_km", shared_note="Delivery fee by distance in the customer app. " + SHARED),
}


def _kpusers_scope(company_id):
    """kpusers.organization holds the website slug (e.g. 'ogi-kailash-parbat'); config maps slugs to restaurants."""
    from flask import current_app
    slugs = current_app.config["KS"].get("customers", {}).get("kpusers_organization_by_company", {}).get(str(company_id), [])
    if not slugs:
        return "1 = 0", []
    return "(" + " OR ".join(["X.organization LIKE %s"] * len(slugs)) + ")", [s + "%" for s in slugs]


RESOURCES["feedback"] = Resource(
    key="feedback", title="Feedback Users", singular="Feedback", table="feedback_kp", pk="id", company_column="company_id",
    fields=[Field("name", "Name"), Field("mobile", "Mobile"), Field("email", "Email"), Field("dob", "Date of Birth"),
            Field("service", "Service"), Field("food", "Food"), Field("cost", "Cost"), Field("ambience", "Ambience"),
            Field("refer", "Refer"), Field("suggestions", "Suggestions"), Field("created_at", "Date")],
    order_by="X.id DESC", search=["name", "mobile", "email", "suggestions"], read_only=True)
RESOURCES["kpusers"] = Resource(
    key="kpusers", title="Users", singular="User", table="kpusers", pk="id",
    fields=[Field("name", "Name"), Field("mobile", "Mobile"), Field("organization", "Signed up on")],
    order_by="X.id DESC", search=["name", "mobile"], read_only=True, scope_sql=_kpusers_scope)


def get_resource(key: str) -> Resource:
    if key not in RESOURCES:
        raise NotFoundError("That page does not exist.")
    return RESOURCES[key]
