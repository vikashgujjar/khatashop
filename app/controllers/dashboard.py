from __future__ import annotations
from datetime import timedelta

from ..core import Blueprint, redirect, render_template, request, url_for

from ..models import company as company_model
from ..models import dashboard
from ..security import outlet_required
from ..services import uploads
from ..utils import now, today
from .helpers import company_id

bp = Blueprint("dashboard", __name__)


def _greeting(hour: int) -> str:
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


@bp.route("/")
def home():
    return redirect(url_for("dashboard.index"))


@bp.route("/dashboard")
@outlet_required
def index():
    cid = company_id()
    key, start, end = dashboard.resolve_range(request.args.get("range", "today"), today())
    span = (end - start).days + 1
    prev_start, prev_end = start - timedelta(days=span), start - timedelta(days=1)

    current = dashboard.summary(cid, start, end)
    previous = dashboard.summary(cid, prev_start, prev_end)
    series = dashboard.sales_series(cid, start, end)
    return render_template(
        "dashboard/index.html",
        company=company_model.get(cid),
        ranges=dashboard.RANGES,
        range_key=key,
        start=start,
        end=end,
        stats=current,
        sales_change=dashboard.change_percent(current["sales"], previous["sales"]),
        bills_change=dashboard.change_percent(current["bills"], previous["bills"]),
        payments=dashboard.by_payment_mode(cid, start, end),
        series=series,
        series_unit="day" if (end - start).days <= 62 else "month",
        greeting=_greeting(now().hour),
        series_max=max([p["amount"] for p in series] + [0]),
        top_items=dashboard.top_items(cid, start, end, current["min_id"], current["max_id"]),
        recent=dashboard.recent_bills(cid),
        counts=dashboard.menu_counts(cid),
        menu_pdf=uploads.menu_pdf_info(cid),
    )
