from __future__ import annotations
from ..core import Blueprint, flash, redirect, render_template, request, send_file, url_for

from ..errors import ValidationError
from ..models import menu_category
from ..security import outlet_required
from ..services import menu_io, uploads
from ..utils import now
from .helpers import company_id

bp = Blueprint("menu_category", __name__, url_prefix="/menu-categories")


def _render_index(import_errors=None):
    search = (request.args.get("q") or "").strip()
    status = request.args.get("status", "")
    categories = menu_category.list_for_company(company_id(), search, status)
    for cat in categories:
        cat["image_url"] = uploads.image_url(uploads.CATEGORY_FOLDER, cat["id"])
    return (
        render_template("menu_category/index.html", categories=categories, search=search, status=status, import_errors=import_errors),
        422 if import_errors else 200,
    )


@bp.route("/")
@outlet_required
def index():
    return _render_index()


def _xlsx(buffer, name):
    return send_file(buffer, as_attachment=True, download_name=name,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/template.xlsx")
@outlet_required
def template():
    return _xlsx(menu_io.category_template(), "menu_category_template.xlsx")


@bp.route("/export.xlsx")
@outlet_required
def export():
    return _xlsx(menu_io.category_export(menu_category.list_for_company(company_id())), f"menu_categories_{now():%Y%m%d_%H%M}.xlsx")


@bp.route("/import", methods=["POST"])
@outlet_required
def import_file():
    try:
        rows = menu_io.read_rows(request.files.get("file"), menu_io.CATEGORY_COLUMNS)
        result = menu_category.import_rows(company_id(), rows)
    except ValidationError as exc:
        flash(exc.message, "error")
        return _render_index(exc.errors.get("rows") or [exc.message])
    flash(f"Import finished: {result['added']} added, {result['updated']} updated, {result['unchanged']} unchanged.", "success")
    return redirect(url_for("menu_category.index"))


def _form(category: dict | None):
    form = {"name": "", "sort_order": 0, "active": 1} if category is None else dict(category)
    errors = {}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            data = menu_category.validate(request.form)
            if category is None:
                new_id = menu_category.create(company_id(), data)
                uploads.save_image(request.files.get("image"), uploads.CATEGORY_FOLDER, new_id)
                flash(f"Category '{data['name']}' added.", "success")
            else:
                menu_category.update(company_id(), category["id"], data)
                if request.form.get("remove_image") == "1":
                    uploads.delete_image(uploads.CATEGORY_FOLDER, category["id"])
                uploads.save_image(request.files.get("image"), uploads.CATEGORY_FOLDER, category["id"])
                flash(f"Category '{data['name']}' updated.", "success")
            return redirect(url_for("menu_category.index"))
        except ValidationError as exc:
            errors = exc.errors or {"_": exc.message}
            flash(exc.message, "error")
    image_url = uploads.image_url(uploads.CATEGORY_FOLDER, category["id"]) if category else None
    status = 422 if errors else 200
    return render_template("menu_category/form.html", category=category, form=form, errors=errors, image_url=image_url), status


@bp.route("/new", methods=["GET", "POST"])
@outlet_required
def create():
    return _form(None)


@bp.route("/<int:category_id>/edit", methods=["GET", "POST"])
@outlet_required
def edit(category_id):
    return _form(menu_category.get(company_id(), category_id))


@bp.route("/<int:category_id>/toggle", methods=["POST"])
@outlet_required
def toggle(category_id):
    state = menu_category.toggle(company_id(), category_id)
    flash("Category is now visible for billing." if state else "Category and its items are now hidden.", "success")
    return redirect(request.referrer or url_for("menu_category.index"))


@bp.route("/<int:category_id>/delete", methods=["POST"])
@outlet_required
def delete(category_id):
    try:
        menu_category.delete(company_id(), category_id)
        uploads.delete_image(uploads.CATEGORY_FOLDER, category_id)
        flash("Category deleted.", "success")
    except ValidationError as exc:
        flash(exc.message, "error")
    return redirect(url_for("menu_category.index"))
