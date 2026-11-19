from __future__ import annotations
from ..core import current_app, g, request

from ..utils import to_int


def company_id() -> int:
    return g.user["company_id"]


def user_id() -> int:
    """Counter number stored on bills. Super admins are not in the staff table, so they bill as counter 0."""
    return 0 if g.user.get("role") == "super" else g.user["id"]


ADMIN_TYPES = {1, 7, 8}  # Admin, Manager, Coordinator


def is_outlet_admin() -> bool:
    """Super admins and the restaurant's Admin / Manager / Coordinator logins."""
    user = g.user or {}
    return user.get("role") == "super" or user.get("user_type") in ADMIN_TYPES


def page_arg() -> int:
    return max(1, to_int(request.args.get("page"), 1))


def per_page() -> int:
    default = current_app.config["KS"]["app"]["items_per_page"]
    value = to_int(request.args.get("per_page"), default)
    return value if value in (10, 20, 25, 50, 100) else default


def safe_next(target: str | None, fallback: str) -> str:
    """Only allow redirects to paths on this site."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return fallback
