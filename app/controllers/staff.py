"""Staff logins of the current restaurant (old Counter Manager / Executive / ... pages), and Attendance."""
import calendar
from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for

from .. import db
from ..errors import ValidationError
from ..models import company as company_model
from ..models import staff as staff_model
from ..security import outlet_required
from ..services import excel
from ..utils import to_int, today
from .helpers import company_id

bp = Blueprint("staff", __name__, url_prefix="/staff")
att_bp = Blueprint("attendance", __name__, url_prefix="/attendance")


@bp.route("/")
@outlet_required
def index():
    role = to_int(request.args.get("role"))
    people = staff_model.list_for_company(company_id())
    if role is not None:
        people = [p for p in people if p["user_type"] == role]
    return render_template("staff/index.html", people=people, roles=staff_model.ROLES, role=role)


def _form(person):
    form = dict(person) if person else {"user_type": 2, "active": 1}
    errors = {}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            data = staff_model.validate(request.form, person["id"] if person else None, require_password=person is None)
            if person is None:
                staff_model.create(company_id(), data)
                flash(f"Login created for {data['name']}.", "success")
            else:
                staff_model.update(company_id(), person["id"], data)
                flash(f"{data['name']} saved." + (" Password changed." if data["password"] else ""), "success")
            return redirect(url_for("staff.index"))
        except ValidationError as exc:
            errors = exc.errors or {"_": exc.message}
            flash(exc.message, "error")
    return render_template("staff/form.html", person=person, form=form, errors=errors, roles=staff_model.ROLES), (422 if errors else 200)


@bp.route("/new", methods=["GET", "POST"])
@outlet_required
def create():
    return _form(None)


@bp.route("/<int:staff_id>", methods=["GET", "POST"])
@outlet_required
def edit(staff_id):
    return _form(staff_model.get(company_id(), staff_id))


# --------------------------------------------------------------- attendance --

def _attendance(month: date):
    """Staff of this restaurant x days of the month, from the biometric punches (`attendances`)."""
    days = calendar.monthrange(month.year, month.month)[1]
    first, last = month.replace(day=1), month.replace(day=days)
    people = db.fetch_all(
        "SELECT aspirant_registration_id AS id, name, employee_id, user_type FROM aspirant_registration "
        "WHERE company_detail_id = %s AND employee_id > 0 AND active = 1 ORDER BY name", (company_id(),))
    statuses = {r["employee_attendance_status_id"]: r for r in db.fetch_all("SELECT * FROM employee_attendance_status")}
    grid = {}
    if people:
        ids = list({p["employee_id"] for p in people})
        marks = ",".join(["%s"] * len(ids))
        for r in db.fetch_all(
            f"SELECT employee_id, DATE(punch_time) AS d, MIN(punch_time) AS first_in, MAX(punch_time) AS last_out, COUNT(*) AS punches, "
            f"MAX(employee_attendance_status_id) AS status FROM attendances WHERE employee_id IN ({marks}) "
            f"AND punch_time BETWEEN %s AND %s GROUP BY employee_id, DATE(punch_time)",
            [*ids, first, f"{last} 23:59:59"],
        ):
            grid[(r["employee_id"], r["d"].day)] = r
    rows = []
    for p in people:
        cells, present = [], 0
        for day in range(1, days + 1):
            r = grid.get((p["employee_id"], day))
            if r and r["punches"]:
                present += 1
                text = r["first_in"].strftime("%H:%M") + ("-" + r["last_out"].strftime("%H:%M") if r["punches"] > 1 else "")
                cells.append({"text": text, "cls": "p", "title": f"{r['punches']} punch(es)"})
            elif r and r["status"] in statuses:
                s = statuses[r["status"]]
                cells.append({"text": s["status_name"][:3], "cls": "", "color": s["status_color"], "title": s["status_name"]})
            else:
                cells.append({"text": "", "cls": "wk" if date(month.year, month.month, day).weekday() == 6 else "", "title": ""})
        rows.append({"name": p["name"], "employee_id": p["employee_id"], "role": staff_model.ROLES.get(p["user_type"], ""),
                     "cells": cells, "present": present})
    return days, rows, statuses


@att_bp.route("/")
@outlet_required
def index():
    raw = request.args.get("month") or today().strftime("%Y-%m")
    try:
        year, mon = (int(x) for x in raw.split("-"))
        month = date(year, mon, 1)
    except (ValueError, TypeError):
        month = today().replace(day=1)
    days, rows, statuses = _attendance(month)
    if request.args.get("export") == "1":
        report = {"title": "Attendance", "columns": [("name", "Employee", "text"), ("employee_id", "Employee ID", "text")]
                  + [(f"d{d}", str(d), "text") for d in range(1, days + 1)] + [("present", "Days present", "int")], "rows": [], "totals": {}}
        for r in rows:
            row = {"name": r["name"], "employee_id": r["employee_id"], "present": r["present"]}
            row.update({f"d{i + 1}": c["text"] for i, c in enumerate(r["cells"])})
            report["rows"].append(row)
        title = f"{company_model.get(company_id())['name']} - Attendance {month:%B %Y}"
        return send_file(excel.report_workbook(report, title), as_attachment=True, download_name=f"attendance_{month:%Y_%m}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return render_template("staff/attendance.html", month=month, days=days, rows=rows, statuses=statuses)
