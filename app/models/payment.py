"""Payment modes (`bill_payment_mode`) and service modes (`bill_service_mode`).

These are only labels recorded on each bill (Cash, Card, UPI ...).
No payment gateway is involved.
"""
from .. import db
from ..errors import NotFoundError, ValidationError

DEFAULT_MODES = ("Cash", "Card", "UPI")


def payment_modes(company_id: int) -> list[dict]:
    return db.fetch_all(
        "SELECT payment_mode_id AS id, payment_mode AS name, `order` AS sort_order "
        "FROM bill_payment_mode WHERE company_detail_id = %s ORDER BY `order`, payment_mode_id",
        (company_id,),
    )


def payment_modes_with_usage(company_id: int) -> list[dict]:
    """Same as payment_modes plus how many bills use each one (slower; admin screens only)."""
    modes = payment_modes(company_id)
    if modes:
        marks = ",".join(["%s"] * len(modes))
        counts = db.fetch_all(
            f"SELECT payment_mode_id AS id, COUNT(*) AS used FROM bill WHERE payment_mode_id IN ({marks}) GROUP BY payment_mode_id",
            [m["id"] for m in modes],
        )
        used = {c["id"]: c["used"] for c in counts}
        for m in modes:
            m["used"] = used.get(m["id"], 0)
    return modes


def add_mode(company_id: int, name: str) -> int:
    name = (name or "").strip()
    if not name:
        raise ValidationError("Enter the payment option name, e.g. Cash.", errors={"payment_mode": "Required."})
    if len(name) > 40:
        raise ValidationError("Keep the name under 40 characters.", errors={"payment_mode": "Too long."})
    existing = payment_modes(company_id)
    if any(m["name"].lower() == name.lower() for m in existing):
        raise ValidationError(f"'{name}' is already a payment option for this restaurant.", errors={"payment_mode": "Already added."})
    next_order = max([m["sort_order"] for m in existing] + [0]) + 1
    _, new_id = db.execute(
        "INSERT INTO bill_payment_mode (company_detail_id, payment_mode, `order`) VALUES (%s, %s, %s)",
        (company_id, name, next_order),
    )
    return new_id


def add_defaults(company_id: int) -> list[str]:
    """Adds Cash, Card and UPI where missing. Returns the names added."""
    have = {m["name"].lower() for m in payment_modes(company_id)}
    added = []
    for name in DEFAULT_MODES:
        if name.lower() not in have:
            add_mode(company_id, name)
            added.append(name)
    return added


def delete_mode(company_id: int, mode_id: int) -> None:
    mode = db.fetch_one(
        "SELECT payment_mode FROM bill_payment_mode WHERE payment_mode_id = %s AND company_detail_id = %s",
        (mode_id, company_id),
    )
    if not mode:
        raise NotFoundError("That payment option does not exist.")
    mode["used"] = db.fetch_value("SELECT COUNT(*) AS c FROM bill WHERE payment_mode_id = %s", (mode_id,), 0)
    if mode["used"]:
        raise ValidationError(f"'{mode['payment_mode']}' is used on {mode['used']} bills and cannot be removed.")
    db.execute("DELETE FROM bill_payment_mode WHERE payment_mode_id = %s", (mode_id,))


def service_modes(company_id: int) -> list[dict]:
    rows = db.fetch_all(
        "SELECT service_mode_id AS id, service_mode AS name, service_charges AS percent FROM bill_service_mode "
        "WHERE company_detail_id = %s ORDER BY service_mode_id",
        (company_id,),
    )
    for row in rows:
        row["percent"] = float(row["percent"] or 0)
    return rows
