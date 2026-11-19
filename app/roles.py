"""Who can open what. One table drives both the sidebar and page access.

Roles are the PHP app's user_type codes:
  0 Employee, 1 Admin, 2 Counter Manager, 3 Store Manager, 4 Waiter,
  5 Kitchen, 6 Executive, 7 Manager, 8 Coordinator (outlet admin)
Super admins can open everything.
"""
from __future__ import annotations
from .core import g, request, url_for

from .errors import ForbiddenError

ROLE_NAMES = {0: "Employee", 1: "Admin", 2: "Counter Manager", 3: "Store Manager", 4: "Waiter",
              5: "Kitchen", 6: "Executive", 7: "Manager", 8: "Coordinator"}
FULL = {1, 7, 8}  # outlet admins see every outlet page

# Sidebar: (group, [(endpoint, label, icon, access key)])
NAV = [
    ("Overview", [("dashboard.index", "Dashboard", "grid", "dashboard")]),
    ("Billing", [
        ("tables.index", "Table View", "utensils", "tables"),
        ("tables.kitchen", "Kitchen (KOT)", "printer", "kitchen"),
        ("bills.create", "Generate Bill", "plus", "billing"),
        ("bills.index", "Bill Detail", "receipt", "bills"),
    ]),
    ("Report", [
        ("reports.index?type=item_consumption", "Item Consumption", "percent", "reports+inventory"),
        ("reports.index?type=ingredient_stock", "Ingredient Purchase", "download", "reports+inventory"),
        ("reports.index?type=daily_consolidated", "Daily Sale Consolidated", "layers", "reports"),
        ("reports.index?type=daily_sale", "Daily Sale", "calendar", "reports"),
        ("reports.index?type=gst_monthly", "Monthly GST Report", "percent", "reports"),
        ("reports.index?type=period_sales", "Quarterly / Yearly Sale", "trend-up", "reports"),
        ("online.index", "Order Detail", "truck", "online"),
        ("online.summary", "Item Summary", "box", "online"),
        ("reports.index?type=online_orders", "Online Orders", "receipt", "reports+online"),
        ("reports.index", "All Reports", "trend-up", "reports"),
    ]),
    ("Online Orders", [
        ("crud.index:drivers", "Drivers", "user", "online_admin"),
    ]),
    ("Customers", [
        ("crud.index:feedback", "Feedback Users", "edit", "customers"),
        ("crud.index:kpusers", "Users", "user", "customers"),
    ]),
    ("Inventory", [
        ("notes.index:procurement", "Procurement", "download", "inventory"),
        ("notes.index:issuing", "Issuing", "upload", "issuing"),
        ("notes.index:consumption", "Consumption", "layers", "issuing"),
        ("formula.index", "Consumption Formula", "percent", "inventory"),
        ("crud.index:ingredients", "Ingredient", "box", "inventory"),
        ("crud.index:ingredient_categories", "Ingredient Category", "tag", "inventory"),
        ("crud.index:vendors", "Vendor Detail", "user", "inventory"),
    ]),
    ("Define Menu", [
        ("menu_category.index", "Menu Category", "tag", "menu"),
        ("menu.index", "Menu", "book", "menu"),
        ("menu_pdf.index", "Upload PDF", "file", "menu"),
    ]),
    ("Staff & Settings", [
        ("staff.index", "Staff Logins", "user", "staff"),
        ("attendance.index", "Attendance", "calendar", "attendance"),
        ("crud.index:discounts", "Discount", "percent", "online_admin"),
        ("crud.index:delivery_charges", "Delivery Charges", "truck", "online_admin"),
    ]),
]

# access key -> user_types allowed (FULL roles always allowed)
ACCESS = {
    "dashboard": {0, 2, 3, 4, 5, 6},
    "tables": {2, 4},
    "kitchen": {2, 5},
    "billing": {2},
    "bills": {2, 3},
    "reports": {2, 3},
    "online": {2, 5, 6},
    "online_admin": {6},
    "inventory": {3},
    "issuing": {2, 3},
    "menu": {6},
    "staff": set(),
    "attendance": {0, 2, 3, 4, 5, 6},
    "admin_tables": set(),
    "customers": {6},
}

