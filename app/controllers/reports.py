from flask import Blueprint, render_template, request, send_file

from ..models import company as company_model
from .. import date_ranges
from ..models import report
from ..security import outlet_required
from ..services import excel
from ..utils import now, parse_date, today
from .helpers import company_id

bp = Blueprint("reports", __name__, url_prefix="/reports")
MAX_DAYS = 3 * 366
INVENTORY_REPORTS = {"ingredient_stock", "item_consumption"}


def can_view(kind: str) -> bool:
    from ..roles import allowed
    if kind == "online_orders":
        return allowed("online")
    return kind not in INVENTORY_REPORTS or allowed("inventory")


def _params():
    kind = request.args.get("type", "daily")
    if not can_view(kind):
        kind = "daily"
    # Every report opens on the current quarter; the quarterly / yearly comparison opens on 3 financial years.
    t = today()
    default = (report.default_start(kind, t), t) if kind == "period_sales" else "this_quarter"
    start, end, active, note = date_ranges.resolve(request.args, default)
    if (end - start).days > MAX_DAYS:
        start = end.replace(year=end.year - 3)
        active = "custom"
        note = "Reports cover at most 3 years; the start date was moved."
    return kind, start, end, note, active


@bp.route("/")
@outlet_required
def index():
    kind, start, end, note, active = _params()
    return render_template(
        "reports/index.html",
        report=report.build(kind, company_id(), start, end, request.args.get("source", ""), request.args.get("period", "")),
        sources=report.SOURCES,
        periods=report.PERIODS,
        reports={k: v for k, v in report.REPORTS.items() if can_view(k)},
        start=start,
        end=end,
        note=note,
        active_range=active,
        range_labels=date_ranges.LABELS,
        today_iso=today().isoformat(),
    )


@bp.route("/export")
@outlet_required
def export():
    kind, start, end, _note, _active = _params()
    data = report.build(kind, company_id(), start, end, request.args.get("source", ""), request.args.get("period", ""))
    company = company_model.get(company_id())
    title = f"{company['name']} - {data['title']} ({start:%d-%m-%Y} to {end:%d-%m-%Y})"
    return send_file(
        excel.report_workbook(data, title),
        as_attachment=True,
        download_name=f"{data['kind']}_report_{start:%Y%m%d}_{end:%Y%m%d}_{now():%H%M}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
