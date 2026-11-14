"""Super admin accounts.

Kept in their own table (`ks_super_admin`) so the PHP app's tables are not
touched. The table is created automatically the first time it is needed.
"""
import logging
import re
import threading

import bcrypt

from .. import db
from ..errors import NotFoundError, ValidationError
from ..utils import now

log = logging.getLogger("khatashop.models.super_admin")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8

_ready = False
_ready_lock = threading.Lock()

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS ks_super_admin (
  id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  email VARCHAR(150) NOT NULL,
  password VARCHAR(200) NOT NULL,
  active TINYINT(1) NOT NULL DEFAULT 1,
  last_login DATETIME NULL,
  created DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_ks_super_admin_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def ensure_table() -> None:
    global _ready
    if _ready:
        return
    with _ready_lock:
        if not _ready:
            db.execute(CREATE_SQL)
            _ready = True


def hash_password(password: str) -> str:
    # $2y$ prefix keeps hashes interchangeable with Laravel/PHP.
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(10)).decode().replace("$2b$", "$2y$", 1)


def check_password_rules(password: str, field: str = "password") -> None:
    if len(password or "") < MIN_PASSWORD:
        raise ValidationError(f"Use a password of at least {MIN_PASSWORD} characters.", errors={field: f"At least {MIN_PASSWORD} characters."})


def authenticate(email: str, password: str) -> dict | None:
    ensure_table()
    row = db.fetch_one("SELECT id, name, email, password, active FROM ks_super_admin WHERE email = %s", (email,))
    if not row or not row["active"]:
        return None
    try:
        ok = bcrypt.checkpw(password.encode("utf-8"), row["password"].encode("utf-8"))
    except ValueError:
        log.warning("Super admin %s has an invalid password hash", row["id"])
        return None
    if ok:
        db.execute("UPDATE ks_super_admin SET last_login = %s WHERE id = %s", (now(), row["id"]))
        return row
    return None


def list_all() -> list[dict]:
    ensure_table()
    return db.fetch_all("SELECT id, name, email, active, last_login, created FROM ks_super_admin ORDER BY name")


def get(admin_id: int) -> dict:
    ensure_table()
    row = db.fetch_one("SELECT id, name, email, active, last_login, created FROM ks_super_admin WHERE id = %s", (admin_id,))
    if not row:
        raise NotFoundError("That super admin does not exist.")
    return row


def create(name: str, email: str, password: str) -> int:
    ensure_table()
    name, email = (name or "").strip(), (email or "").strip().lower()
    errors = {}
    if not name:
        errors["name"] = "Enter a name."
    if not EMAIL_RE.match(email):
        errors["email"] = "Enter a valid email address."
    elif db.fetch_value("SELECT COUNT(*) AS c FROM ks_super_admin WHERE email = %s", (email,), 0):
        errors["email"] = "A super admin with this email already exists."
    if errors:
        raise ValidationError("Please fix the highlighted fields.", errors=errors)
    check_password_rules(password)
    _, new_id = db.execute(
        "INSERT INTO ks_super_admin (name, email, password, active, created) VALUES (%s, %s, %s, 1, %s)",
        (name, email, hash_password(password), now()),
    )
    log.info("Super admin %s (%s) created", new_id, email)
    return new_id


def set_active(admin_id: int, active: bool, acting_id: int) -> None:
    get(admin_id)
    if not active:
        if admin_id == acting_id:
            raise ValidationError("You cannot deactivate your own account.")
        others = db.fetch_value("SELECT COUNT(*) AS c FROM ks_super_admin WHERE active = 1 AND id <> %s", (admin_id,), 0)
        if not others:
            raise ValidationError("At least one super admin must stay active.")
    db.execute("UPDATE ks_super_admin SET active = %s WHERE id = %s", (1 if active else 0, admin_id))


def set_password(admin_id: int, password: str) -> None:
    get(admin_id)
    check_password_rules(password)
    db.execute("UPDATE ks_super_admin SET password = %s WHERE id = %s", (hash_password(password), admin_id))
