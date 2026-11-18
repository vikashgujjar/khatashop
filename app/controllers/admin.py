"""Super admin area: restaurants, their payment options and staff logins, and super admin accounts."""
import logging

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from ..errors import ValidationError
from ..models import modules, payment, staff, super_admin
from ..models import restaurant as restaurant_model
from ..security import enter_outlet, leave_outlet, super_admin_required
from ..services import uploads

bp = Blueprint("admin", __name__, url_prefix="/admin")
log = logging.getLogger("khatashop.admin")


@bp.route("/")
@super_admin_required
def home():
    return redirect(url_for("admin.restaurants"))


# ------------------------------------------------------------- restaurants --

@bp.route("/restaurants")
@super_admin_required
def restaurants():
    rows = restaurant_model.list_all()
    for r in rows:
        r["logo_url"] = uploads.logo_url(r["id"])
        on = modules.enabled(r["id"])
        r["modules_on"] = [modules.MODULES[k][0] for k in modules.MODULES if k in on]
        r["modules_off"] = [modules.MODULES[k][0] for k in modules.MODULES if k not in on]
    return render_template("admin/restaurants.html", restaurants=rows)


def _restaurant_form(rest: dict | None):
    form = dict(rest) if rest else {"status": "open"}
    selected = set(modules.enabled(rest["id"])) if rest else set(modules.ALL)
    errors = {}
    if request.method == "POST":
        form = request.form.to_dict()
        # Only change modules when the form's module section was sent (marker field); otherwise keep them.
        modules_sent = request.form.get("modules_present") == "1"
        if modules_sent:
            selected = {k for k in request.form.getlist("modules") if k in modules.MODULES}
        try:
            data = restaurant_model.validate(request.form, rest["id"] if rest else None)
            if rest is None:
                new_id = restaurant_model.create(data, add_default_payments=request.form.get("default_payments") == "1")
                if modules_sent:
                    modules.save(new_id, selected)
                uploads.save_logo(request.files.get("logo"), new_id)
                log.info("Super admin %s registered restaurant %s (%s)", g.user["id"], new_id, data["name"])
                flash(f"'{data['name']}' registered. Next, add a staff login so the restaurant can sign in.", "success")
                return redirect(url_for("admin.restaurant", company_id=new_id) + "#staff")
            restaurant_model.update(rest["id"], data)
            before = modules.enabled(rest["id"])
            if modules_sent:
                modules.save(rest["id"], selected)
            if modules_sent and before != frozenset(selected):
                log.info("Super admin %s set modules of restaurant %s to %s", g.user["id"], rest["id"], sorted(selected))
            if request.form.get("remove_logo") == "1":
                uploads.delete_logo(rest["id"])
            uploads.save_logo(request.files.get("logo"), rest["id"])
            flash(f"'{data['name']}' saved.", "success")
            return redirect(url_for("admin.restaurant", company_id=rest["id"]))
        except ValidationError as exc:
            errors = exc.errors or {"_": exc.message}
            flash(exc.message, "error")
    context = {"rest": rest, "form": form, "errors": errors, "modules": modules.MODULES, "selected": selected}
    if rest:
        context.update(
            logo_url=uploads.logo_url(rest["id"]),
            payment_modes=payment.payment_modes_with_usage(rest["id"]),
            staff=staff.list_for_company(rest["id"]),
        )
    return render_template("admin/restaurant_form.html", **context), (422 if errors else 200)


@bp.route("/restaurants/new", methods=["GET", "POST"])
@super_admin_required
def restaurant_new():
    return _restaurant_form(None)


@bp.route("/restaurants/<int:company_id>", methods=["GET", "POST"])
@super_admin_required
def restaurant(company_id):
    return _restaurant_form(restaurant_model.get(company_id))


