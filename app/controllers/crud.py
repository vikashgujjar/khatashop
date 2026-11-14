"""One controller for all simple master pages declared in app/crud.py."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from flask import g

from ..crud import get_resource as _get_resource
from ..errors import ForbiddenError, ValidationError
from ..models import driver_links
from ..security import is_super_admin, outlet_required
from .helpers import company_id

bp = Blueprint("crud", __name__, url_prefix="/m")


def get_resource(key):
    res = _get_resource(key)
    if res.key == "drivers":
        driver_links.ensure_table()
    return res


def can_write(res) -> bool:
    return not res.super_only_write or is_super_admin()


def can_edit(res) -> bool:
    return can_write(res) and not res.read_only


def _require_write(res):
    if not can_write(res):
        raise ForbiddenError(f"{res.title} is an app-wide setting shared by every restaurant. Only a super admin can change it.")


@bp.route("/<resource>/")
@outlet_required
def index(resource):
    res = get_resource(resource)
    if res.single_row:
        rows = res.list(company_id())
        return redirect(url_for("crud.edit", resource=resource, record_id=rows[0]["_id"]) if rows else url_for("crud.create", resource=resource))
    q = (request.args.get("q") or "").strip()
    return render_template("crud/index.html", res=res, rows=res.list(company_id(), q), q=q, can_write=can_write(res), can_edit=can_edit(res))


def _form(res, record):
    form = {f.name: f.default for f in res.fields} if record is None else dict(record)
    errors = {}
    if request.method == "POST":
        _require_write(res)
        form = request.form.to_dict()
        try:
            data = res.validate(company_id(), request.form, record["_id"] if record else None)
            if record is None:
                res.create(company_id(), data)
                flash(f"{res.singular} added.", "success")
            else:
                res.update(company_id(), record["_id"], data)
                flash(f"{res.singular} saved.", "success")
            return redirect(url_for("crud.index", resource=res.key) if not res.single_row else url_for("crud.index", resource=res.key))
        except ValidationError as exc:
            errors = exc.errors or {"_": exc.message}
            flash(exc.message, "error")
    options = {f.name: f.options(company_id()) for f in res.fields if f.type == "select" and f.options}
    return render_template("crud/form.html", res=res, record=record, form=form, errors=errors, options=options,
                           can_write=can_write(res)), (422 if errors else 200)


@bp.route("/<resource>/new", methods=["GET", "POST"])
@outlet_required
def create(resource):
    res = get_resource(resource)
    _require_write(res)
    if res.read_only:
        raise ForbiddenError(f"{res.title} come from the website and cannot be added here.")
    return _form(res, None)


@bp.route("/<resource>/<int:record_id>", methods=["GET", "POST"])
@outlet_required
def edit(resource, record_id):
    res = get_resource(resource)
    if res.read_only and request.method == "POST":
        raise ForbiddenError(f"{res.title} cannot be edited.")
    return _form(res, res.get(company_id(), record_id))


@bp.route("/<resource>/<int:record_id>/delete", methods=["POST"])
@outlet_required
def delete(resource, record_id):
    res = get_resource(resource)
    _require_write(res)
    try:
        res.delete(company_id(), record_id)
        flash(f"{res.singular} deleted.", "success")
    except ValidationError as exc:
        flash(exc.message, "error")
    return redirect(url_for("crud.index", resource=resource))
