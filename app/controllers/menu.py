from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for

from ..errors import ValidationError
from ..models import menu_category, menu_item
from ..security import outlet_required
from ..services import menu_io, uploads
from ..utils import now, to_int
from .helpers import company_id, page_arg, per_page

bp = Blueprint("menu", __name__, url_prefix="/menu")


def _filters():
    return {
        "category_id": to_int(request.args.get("category")),
        "search": (request.args.get("q") or "").strip(),
        "veg": request.args.get("veg", ""),
        "status": request.args.get("status", ""),
    }


def _render_index(import_errors=None):
    filters = _filters()
    items, pager = menu_item.paginate(company_id(), page_arg(), per_page(), **filters)
    for item in items:
        item["image_url"] = uploads.image_url(uploads.ITEM_FOLDER, item["id"])
    return (
        render_template(
            "menu/index.html",
            items=items,
            pager=pager,
            filters=filters,
            categories=menu_category.options(company_id()),
            import_errors=import_errors,
        ),
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
    return _xlsx(menu_io.menu_template(), "menu_template.xlsx")


@bp.route("/export.xlsx")
@outlet_required
def export():
    return _xlsx(menu_io.menu_export(menu_item.export_all(company_id())), f"menu_{now():%Y%m%d_%H%M}.xlsx")


@bp.route("/import", methods=["POST"])
@outlet_required
def import_file():
    try:
        rows = menu_io.read_rows(request.files.get("file"), menu_io.MENU_COLUMNS)
        result = menu_item.import_rows(company_id(), rows, create_categories=request.form.get("create_categories") == "1")
    except ValidationError as exc:
        flash(exc.message, "error")
        return _render_index(exc.errors.get("rows") or [exc.message])
    extra = f", {result['categories_added']} new categories" if result["categories_added"] else ""
    flash(f"Import finished: {result['added']} added, {result['updated']} updated, {result['unchanged']} unchanged{extra}.", "success")
    return redirect(url_for("menu.index"))


def _form(item: dict | None):
    preset = to_int(request.args.get("category"))
    form = (
        {"name": "", "category_id": preset, "rate": "", "gst": "5", "description": "", "veg": "1", "active": 1}
        if item is None
        else dict(item)
    )
    errors = {}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            data = menu_item.validate(company_id(), request.form)
            if item is None:
                new_id = menu_item.create(company_id(), data)
                uploads.save_image(request.files.get("image"), uploads.ITEM_FOLDER, new_id)
                flash(f"'{data['name']}' added to the menu.", "success")
            else:
                menu_item.update(company_id(), item["id"], data)
                if request.form.get("remove_image") == "1":
                    uploads.delete_image(uploads.ITEM_FOLDER, item["id"])
                uploads.save_image(request.files.get("image"), uploads.ITEM_FOLDER, item["id"])
                flash(f"'{data['name']}' updated.", "success")
            return redirect(request.form.get("back") or url_for("menu.index"))
        except ValidationError as exc:
            errors = exc.errors or {"_": exc.message}
            flash(exc.message, "error")
    return (
        render_template(
            "menu/form.html",
            item=item,
            form=form,
            errors=errors,
            categories=menu_category.options(company_id()),
            image_url=uploads.image_url(uploads.ITEM_FOLDER, item["id"]) if item else None,
            back=request.form.get("back") or request.referrer or url_for("menu.index"),
        ),
        422 if errors else 200,
    )


@bp.route("/new", methods=["GET", "POST"])
@outlet_required
def create():
    return _form(None)


@bp.route("/<int:item_id>/edit", methods=["GET", "POST"])
@outlet_required
def edit(item_id):
    return _form(menu_item.get(company_id(), item_id))


@bp.route("/<int:item_id>/toggle", methods=["POST"])
@outlet_required
def toggle(item_id):
    state = menu_item.toggle(company_id(), item_id)
    flash("Item is now available for billing." if state else "Item hidden from billing.", "success")
    return redirect(request.referrer or url_for("menu.index"))


@bp.route("/<int:item_id>/delete", methods=["POST"])
@outlet_required
def delete(item_id):
    try:
        menu_item.delete(company_id(), item_id)
        uploads.delete_image(uploads.ITEM_FOLDER, item_id)
        flash("Menu item deleted.", "success")
    except ValidationError as exc:
        flash(exc.message, "error")
    return redirect(request.referrer or url_for("menu.index"))
