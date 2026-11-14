"""Thin MySQL access layer used by the models.

One connection per request (opened lazily, closed on teardown). Statements
run in autocommit mode unless wrapped in `transaction()`.
"""
import logging
import threading
from contextlib import contextmanager

import pymysql
from flask import current_app, g, has_app_context
from pymysql.cursors import DictCursor

from .errors import DatabaseError

log = logging.getLogger("khatashop.db")

_column_cache: dict[tuple[str, str], bool] = {}
_column_lock = threading.Lock()


def connect(db_cfg: dict):
    try:
        return pymysql.connect(
            host=db_cfg["host"],
            port=db_cfg["port"],
            user=db_cfg["user"],
            password=db_cfg["password"],
            database=db_cfg["name"],
            charset=db_cfg["charset"],
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=db_cfg["connect_timeout"],
            read_timeout=db_cfg["read_timeout"],
            write_timeout=db_cfg["write_timeout"],
        )
    except pymysql.err.OperationalError as exc:
        code = exc.args[0] if exc.args else None
        log.error("Database connection failed (%s): %s", code, exc)
        if code == 1045:
            raise DatabaseError("The database rejected the username or password in db_config.json.") from exc
        if code == 1049:
            raise DatabaseError(f"The database '{db_cfg['name']}' does not exist.") from exc
        raise DatabaseError(
            "Cannot connect to the database. Check that MySQL is running and the settings in db_config.json are correct."
        ) from exc


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
    with get_conn().cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def fetch_one(sql: str, params=None) -> dict | None:
    with get_conn().cursor() as cur:
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
    with get_conn().cursor() as cur:
        affected = cur.execute(sql, params)
        return affected, cur.lastrowid


@contextmanager
def transaction():
    """Groups statements into one transaction; rolls back on any exception."""
    conn = get_conn()
    conn.begin()
    try:
        with conn.cursor() as cur:
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