@bp.route("/restaurants/<int:company_id>/delete", methods=["POST"])
@super_admin_required
def restaurant_delete(company_id):
    try:
        restaurant_model.delete(company_id)
        modules.delete(company_id)
        uploads.delete_logo(company_id)
        flash("Restaurant deleted.", "success")
        return redirect(url_for("admin.restaurants"))
    except ValidationError as exc:
        flash(exc.message, "error")
        return redirect(url_for("admin.restaurant", company_id=company_id))


@bp.route("/restaurants/<int:company_id>/open", methods=["POST"])
@super_admin_required
def restaurant_open(company_id):
    rest = restaurant_model.get(company_id)
    enter_outlet(rest["id"], rest["name"])
    flash(f"You are now working in {rest['name']}.", "success")
    return redirect(url_for("dashboard.index"))


@bp.route("/exit", methods=["POST"])
@super_admin_required
def exit_outlet():
    leave_outlet()
    return redirect(url_for("admin.restaurants"))


# ---------------------------------------------------------- payment modes --

@bp.route("/restaurants/<int:company_id>/payment-modes", methods=["POST"])
@super_admin_required
def payment_mode_add(company_id):
    restaurant_model.get(company_id)
    try:
        if request.form.get("defaults") == "1":
            added = payment.add_defaults(company_id)
            flash(f"Added {', '.join(added)}." if added else "Cash, Card and UPI are already set up.", "success")
        else:
            payment.add_mode(company_id, request.form.get("payment_mode"))
            flash("Payment option added.", "success")
    except ValidationError as exc:
        flash(exc.message, "error")
    return redirect(url_for("admin.restaurant", company_id=company_id) + "#payments")


@bp.route("/restaurants/<int:company_id>/payment-modes/<int:mode_id>/delete", methods=["POST"])
@super_admin_required
def payment_mode_delete(company_id, mode_id):
    try:
        payment.delete_mode(company_id, mode_id)
        flash("Payment option removed.", "success")
    except ValidationError as exc:
        flash(exc.message, "error")
    return redirect(url_for("admin.restaurant", company_id=company_id) + "#payments")


# ------------------------------------------------------------------- staff --

def _staff_form(rest: dict, person: dict | None):
    form = dict(person) if person else {"user_type": 8, "active": 1}
    errors = {}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            data = staff.validate(request.form, person["id"] if person else None, require_password=person is None)
            if person is None:
                staff.create(rest["id"], data)
                flash(f"Login created for {data['name']}. They can sign in with {data['email']}.", "success")
            else:
                staff.update(rest["id"], person["id"], data)
                flash(f"{data['name']} saved." + (" Password changed." if data["password"] else ""), "success")
            return redirect(url_for("admin.restaurant", company_id=rest["id"]) + "#staff")
        except ValidationError as exc:
            errors = exc.errors or {"_": exc.message}
            flash(exc.message, "error")
    return render_template("admin/staff_form.html", rest=rest, person=person, form=form, errors=errors, roles=staff.ROLES), (422 if errors else 200)


@bp.route("/restaurants/<int:company_id>/staff/new", methods=["GET", "POST"])
@super_admin_required
def staff_new(company_id):
    return _staff_form(restaurant_model.get(company_id), None)


@bp.route("/restaurants/<int:company_id>/staff/<int:staff_id>", methods=["GET", "POST"])
@super_admin_required
def staff_edit(company_id, staff_id):
    return _staff_form(restaurant_model.get(company_id), staff.get(company_id, staff_id))


# ------------------------------------------------------------ super admins --

@bp.route("/super-admins", methods=["GET", "POST"])
@super_admin_required
def super_admins():
    errors, form = {}, {}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            super_admin.create(form.get("name"), form.get("email"), form.get("password"))
            flash(f"Super admin {form.get('email')} added.", "success")
            return redirect(url_for("admin.super_admins"))
        except ValidationError as exc:
            errors = exc.errors or {"_": exc.message}
            flash(exc.message, "error")
    return render_template("admin/super_admins.html", admins=super_admin.list_all(), form=form, errors=errors), (422 if errors else 200)


