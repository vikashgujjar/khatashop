"""Thin MySQL access layer used by the models.

One connection per request (opened lazily, closed on teardown). Statements
run in autocommit mode unless wrapped in `transaction()`.
"""
from __future__ import annotations
import logging
import re
import threading
from contextlib import contextmanager

import mysql.connector
from .core import current_app, g, has_app_context
from mysql.connector import errorcode

from .errors import DatabaseError

log = logging.getLogger("khatashop.db")

_column_cache: dict[tuple[str, str], bool] = {}
_column_lock = threading.Lock()


class Cursor:
    """Wraps a mysql.connector cursor so the rest of the app gets what it expects:
    rows as dicts, execute() returning the number of affected rows, and text that is
    always str (mysql.connector can hand back bytearray, e.g. for GROUP_CONCAT)."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=None):
        self._raw.execute(sql, params)
        return self._raw.rowcount

    def executemany(self, sql, seq):
        self._raw.executemany(sql, list(seq))
        return self._raw.rowcount

    @staticmethod
    def _clean(row):
        if row is None:
            return None
        return {k: (bytes(v).decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else v) for k, v in row.items()}

    def fetchone(self):
        return self._clean(self._raw.fetchone())

    def fetchall(self):
        return [self._clean(r) for r in self._raw.fetchall()]

    @property
    def rowcount(self):
        return self._raw.rowcount

    @property
    def lastrowid(self):
        return self._raw.lastrowid

    def close(self):
        try:
            self._raw.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _cursor(conn) -> Cursor:
    # buffered: every result is read at once, so the next statement never meets "unread result"
    return Cursor(conn.cursor(dictionary=True, buffered=True))


def connect(db_cfg: dict):
    try:
        conn = mysql.connector.connect(
            host=db_cfg["host"],
            port=db_cfg["port"],
            user=db_cfg["user"],
            password=db_cfg["password"],
            database=db_cfg["name"],
            charset=db_cfg["charset"],
            # MariaDB does not know MySQL 8's default utf8mb4_0900_ai_ci; set a collation both understand.
            collation=db_cfg.get("collation") or "utf8mb4_general_ci",
            autocommit=True,
            connection_timeout=db_cfg["connect_timeout"],
            use_unicode=True,
            get_warnings=False,
            use_pure=True,       # the bundled pure-Python driver (vendor/mysql); no C extension needed
        )
    except mysql.connector.Error as exc:
        code = getattr(exc, "errno", None)
        log.error("Database connection failed (%s): %s", code, exc)
        if code == errorcode.ER_ACCESS_DENIED_ERROR:
            raise DatabaseError("The database rejected the username or password in db_config.json.") from exc
        if code == errorcode.ER_BAD_DB_ERROR:
            raise DatabaseError(f"The database '{db_cfg['name']}' does not exist.") from exc
        if code == errorcode.ER_DBACCESS_DENIED_ERROR:
            raise DatabaseError(f"The database user in db_config.json has no access to '{db_cfg['name']}' "
                                "(or that database does not exist). Check database.name and the user's permissions.") from exc
        raise DatabaseError(
            "Cannot connect to the database. Check that MySQL is running and the settings in db_config.json are correct."
        ) from exc
    # Statements that run longer than read_timeout seconds are stopped by the server (MariaDB / MySQL 5.7+).
    timeout = int(db_cfg.get("read_timeout") or 0)
    if timeout > 0:
        for statement in (f"SET SESSION max_statement_time = {timeout}", f"SET SESSION max_execution_time = {timeout * 1000}"):
            try:
                with _cursor(conn) as cur:
                    cur.execute(statement)
                break
            except mysql.connector.Error:
                continue  # the other server type
    return conn


def get_conn():
    if not has_app_context():
        raise RuntimeError("Database access needs an application context")
    conn = g.get("_db_conn")
    if conn is None:
        conn = connect(current_app.config["KS"]["database"])
        g._db_conn = conn
    return conn


def close_conn(_exc=None):
    conn = g.pop("_db_conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - closing a broken socket must never fail a request
            log.debug("Ignoring error while closing DB connection", exc_info=True)


def fetch_all(sql: str, params=None) -> list[dict]:
    with _cursor(get_conn()) as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def fetch_one(sql: str, params=None) -> dict | None:
    with _cursor(get_conn()) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def fetch_value(sql: str, params=None, default=None):
    row = fetch_one(sql, params)
    if not row:
        return default
    value = next(iter(row.values()))
    return default if value is None else value


def execute(sql: str, params=None) -> tuple[int, int]:
    """Runs a write statement. Returns (rows affected, last insert id)."""
    with _cursor(get_conn()) as cur:
        affected = cur.execute(sql, params)
        return affected, cur.lastrowid


@contextmanager
def transaction():
    """Groups statements into one transaction; rolls back on any exception."""
    conn = get_conn()
    conn.start_transaction()
    try:
        with _cursor(conn) as cur:
            yield cur
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            log.exception("Rollback failed")
        raise


@contextmanager
def named_lock(name: str, timeout: int = 10):
    """MySQL advisory lock, used to hand out bill numbers one at a time."""
    got = fetch_value("SELECT GET_LOCK(%s, %s) AS l", (name, timeout))
    if got != 1:
        raise DatabaseError("The system is busy saving another bill. Please try again in a moment.")
    try:
        yield
    finally:
        try:
            fetch_value("SELECT RELEASE_LOCK(%s) AS l", (name,))
        except Exception:  # noqa: BLE001 - the lock is released anyway when the connection closes
            log.warning("Could not release lock %s", name, exc_info=True)


def column_exists(table: str, column: str) -> bool:
    key = (table, column)
    with _column_lock:
        if key in _column_cache:
            return _column_cache[key]
    exists = bool(
        fetch_value(
            "SELECT COUNT(*) AS c FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = %s",
            (table, column),
            0,
        )
    )
    with _column_lock:
        _column_cache[key] = exists
    return exists


# The app's own tables (ks_...). They are created by deploy/001_khatashop_python_tables.sql.
APP_TABLES = ("ks_super_admin", "ks_company_modules", "ks_driver_company", "ks_table_area",
              "ks_kot_note", "ks_kot_ready", "ks_bill_payment", "ks_activity_log")
_table_cache: set = set()


def missing_app_tables() -> list[str]:
    marks = ",".join(["%s"] * len(APP_TABLES))
    have = {r["t"] for r in fetch_all(
        f"SELECT table_name AS t FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name IN ({marks})",
        APP_TABLES)}
    return [t for t in APP_TABLES if t not in have]


def create_table(ddl: str) -> None:
    """Runs a CREATE TABLE IF NOT EXISTS only when database.auto_create_tables is true in
    db_config.json. Otherwise (the default) the app never changes the schema: it only checks
    the table is there and explains what to run if it is not."""
    match = re.search(r"CREATE TABLE IF NOT EXISTS\s+`?(\w+)`?", ddl, re.I)
    name = match.group(1) if match else "?"
    if name in _table_cache:
        return
    if current_app.config["KS"]["database"].get("auto_create_tables"):
        execute(ddl)
    elif not fetch_value(
        "SELECT COUNT(*) AS c FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s", (name,), 0
    ):
        log.error("Table %s is missing and auto_create_tables is off", name)
        raise DatabaseError(
            f"The database table '{name}' is missing. Run deploy/001_khatashop_python_tables.sql on this database "
            "(the app does not create tables by itself)."
        )
    _table_cache.add(name)
