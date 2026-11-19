"""Procurement / Issuing / Consumption notes and Consumption Formula (recipes)."""
from __future__ import annotations
from ..core import Blueprint, flash, redirect, render_template, request, url_for

from ..errors import ValidationError
from ..models import company as company_model
from ..models import inventory
from ..security import outlet_required
from .helpers import company_id, page_arg, per_page, user_id

bp = Blueprint("notes", __name__, url_prefix="/inventory")
formula_bp = Blueprint("formula", __name__, url_prefix="/consumption-formula")


@bp.route("/<resource>/")
@outlet_required
def index(resource):
    nt = inventory.note_type(resource)
    filters = {k: (request.args.get(k) or "").strip() for k in ("q", "date_from", "date_to")}
    rows, pager = inventory.paginate(nt, company_id(), filters, page_arg(), per_page())
    return render_template("notes/index.html", nt=nt, rows=rows, pager=pager, filters=filters, payment=inventory.PAYMENT)


def _form(nt, note):
    errors, form, lines = {}, {}, (note or {}).get("lines", [])
    if request.method == "POST":
        form = request.form.to_dict()
        lines = [{"ingredient_id": i, "quantity": q, "rate": r, "ingredient_gst": g} for i, q, r, g in
                 zip(request.form.getlist("ingredient_id"), request.form.getlist("quantity"),
                     request.form.getlist("rate") or [""] * 999, request.form.getlist("gst") or [""] * 999)]
        try:
            saved = inventory.save(nt, company_id(), user_id(), request.form, note["id"] if note else None)
            flash(f"{nt.title} note {saved['ref']} saved.", "success")
            return redirect(url_for("notes.index", resource=nt.key))
        except ValidationError as exc:
            errors = exc.errors
            flash(exc.message, "error")
    elif note:
        form = {"note_date": note["note_date"].isoformat() if note["note_date"] else "", "vendor_id": note.get("vendor_id"),
                "invoice_no": note.get("invoice_no"), "payment_status": note.get("payment_status")}
    return render_template("notes/form.html", nt=nt, note=note, form=form, lines=lines, errors=errors,
                           ingredients=inventory.ingredients(company_id()), vendors=inventory.vendors(company_id()) if nt.priced else []), (422 if errors else 200)


@bp.route("/<resource>/new", methods=["GET", "POST"])
@outlet_required
def create(resource):
    return _form(inventory.note_type(resource), None)


@bp.route("/<resource>/<int:note_id>", methods=["GET", "POST"])
@outlet_required
def edit(resource, note_id):
    nt = inventory.note_type(resource)
    return _form(nt, inventory.get(nt, company_id(), note_id))


@bp.route("/<resource>/<int:note_id>/print")
@outlet_required
def print_note(resource, note_id):
    nt = inventory.note_type(resource)
    return render_template("notes/print.html", nt=nt, note=inventory.get(nt, company_id(), note_id),
                           company=company_model.get(company_id()), payment=inventory.PAYMENT)


@bp.route("/<resource>/<int:note_id>/delete", methods=["POST"])
@outlet_required
def delete(resource, note_id):
    nt = inventory.note_type(resource)
    inventory.delete(nt, company_id(), note_id)
    flash(f"{nt.title} note deleted.", "success")
    return redirect(url_for("notes.index", resource=resource))


# --------------------------------------------------------------- formulas --

@formula_bp.route("/")
@outlet_required
def index():
    q = (request.args.get("q") or "").strip()
    return render_template("notes/formula_index.html", items=inventory.formula_items(company_id(), q), q=q)


@formula_bp.route("/<int:item_id>", methods=["GET", "POST"])
@outlet_required
def edit(item_id):
    item = inventory.formula(company_id(), item_id)
    errors, lines = {}, item["lines"]
    if request.method == "POST":
        lines = [{"ingredient_id": i, "quantity": q} for i, q in zip(request.form.getlist("ingredient_id"), request.form.getlist("quantity"))]
        try:
            n = inventory.save_formula(company_id(), item_id, request.form)
            flash(f"Recipe for {item['name']} saved ({n} ingredient{'s' if n != 1 else ''}).", "success")
            return redirect(url_for("formula.index"))
        except ValidationError as exc:
            errors = exc.errors
            flash(exc.message, "error")
    return render_template("notes/formula_form.html", item=item, lines=lines, errors=errors,
                           ingredients=inventory.ingredients(company_id())), (422 if errors else 200)
