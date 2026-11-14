"""Loads and validates db_config.json.

Every setting has a default here, so db_config.json only needs the values you
want to change. Problems are reported as ConfigError with a message that says
exactly which setting is wrong.
"""
import copy
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "db_config.json"

DEFAULTS = {
    "database": {
        "host": "127.0.0.1",
        "port": 3306,
        "user": "",
        "password": "",
        "name": "",
        "charset": "utf8mb4",
        "connect_timeout": 5,
        "read_timeout": 30,
        "write_timeout": 30,
    },
    "app": {
        "host": "0.0.0.0",
        "port": 5000,
        "secret_key": "",
        "debug": False,
        "timezone": "Asia/Kolkata",
        "session_hours": 12,
        "items_per_page": 20,
    },
    "uploads": {
        "base_dir": "uploads",
        "max_upload_mb": 10,
        # The old website shows uploads/category_img/menupdf.pdf. Uploads by this restaurant (id)
        # also update that shared file; every other restaurant only gets its own menu_pdf/<id>.pdf.
        "legacy_menu_pdf_company": None,
    },
    "server": {
        "threads": 8,
        "auto_restart": True,
        "watch_dirs": ["app"],
        "watch_extensions": [".py", ".html", ".css", ".js", ".json"],
        "restart_delay_seconds": 1.0,
        "max_crash_restarts_per_minute": 5,
    },
    "online_orders": {
        # orders.vendor_id used by each restaurant (company_detail_id) in the customer app.
        # Restaurants not listed use their own id.
        "vendor_by_company": {"2": 1},
    },
    "customers": {
        # website sign-ups (kpusers.organization slug) shown under Customers -> Users, per restaurant id
        "kpusers_organization_by_company": {"6": ["kailash-parvatam"], "8": ["ogi-kailash-parbat"], "9": ["skp-kailash-parvatam"]},
    },
    "legacy_api": {
        # Old app-facing URLs not yet rebuilt in Python are forwarded here unchanged.
        "enabled": True,
        "base_url": "http://127.0.0.1:8000",
        "timeout_seconds": 30,
        # Public address used in links returned to the apps (image / PDF URLs), as in the PHP APP_URL.
        "public_base_url": "https://api.khatashop.com",
    },
    "logging": {
        "level": "INFO",
        "file": "logs/app.log",
        "max_bytes": 5 * 1024 * 1024,
        "backup_count": 5,
    },
}


class ConfigError(Exception):
    """Raised when db_config.json is missing or contains invalid values."""


def config_path() -> Path:
    return Path(os.environ.get("KHATASHOP_CONFIG") or DEFAULT_CONFIG_PATH)


def _merge(base: dict, override: dict, prefix: str = "") -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key not in result:
            # Unknown keys are kept (harmless) so newer configs still load.
            result[key] = value
            continue
        if isinstance(result[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"'{prefix}{key}' must be an object in db_config.json")
            result[key] = _merge(result[key], value, f"{prefix}{key}.")
        else:
            result[key] = value
    return result


def _require_str(cfg, section, key, allow_empty=False):
    value = cfg[section][key]
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ConfigError(f"'{section}.{key}' must be a non-empty text value in db_config.json")


def _require_int(cfg, section, key, low, high):
    value = cfg[section][key]
    if isinstance(value, bool) or not isinstance(value, int) or not (low <= value <= high):
        raise ConfigError(f"'{section}.{key}' must be a whole number between {low} and {high} (got {value!r})")


def _validate(cfg: dict) -> None:
    for key in ("host", "user", "name", "charset"):
        _require_str(cfg, "database", key)
    _require_str(cfg, "database", "password", allow_empty=True)
    _require_int(cfg, "database", "port", 1, 65535)
    for key in ("connect_timeout", "read_timeout", "write_timeout"):
        _require_int(cfg, "database", key, 1, 600)

    _require_str(cfg, "app", "host")
    _require_int(cfg, "app", "port", 1, 65535)
    _require_int(cfg, "app", "session_hours", 1, 24 * 30)
    _require_int(cfg, "app", "items_per_page", 5, 500)
    secret = cfg["app"]["secret_key"]
    if not isinstance(secret, str) or len(secret) < 16 or secret.startswith("CHANGE_ME"):
        raise ConfigError("'app.secret_key' must be a random string of at least 16 characters")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(cfg["app"]["timezone"])
    except Exception as exc:  # noqa: BLE001 - any failure means a bad zone name
        raise ConfigError(f"'app.timezone' is not a valid time zone: {cfg['app']['timezone']!r} ({exc})")

    _require_str(cfg, "uploads", "base_dir")
    _require_int(cfg, "uploads", "max_upload_mb", 1, 200)

    _require_int(cfg, "server", "threads", 1, 64)
    _require_int(cfg, "server", "max_crash_restarts_per_minute", 1, 100)
    if not isinstance(cfg["server"]["watch_dirs"], list) or not cfg["server"]["watch_dirs"]:
        raise ConfigError("'server.watch_dirs' must be a non-empty list of folders")

    level = str(cfg["logging"]["level"]).upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ConfigError("'logging.level' must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
    cfg["logging"]["level"] = level


def _resolve(path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else (BASE_DIR / path).resolve()


def load_config(path=None) -> dict:
    path = Path(path) if path else config_path()
    if not path.is_file():
        raise ConfigError(
            f"Config file not found: {path}\n"
            "Copy db_config.example.json to db_config.json and fill in your database details."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path.name} is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}")
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc}")
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name} must contain a JSON object at the top level")

    cfg = _merge(DEFAULTS, raw)
    _validate(cfg)

    cfg["uploads"]["base_dir"] = str(_resolve(cfg["uploads"]["base_dir"]))
    cfg["logging"]["file"] = str(_resolve(cfg["logging"]["file"]))
    cfg["_path"] = str(path)
    return cfg
