"""Table service: Table View, the table order screen (KOT / bill / settle) and table management."""
from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

from ..errors import ForbiddenError, ValidationError
from ..models import bill as bill_model
from ..models import company as company_model
from ..models import menu_item, payment, tables
from ..security import outlet_required
from ..services import uploads
from ..utils import now, to_int
from .helpers import company_id, is_outlet_admin, user_id

bp = Blueprint("tables", __name__)

STATUS_LABELS = {"blank": "Blank", "running": "Running", "kot": "KOT sent", "ready": "Food ready", "printed": "Bill printed"}


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("The request was not sent correctly. Please reload the page.")
    return data


def _require_admin():
    if not is_outlet_admin():
        raise ForbiddenError("Only an outlet admin or manager can do this.")


# --------------------------------------------------------------- table view --

@bp.route("/tables")
@outlet_required
def index():
    return render_template(
        "tables/index.html",
        areas=tables.board(company_id()),
        status_labels=STATUS_LABELS,
        can_manage=is_outlet_admin(),
    )


@bp.route("/api/tables")
@outlet_required
def board_json():
    return jsonify({"ok": True, "areas": tables.board(company_id())})


# -------------------------------------------------------------- table order --

@bp.route("/tables/<int:table_id>")
@outlet_required
def order(table_id):
    cid = company_id()
    table = tables.get_table(cid, table_id)
    categories = menu_item.billing_menu(cid)
    for cat in categories:
        cat["image"] = uploads.image_url(uploads.CATEGORY_FOLDER, cat["id"])
        for item in cat["items"]:
            item["image"] = uploads.image_url(uploads.ITEM_FOLDER, item["id"])
    return render_template(
        "tables/order.html",
        company=company_model.get(cid),
        table=table,
        all_tables=tables.list_tables(cid),
        categories=categories,
        payment_modes=payment.payment_modes(cid),
        service_modes=payment.service_modes(cid),
        state=tables.order_state(cid, table_id),
        started=now(),
        can_manage=is_outlet_admin(),
        can_bill=(g.user.get("user_type") != 4),  # waiters take orders; billing happens at the counter
    )


@bp.route("/api/tables/<int:table_id>")
@outlet_required
def state_json(table_id):
    return jsonify({"ok": True, "state": tables.order_state(company_id(), table_id)})


def _with_state(table_id, **extra):
    return jsonify({"ok": True, "state": tables.order_state(company_id(), table_id), **extra})


@bp.route("/api/tables/<int:table_id>/kot", methods=["POST"])
@outlet_required
def kot(table_id):
    data = _json_body()
    result = tables.add_kot(company_id(), table_id, user_id(), data.get("items"), bool(data.get("print")))
    ids = result["print_kot_ids"]
    print_url = url_for("tables.kot_print", ids=",".join(map(str, ids)), embed=1) if ids else None
    return _with_state(table_id, print_url=print_url)


@bp.route("/api/tables/<int:table_id>/void", methods=["POST"])
@outlet_required
def void(table_id):
    line_id = to_int(_json_body().get("line_id"))
    if not line_id:
        raise ValidationError("Choose the line to remove.")
    tables.void_line(company_id(), table_id, line_id)
    return _with_state(table_id)


@bp.route("/api/tables/<int:table_id>/bill", methods=["POST"])
@outlet_required
def bill(table_id):
    data = _json_body()
    if data.get("items"):  # unsent items in the cart are saved first, then billed
        tables.add_kot(company_id(), table_id, user_id(), data.get("items"), print_kot=bool(data.get("print_kot", True)))
    result = tables.print_bill(company_id(), table_id, user_id(), data)
    receipt = url_for("bills.show", bill_id=result["bill_id"], embed=1)
    return _with_state(table_id, bill=result, print_url=receipt)


@bp.route("/api/tables/<int:table_id>/settle", methods=["POST"])
@outlet_required
def settle(table_id):
    result = tables.settle(company_id(), table_id, _json_body().get("payment_mode_id"), user_id())
    return _with_state(table_id, bill=result)


