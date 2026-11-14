"""KhataShop (Python) - application factory."""
import logging
import sys
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, g, request, url_for

from . import db, roles
from .config import load_config
from .errors import register_error_handlers
from .icons import icon
from .security import check_csrf, csrf_token, load_user
from .utils import money


def _setup_logging(cfg: dict) -> None:
    log_cfg = cfg["logging"]
    Path(log_cfg["file"]).parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(log_cfg["level"])
    for handler in list(root.handlers):
        if getattr(handler, "_khatashop", False):
            root.removeHandler(handler)

    file_handler = RotatingFileHandler(
        log_cfg["file"], maxBytes=log_cfg["max_bytes"], backupCount=log_cfg["backup_count"], encoding="utf-8"
    )
    # Windows consoles default to cp1252; never let a log line with ₹ or → raise an encoding error.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    console = logging.StreamHandler(sys.stdout)
    for handler in (file_handler, console):
        handler.setFormatter(fmt)
        handler._khatashop = True
        root.addHandler(handler)


def create_app(config_path=None) -> Flask:
    cfg = load_config(config_path)
    _setup_logging(cfg)

    app = Flask(__name__)
    app.config.update(
        KS=cfg,
        SECRET_KEY=cfg["app"]["secret_key"],
        MAX_CONTENT_LENGTH=cfg["uploads"]["max_upload_mb"] * 1024 * 1024,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=cfg["app"]["session_hours"]),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        TEMPLATES_AUTO_RELOAD=bool(cfg["app"]["debug"]),
        JSON_SORT_KEYS=False,
    )

    app.teardown_appcontext(db.close_conn)
    register_error_handlers(app)

    @app.before_request
    def _before():
        if request.blueprint in ("mobile_api", "legacy_api"):
            return  # app-facing API: token auth (checked by the API itself), no browser session / CSRF
        load_user()
        check_csrf()
        roles.check_request()

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    static_root = Path(app.static_folder)

    def asset(filename: str) -> str:
        """Static URL with a version stamp so browsers pick up edited files."""
        try:
            version = int((static_root / filename).stat().st_mtime)
        except OSError:
            version = 0
        return url_for("static", filename=filename, v=version)

    def url_with(**changes) -> str:
        """Current URL with some query arguments replaced (used for paging/filters)."""
        args = request.args.to_dict()
        args.update(changes)
        args = {k: v for k, v in args.items() if v not in (None, "")}
        return url_for(request.endpoint, **(request.view_args or {}), **args)

    def brand_logo() -> str:
        """The current restaurant's uploaded logo, or the Kailash Parvatam logo."""
        from .services.uploads import logo_url

        user = g.get("user") or {}
        try:
            return logo_url(user.get("company_id")) or asset("img/kp-logo.png")
        except Exception:  # noqa: BLE001 - a logo problem must never break a page
            return asset("img/kp-logo.png")

    @app.context_processor
    def _template_globals():
        user = g.get("user")
        return {
            "csrf_token": csrf_token,
            "current_user": user,
            "is_super": bool(user and user.get("role") == "super"),
            "outlet_nav": (lambda: roles.nav_for(user) if user and user.get("company_id") else []),
            "role_name": roles.ROLE_NAMES.get((user or {}).get("user_type"), ""),
            "can": roles.allowed,
            "active_endpoint": request.endpoint or "",
            "asset": asset,
            "url_with": url_with,
            "brand_logo": brand_logo,
        }

    app.add_template_filter(money, "money")
    app.add_template_global(icon, "icon")

    from .controllers import register_blueprints

    register_blueprints(app)

    logging.getLogger("khatashop").info(
        "KhataShop app ready (config: %s, database: %s@%s/%s)",
        cfg["_path"],
        cfg["database"]["user"],
        cfg["database"]["host"],
        cfg["database"]["name"],
    )
    return app
