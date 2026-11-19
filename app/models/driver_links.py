"""Which restaurant each delivery driver belongs to.

The PHP `drivers` table has no restaurant column (it is shared with the driver app),
so the link lives in our own table `ks_driver_company`. On first use it is filled from
delivery history: a driver who delivered orders for a restaurant belongs to it.
"""
from __future__ import annotations
import threading

from ..core import current_app

from .. import db

_ready = False
_lock = threading.Lock()


def ensure_table() -> None:
    global _ready
    if _ready:
        return
    with _lock:
        if _ready:
            return
        db.create_table(
            "CREATE TABLE IF NOT EXISTS ks_driver_company ("
            " driver_id INT NOT NULL, company_detail_id INT NOT NULL,"
            " PRIMARY KEY (driver_id, company_detail_id)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
        if not db.fetch_value("SELECT COUNT(*) AS c FROM ks_driver_company", None, 0):
            _backfill()
        _ready = True


def _vendor_to_company() -> dict:
    mapping = {int(k): int(v) for k, v in current_app.config["KS"].get("online_orders", {}).get("vendor_by_company", {}).items()}
    companies = {r["id"] for r in db.fetch_all("SELECT company_detail_id AS id FROM company_detail")}
    out = {v: c for c, v in mapping.items()}
    for c in companies:
        if c not in mapping.values() and c not in out:
            out.setdefault(c, c)
    return out


def _backfill() -> None:
    v2c = _vendor_to_company()
    rows = db.fetch_all("SELECT DISTINCT vendor_id, driver_id FROM orders WHERE driver_id IS NOT NULL AND driver_id > 0")
    known = {r["id"] for r in db.fetch_all("SELECT id FROM drivers")}
    for r in rows:
        company = v2c.get(r["vendor_id"])
        if company and r["driver_id"] in known:
            db.execute("INSERT IGNORE INTO ks_driver_company (driver_id, company_detail_id) VALUES (%s, %s)", (r["driver_id"], company))


def link(driver_id: int, company_id: int) -> None:
    ensure_table()
    db.execute("INSERT IGNORE INTO ks_driver_company (driver_id, company_detail_id) VALUES (%s, %s)", (driver_id, company_id))


def unlink(driver_id: int, company_id: int) -> int:
    """Removes the driver from this restaurant. Returns how many restaurants still use the driver."""
    ensure_table()
    db.execute("DELETE FROM ks_driver_company WHERE driver_id = %s AND company_detail_id = %s", (driver_id, company_id))
    return db.fetch_value("SELECT COUNT(*) AS c FROM ks_driver_company WHERE driver_id = %s", (driver_id,), 0)


def drivers_for(company_id: int) -> list[dict]:
    ensure_table()
    return db.fetch_all(
        "SELECT D.id, TRIM(CONCAT(D.first_name, ' ', COALESCE(D.last_name, ''))) AS name, D.mobile FROM drivers D "
        "JOIN ks_driver_company L ON L.driver_id = D.id WHERE L.company_detail_id = %s ORDER BY D.first_name", (company_id,))
