import logging

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..models import super_admin
from ..models import user as user_model
from ..security import login_throttle, login_user, logout_user
from .helpers import safe_next

bp = Blueprint("auth", __name__)
log = logging.getLogger("khatashop.auth")


def _home():
    user = g.get("user")
    if user and user.get("role") == "super" and not user.get("company_id"):
        return url_for("admin.restaurants")
    return url_for("dashboard.index")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user"):
        return redirect(_home())

    email = ""
    error = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        key = f"{email}|{request.remote_addr}"
        wait = login_throttle.seconds_locked(key)
        if not email or not password:
            error = "Enter your email and password."
        elif wait:
            error = f"Too many failed attempts. Try again in {max(1, wait // 60)} minute(s)."
        else:
            admin = super_admin.authenticate(email, password)
            staff = None if admin else user_model.authenticate(email, password)
            if admin or staff:
                login_throttle.succeeded(key)
                login_user(admin or staff, role="super" if admin else "staff")
                log.info("%s %s (%s) signed in", "Super admin" if admin else "User", (admin or staff)["id"], email)
                from flask import session as _session
                from ..roles import home_endpoint
                home = url_for(home_endpoint(_session["user"]))
                return redirect(safe_next(request.args.get("next"), home))
            login_throttle.failed(key)
            log.warning("Failed sign-in for %s from %s", email, request.remote_addr)
            error = "The email or password is incorrect."
    return render_template("auth/login.html", email=email, error=error), (401 if error else 200)


@bp.route("/logout", methods=["POST"])
def logout():
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))
