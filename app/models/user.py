"""Staff accounts. These live in `aspirant_registration`, the table the PHP
API's `api` guard signs in against, so existing passwords keep working."""
from __future__ import annotations
import logging

from ..core import passwords

from .. import db

log = logging.getLogger("khatashop.models.user")


def find_by_email(email: str) -> dict | None:
    return db.fetch_one(
        "SELECT aspirant_registration_id AS id, name, email, password, user_type, company_detail_id "
        "FROM aspirant_registration WHERE email = %s ORDER BY aspirant_registration_id LIMIT 1",
        (email,),
    )


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Checks a Laravel bcrypt hash ($2y$...)."""
    if not stored_hash or not password:
        return False
    try:
        return passwords.checkpw(password, stored_hash)
    except ValueError:
        log.warning("Stored password hash is not a valid bcrypt hash; sign-in refused")
        return False


def authenticate(email: str, password: str) -> dict | None:
    user = find_by_email(email)
    if user and verify_password(user.get("password"), password):
        if not user.get("company_detail_id"):
            log.warning("User %s has no company assigned; sign-in refused", user["id"])
            return None
        return user
    return None