# blueprint (or endpoint) -> access key, checked on every request
BLUEPRINT_ACCESS = {
    "dashboard": "dashboard", "tables": "tables", "bills": "bills", "reports": "reports",
    "menu_category": "menu", "menu": "menu", "menu_pdf": "menu", "online": "online",
    "notes": "inventory", "formula": "inventory", "staff": "staff", "attendance": "attendance",
}
ENDPOINT_ACCESS = {
    "tables.kitchen": "kitchen", "tables.kitchen_json": "kitchen", "tables.kitchen_ready": "kitchen",
    "bills.create": "billing", "bills.store": "billing", "bills.setup_payment_options": "billing",
    "tables.manage": "admin_tables", "tables.manage_update": "admin_tables", "tables.manage_delete": "admin_tables",
}
# generic CRUD resources and inventory notes have their own keys
RESOURCE_ACCESS = {
    "drivers": "online_admin", "discounts": "online_admin", "delivery_charges": "online_admin",
    "ingredients": "inventory", "ingredient_categories": "inventory", "vendors": "inventory",
    "feedback": "customers", "kpusers": "customers", "procurement": "inventory", "issuing": "issuing", "consumption": "issuing",
}
WAITER_BLOCKED = {"tables.bill", "tables.settle", "tables.cancel", "tables.part_payment_add", "tables.part_payment_remove"}  # waiters take orders, counters bill


def allowed(key: str, user: dict | None = None) -> bool:
    """Role allows it AND the restaurant has the module turned on (set by super admins)."""
    from .models import modules

    user = user if user is not None else (g.get("user") or {})
    if not user:
        return False
    if not modules.access_enabled(key, user.get("company_id")):
        return False
    if user.get("role") == "super":
        return True
    ut = user.get("user_type")
    return ut in FULL or ut in ACCESS.get(key, set())


def check_request() -> None:
    """Called before every request: refuses pages the signed-in role may not open."""
    from .models import modules

    user = g.get("user")
    if not user or not request.endpoint:
        return
    endpoint = request.endpoint
    key = ENDPOINT_ACCESS.get(endpoint)
    if key is None and (request.view_args or {}).get("resource") in RESOURCE_ACCESS:
        key = RESOURCE_ACCESS[request.view_args["resource"]]
    if key is None:
        key = BLUEPRINT_ACCESS.get(endpoint.split(".")[0])
    if key and user.get("company_id") and not modules.access_enabled(key, user["company_id"]):
        label = modules.MODULES[modules.MODULE_OF_ACCESS[key]][0]
        raise ForbiddenError(f"The {label} module is turned off for this restaurant. A super admin can turn it on under Restaurants > Manage.")
    if user.get("role") == "super":
        return
    if key and not allowed(key, user):
        raise ForbiddenError(f"Your role ({ROLE_NAMES.get(user.get('user_type'), 'staff')}) cannot open this page.")
    if user.get("user_type") == 4 and endpoint in WAITER_BLOCKED:
        raise ForbiddenError("Waiters can take orders and send KOTs. Billing is done at the counter.")


# pages that should highlight a sidebar entry other than their own endpoint
ACTIVE_ALIASES = {
    "bills.show": "bills.index", "bills.export": "bills.index",
    "tables.order": "tables.index", "tables.manage": "tables.index", "tables.kot_print": "tables.index",
    "online.print_order": "online.index",
}


def _is_active(endpoint: str, resource: str | None, query: dict | None = None) -> bool:
    current = request.endpoint or ""
    if endpoint == "reports.index":
        kind = request.args.get("type") or "daily"
        if current not in ("reports.index", "reports.export"):
            return False
        named = {"item_consumption", "ingredient_stock", "daily_consolidated", "daily_sale", "online_orders", "gst_monthly", "period_sales"}
        return (query or {}).get("type") == kind if query else kind not in named
    current = ACTIVE_ALIASES.get(current, current)
    if resource:
        return current.split(".")[0] == endpoint.split(".")[0] and (request.view_args or {}).get("resource") == resource
    if current == endpoint:
        return True
    bp = endpoint.split(".")[0]
    siblings = {e.split("?")[0] for _g, links in NAV for e, *_ in links if e.split(".")[0] == bp and ":" not in e}
    return len(siblings) == 1 and current.split(".")[0] == bp


def nav_for(user: dict) -> list:
    """Sidebar groups for this user: [(group, [{url, label, icon, active}])]."""
    groups = []
    for group, links in NAV:
        items = []
        for endpoint, label, icon, key in links:
            if not all(allowed(k, user) for k in key.split("+")):
                continue
            endpoint, _, qs = endpoint.partition("?")
            query = dict(p.split("=") for p in qs.split("&")) if qs else None
            ep, _, resource = endpoint.partition(":")
            url = url_for(ep, resource=resource, **(query or {})) if resource else url_for(ep, **(query or {}))
            items.append({"url": url, "label": label, "icon": icon, "active": _is_active(ep, resource or None, query)})
        if items:
            groups.append((group, items))
    return groups


def home_endpoint(user: dict) -> str:
    """Where a role lands after signing in."""
    ut = user.get("user_type")
    if user.get("role") == "super" and not user.get("company_id"):
        return "admin.restaurants"
    target = {4: "tables.index", 5: "tables.kitchen"}.get(ut, "dashboard.index")
    if target != "dashboard.index" and not allowed("tables" if ut == 4 else "kitchen", user):
        target = "dashboard.index"  # module turned off for this restaurant
    return target
