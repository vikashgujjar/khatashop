from __future__ import annotations
from ..core import Blueprint, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from ..errors import ForbiddenError, ValidationError
from ..models import bill as bill_model
from ..models import company as company_model
from ..models import menu_item, payment
from ..security import outlet_required
from ..services import excel, uploads
from ..utils import now
from .helpers import company_id, page_arg, per_page, user_id

bp = Blueprint("bills", __name__)

FILTER_KEYS = ("q", "session", "quarter", "order_type", "payment_mode_id", "date_from", "date_to")


def _filters() -> dict:
    return {k: (request.args.get(k) or "").strip() for k in FILTER_KEYS}


@bp.route("/bills")
@outlet_required
def index():
    filters = _filters()
    bills, pager, summary = bill_model.paginate(company_id(), filters, page_arg(), per_page())
    return render_template(
        "bills/index.html",
        bills=bills,
        pager=pager,
        summary=summary,
        filters=filters,
        sessions=bill_model.sessions(company_id()),
        quarters=bill_model.QUARTERS,
        order_types=bill_model.ORDER_TYPE_LABELS,
        payment_modes=payment.payment_modes(company_id()),
    )


@bp.route("/bills/export")
@outlet_required
def export():
    filters = _filters()
    bills = bill_model.export_rows(company_id(), filters)
    company = company_model.get(company_id())
    stamp = now().strftime("%Y%m%d_%H%M")
    buffer = excel.bills_workbook(bills, f"{company['name']} - Bill Detail (exported {now():%d-%m-%Y %H:%M})")
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"bill_detail_{stamp}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@bp.route("/bills/new")
@outlet_required
def create():
    cid = company_id()
    categories = menu_item.billing_menu(cid)
    for cat in categories:
        cat["image"] = uploads.image_url(uploads.CATEGORY_FOLDER, cat["id"])
        for item in cat["items"]:
            item["image"] = uploads.image_url(uploads.ITEM_FOLDER, item["id"])
    return render_template(
        "bills/create.html",
        company=company_model.get(cid),
        categories=categories,
        payment_modes=payment.payment_modes(cid),
        service_modes=payment.service_modes(cid),
        next_bill_no=bill_model.preview_bill_no(cid),
        started=now(),
        can_manage_payments=can_manage_payments(),
        initial_order=request.args.get("order") if request.args.get("order") in bill_model.ORDER_TYPES else "dine_in",
    )


ADMIN_TYPES = {1, 7, 8}  # Admin, Manager, Coordinator


def can_manage_payments() -> bool:
    user = g.user
    return user.get("role") == "super" or user.get("user_type") in ADMIN_TYPES


@bp.route("/bills/payment-options", methods=["POST"])
@outlet_required
def setup_payment_options():
    if not can_manage_payments():
        raise ForbiddenError("Only an outlet admin or manager can set up payment options.")
    added = payment.add_defaults(company_id())
    flash(f"Payment options added: {', '.join(added)}." if added else "Cash, Card and UPI were already set up.", "success")
    return redirect(url_for("bills.create"))


@bp.route("/api/bills", methods=["POST"])
@outlet_required
def store():
    payload = request.get_json(silent=True)
    if payload is None:
        raise ValidationError("The bill data was not sent correctly. Please reload the page.")
    result = bill_model.create(company_id(), user_id(), payload)
    result["receipt_url"] = url_for("bills.show", bill_id=result["bill_id"], print=1)
    result["next_bill_no"] = bill_model.preview_bill_no(company_id())
    return jsonify({"ok": True, **result}), 201


@bp.route("/bills/<int:bill_id>")
@outlet_required
def show(bill_id):
    cid = company_id()
    return render_template(
        "bills/receipt.html",
        bill=bill_model.get(cid, bill_id),
        company=company_model.get(cid),
        auto_print=request.args.get("print") == "1",
        embedded=request.args.get("embed") == "1",
    )