@bp.route("/super-admins/<int:admin_id>/toggle", methods=["POST"])
@super_admin_required
def super_admin_toggle(admin_id):
    target = super_admin.get(admin_id)
    try:
        super_admin.set_active(admin_id, not target["active"], g.user["id"])
        flash(f"{target['email']} {'deactivated' if target['active'] else 'activated'}.", "success")
    except ValidationError as exc:
        flash(exc.message, "error")
    return redirect(url_for("admin.super_admins"))


@bp.route("/super-admins/<int:admin_id>/password", methods=["POST"])
@super_admin_required
def super_admin_password(admin_id):
    try:
        super_admin.set_password(admin_id, request.form.get("password"))
        flash("Password changed.", "success")
    except ValidationError as exc:
        flash(exc.message, "error")
    return redirect(url_for("admin.super_admins"))


# ------------------------------------------------------------ activity log --

def _activity_filters():
    from datetime import datetime, time

    from .. import date_ranges

    start, end, active, note = date_ranges.resolve(request.args, "today", max_days=366)
    filters = {
        "start": start, "end": end, "start_dt": datetime.combine(start, time.min), "end_dt": datetime.combine(end, time.max),
        "company_id": request.args.get("company_id", ""), "category": request.args.get("category", ""),
        "status": request.args.get("status", ""), "q": (request.args.get("q") or "").strip()[:100],
    }
    return filters, active, note


@bp.route("/activity")
@super_admin_required
def activity_log():
    from .. import activity, date_ranges
    from ..roles import ROLE_NAMES
    from ..utils import today

    filters, active, note = _activity_filters()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    rows, pager = activity.search(filters, page)
    return render_template(
        "admin/activity.html", rows=rows, pager=pager, f=filters, stats=activity.summary(filters),
        restaurants=restaurant_model.list_all(), categories=activity.CATEGORIES, role_names=ROLE_NAMES,
        start=filters["start"], end=filters["end"], active_range=active, range_labels=date_ranges.LABELS,
        range_note=note, today_iso=today().isoformat(),
    )


@bp.route("/activity/export")
@super_admin_required
def activity_export():
    from flask import send_file

    from .. import activity
    from ..roles import ROLE_NAMES
    from ..services import excel
    from ..utils import now

    filters, _active, _note = _activity_filters()
    rows = activity.export_rows(filters)
    data = {
        "title": "Activity log",
        "columns": [("time", "Time", "text"), ("restaurant", "Restaurant", "text"), ("user", "User", "text"), ("email", "Email", "text"),
                    ("role", "Role", "text"), ("category", "Area", "text"), ("label", "Action", "text"), ("target", "On", "text"),
                    ("details", "Details", "text"), ("status", "Result", "text"), ("ip", "IP address", "text")],
        "rows": [{"time": r["created"].strftime("%d-%m-%Y %H:%M:%S"), "restaurant": r["restaurant"] or ("—" if r["company_detail_id"] is None else f"#{r['company_detail_id']}"),
                  "user": r["user_name"] or "", "email": r["user_email"] or "",
                  "role": "Super admin" if r["role"] == "super" else ROLE_NAMES.get(r["user_type"], "Staff" if r["role"] else ""),
                  "category": r["category"], "label": r["label"], "target": r["target"] or "", "details": r["details"] or "",
                  "status": {"ok": "Done", "failed": "Failed", "denied": "Not allowed"}.get(r["status"], r["status"]), "ip": r["ip"] or ""}
                 for r in rows],
        "totals": {},
        "no_totals": True,
    }
    title = f"Activity log ({filters['start']:%d-%m-%Y} to {filters['end']:%d-%m-%Y})"
    return send_file(excel.report_workbook(data, title), as_attachment=True,
                     download_name=f"activity_log_{filters['start']:%Y%m%d}_{filters['end']:%Y%m%d}_{now():%H%M}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