@bp.route("/api/tables/<int:table_id>/part-payment", methods=["POST"])
@outlet_required
def part_payment_add(table_id):
    tables.add_part_payment(company_id(), table_id, _json_body(), user_id())
    return _with_state(table_id)


@bp.route("/api/tables/<int:table_id>/part-payment/remove", methods=["POST"])
@outlet_required
def part_payment_remove(table_id):
    tables.remove_part_payment(company_id(), table_id, _json_body().get("payment_id"))
    return _with_state(table_id)


@bp.route("/api/tables/<int:table_id>/move", methods=["POST"])
@outlet_required
def move(table_id):
    target = to_int(_json_body().get("to_table_id"))
    if not target:
        raise ValidationError("Choose the table to move to.")
    name = tables.move(company_id(), table_id, target)
    return jsonify({"ok": True, "moved_to": target, "moved_to_name": name, "url": url_for("tables.order", table_id=target)})


@bp.route("/api/tables/<int:table_id>/cancel", methods=["POST"])
@outlet_required
def cancel(table_id):
    _require_admin()
    tables.cancel(company_id(), table_id)
    return _with_state(table_id)


@bp.route("/tables/kot")
@outlet_required
def kot_print():
    ids = [i for i in (to_int(x) for x in (request.args.get("ids") or "").split(",")) if i]
    if not ids:
        raise ValidationError("No KOT selected.")
    return render_template(
        "tables/kot.html",
        kots=tables.kots_for_print(company_id(), ids[:50]),
        company=company_model.get(company_id()),
        embedded=request.args.get("embed") == "1",
    )


# ------------------------------------------------------------------ kitchen --

@bp.route("/kitchen")
@outlet_required
def kitchen():
    return render_template("tables/kitchen.html", board=tables.kitchen_board(company_id()))


@bp.route("/api/kitchen")
@outlet_required
def kitchen_json():
    return jsonify({"ok": True, "board": tables.kitchen_board(company_id())})


@bp.route("/api/kitchen/<int:kot_id>/ready", methods=["POST"])
@outlet_required
def kitchen_ready(kot_id):
    tables.set_kot_ready(company_id(), kot_id, bool(_json_body().get("ready", True)))
    return jsonify({"ok": True, "board": tables.kitchen_board(company_id())})


# ------------------------------------------------------------------- manage --

@bp.route("/tables/manage", methods=["GET", "POST"])
@outlet_required
def manage():
    _require_admin()
    cid = company_id()
    errors, form = {}, {}
    if request.method == "POST":
        form = request.form.to_dict()
        try:
            added = tables.create_tables(cid, form.get("area"), form.get("names"))
            flash(f"Added {len(added)} table(s): {', '.join(added[:12])}{' …' if len(added) > 12 else ''}.", "success")
            return redirect(url_for("tables.manage"))
        except ValidationError as exc:
            errors = exc.errors or {"names": exc.message}
            flash(exc.message, "error")
    all_tables = tables.list_tables(cid, include_inactive=True)
    areas = sorted({t["area"] for t in all_tables}) or [tables.DEFAULT_AREA]
    return render_template("tables/manage.html", tables=all_tables, areas=areas, form=form, errors=errors), (422 if errors else 200)


@bp.route("/tables/manage/<int:table_id>", methods=["POST"])
@outlet_required
def manage_update(table_id):
    _require_admin()
    try:
        tables.update_table(company_id(), table_id, request.form.get("name"), request.form.get("area"),
                            request.form.get("active") == "1")
        flash("Table saved.", "success")
    except ValidationError as exc:
        flash(exc.message, "error")
    return redirect(url_for("tables.manage"))


@bp.route("/tables/manage/<int:table_id>/delete", methods=["POST"])
@outlet_required
def manage_delete(table_id):
    _require_admin()
    try:
        tables.delete_table(company_id(), table_id)
        flash("Table deleted.", "success")
    except ValidationError as exc:
        flash(exc.message, "error")
    return redirect(url_for("tables.manage"))
