"""Online orders from the customer app: Order Detail cards, status / driver updates, print, Item Summary."""
from datetime import timedelta

from flask import Blueprint, jsonify, render_template, request

from ..errors import ValidationError
from ..models import company as company_model
from ..models import online_orders as orders
from ..security import outlet_required
from ..utils import parse_date, today
from .helpers import company_id

bp = Blueprint("online", __name__, url_prefix="/online-orders")


def _range():
    end = parse_date(request.args.get("date_to")) or today()
    start = parse_date(request.args.get("date_from")) or (end - timedelta(days=1))
    if start > end:
        start, end = end, start
    if (end - start).days > 92:
        start = end - timedelta(days=92)
    return start, end


@bp.route("/")
@outlet_required
def index():
    start, end = _range()
    status = request.args.get("status", "")
    q = (request.args.get("q") or "").strip()
    data = orders.list_orders(company_id(), start, end, status if status in orders.STATUSES else "", q)
    return render_template("online/index.html", data=data, start=start, end=end, status=status, q=q,
                           statuses=orders.STATUSES, settable=orders.SETTABLE, drivers=orders.drivers(company_id()))


@bp.route("/<int:order_id>/update", methods=["POST"])
@outlet_required
def update(order_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("The request was not sent correctly. Please reload the page.")
    o = orders.update(company_id(), order_id, body.get("status"), body.get("driver_id"),
                      body.get("confirmed") if "confirmed" in body else None, body.get("ready") if "ready" in body else None)
    return jsonify({"ok": True, "order": {"id": o["id"], "status_key": o["status_key"], "status_label": o["status_label"],
                                          "status_time": o["status_time"], "isReady": o["isReady"], "is_confirmed": o["is_confirmed"],
                                          "driver_name": o["driver_name"]}})


@bp.route("/<int:order_id>/print")
@outlet_required
def print_order(order_id):
    return render_template("online/print.html", o=orders.get(company_id(), order_id), company=company_model.get(company_id()),
                           auto_print=request.args.get("print") == "1")


@bp.route("/item-summary")
@outlet_required
def summary():
    start, end = _range()
    if not request.args.get("date_from"):
        start = end.replace(day=1)
    return render_template("online/summary.html", rows=orders.item_summary(company_id(), start, end), start=start, end=end,
                           vendor=orders.vendor_for(company_id()))
