"""Sign-in session, CSRF protection and login throttling."""
from __future__ import annotations
import functools
import hmac
import secrets
import threading
import time

from .core import g, request, session

from .errors import AuthError, ForbiddenError

CSRF_SESSION_KEY = "_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def check_csrf() -> None:
    if request.method in SAFE_METHODS:
        return
    expected = session.get(CSRF_SESSION_KEY)
    sent = request.headers.get("X-CSRF-Token") or request.form.get("_csrf")
    if not expected or not sent or not hmac.compare_digest(str(expected), str(sent)):
        raise ForbiddenError("Your session has expired or the form is out of date. Please reload the page and try again.")


def load_user() -> None:
    g.user = session.get("user")


def login_user(user: dict, role: str = "staff") -> None:
    """role is 'staff' (restaurant login) or 'super' (super admin)."""
    session.clear()
    session.permanent = True
    session["user"] = {
        "id": int(user["id"]),
        "name": user.get("name") or user.get("email"),
        "email": user.get("email"),
        "user_type": user.get("user_type"),
        "role": role,
        "company_id": int(user["company_detail_id"]) if role == "staff" else None,
        "company_name": None,
    }
    csrf_token()


def is_super_admin() -> bool:
    user = g.get("user")
    return bool(user and user.get("role") == "super")


def enter_outlet(company_id: int, company_name: str) -> None:
    """Lets a super admin work inside one restaurant's screens."""
    user = dict(session["user"])
    user["company_id"] = int(company_id)
    user["company_name"] = company_name
    session["user"] = user


def leave_outlet() -> None:
    user = dict(session["user"])
    user["company_id"] = None
    user["company_name"] = None
    session["user"] = user


def logout_user() -> None:
    session.clear()


def login_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not g.get("user"):
            raise AuthError("Please sign in to continue.")
        return view(*args, **kwargs)

    return wrapper


class OutletNotSelected(Exception):
    """A super admin opened a restaurant screen without choosing a restaurant first."""


def outlet_required(view):
    """Signed in and working inside a restaurant."""
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        user = g.get("user")
        if not user:
            raise AuthError("Please sign in to continue.")
        if not user.get("company_id"):
            raise OutletNotSelected()
        return view(*args, **kwargs)

    return wrapper


def super_admin_required(view):
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if not g.get("user"):
            raise AuthError("Please sign in to continue.")
        if not is_super_admin():
            raise ForbiddenError("Only super admins can open this page.")
        return view(*args, **kwargs)

    return wrapper


class LoginThrottle:
    """Blocks an email/IP pair for a while after repeated failed sign-ins."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 600, lock_seconds: int = 600):
        self.max_failures = max_failures
        self.window = window_seconds
        self.lock_seconds = lock_seconds
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def seconds_locked(self, key: str) -> int:
        with self._lock:
            until = self._locked_until.get(key, 0)
            remaining = until - time.time()
            if remaining <= 0:
                self._locked_until.pop(key, None)
                return 0
            return int(remaining) + 1

    def failed(self, key: str) -> None:
        now = time.time()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < self.window]
            recent.append(now)
            if len(recent) >= self.max_failures:
                self._locked_until[key] = now + self.lock_seconds
                recent = []
            self._failures[key] = recent

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


login_throttle = LoginThrottle()
