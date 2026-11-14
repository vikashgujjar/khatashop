"""Which modules each restaurant may use, set by super admins.

Stored in our own table `ks_company_modules` (one row per restaurant, JSON list of
enabled module keys). A restaurant without a row has every module enabled, so
existing restaurants keep working unchanged.
"""
import json
import threading

from flask import g, has_request_context

from .. import db
from ..utils import now

# key -> (label, description, access keys from app/roles.py that belong to it)
MODULES = {
    "billing": ("Billing", "Generate Bill, Bill Detail and Reports", {"billing", "bills", "reports"}),
    "tables": ("Table service", "Table View, KOT printing, Kitchen screen, Manage Tables", {"tables", "kitchen", "admin_tables"}),
    "online": ("Online orders", "Order Detail, Item Summary, Drivers, Discount, Delivery Charges", {"online", "online_admin"}),
    "inventory": ("Inventory", "Procurement, Issuing, Consumption, Recipes, Ingredients, Vendors", {"inventory", "issuing"}),
    "menu": ("Menu management", "Menu Category, Menu, Upload PDF, Menu Revision", {"menu"}),
    "staff": ("Staff logins", "Create and manage the restaurant's staff logins", {"staff"}),
    "attendance": ("Attendance", "Monthly attendance from the biometric machine", {"attendance"}),
}
ALL = frozenset(MODULES)
MODULE_OF_ACCESS = {access: key for key, (_l, _d, keys) in MODULES.items() for access in keys}

_ready = False
_lock = threading.Lock()


def ensure_table() -> None:
    global _ready
    if _ready:
        return
    with _lock:
        if not _ready:
            db.execute(
                "CREATE TABLE IF NOT EXISTS ks_company_modules ("
                " company_detail_id INT NOT NULL PRIMARY KEY,"
                " modules TEXT NOT NULL,"
                " updated DATETIME NOT NULL"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
            )
            _ready = True


def enabled(company_id: int | None) -> frozenset:
    """Enabled module keys for a restaurant (cached for the current request)."""
    if not company_id:
        return ALL
    cache = g.setdefault("_ks_modules", {}) if has_request_context() else {}
    if company_id not in cache:
        ensure_table()
        raw = db.fetch_value("SELECT modules FROM ks_company_modules WHERE company_detail_id = %s", (company_id,))
        if raw is None:
            cache[company_id] = ALL
        else:
            try:
                cache[company_id] = frozenset(k for k in json.loads(raw) if k in MODULES)
            except (ValueError, TypeError):
                cache[company_id] = ALL
    return cache[company_id]


def save(company_id: int, keys) -> frozenset:
    ensure_table()
    keys = frozenset(k for k in keys if k in MODULES)
    db.execute(
        "INSERT INTO ks_company_modules (company_detail_id, modules, updated) VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE modules = VALUES(modules), updated = VALUES(updated)",
        (company_id, json.dumps(sorted(keys)), now()),
    )
    if has_request_context():
        g.setdefault("_ks_modules", {})[company_id] = keys
    return keys


def access_enabled(access_key: str, company_id: int | None) -> bool:
    module = MODULE_OF_ACCESS.get(access_key)
    return module is None or module in enabled(company_id)


def delete(company_id: int) -> None:
    ensure_table()
    db.execute("DELETE FROM ks_company_modules WHERE company_detail_id = %s", (company_id,))
