from flask import Blueprint, render_template, request, send_file

from ..models import company as company_model
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
    end = parse_date(request.args.get("date_to")) or today()
    start = parse_date(request.args.get("date_from")) or end.replace(day=1)
    note = None
    if start > end:
        start, end = end, start
        note = "The dates were the wrong way round, so they were swapped."
    if (end - start).days > MAX_DAYS:
        start = end.replace(year=end.year - 3)
        note = "Reports cover at most 3 years; the start date was moved."
    return kind, start, end, note


@bp.route("/")
@outlet_required
def index():
    kind, start, end, note = _params()
    return render_template(
        "reports/index.html",
        report=report.build(kind, company_id(), start, end, request.args.get("source", "")),
        sources=report.SOURCES,
        reports={k: v for k, v in report.REPORTS.items() if can_view(k)},
        start=start,
        end=end,
        note=note,
    )


@bp.route("/export")
@outlet_required
def export():
    kind, start, end, _note = _params()
    data = report.build(kind, company_id(), start, end, request.args.get("source", ""))
    company = company_model.get(company_id())
    title = f"{company['name']} - {data['title']} ({start:%d-%m-%Y} to {end:%d-%m-%Y})"
    return send_file(
        excel.report_workbook(data, title),
        as_attachment=True,
        download_name=f"{data['kind']}_report_{start:%Y%m%d}_{end:%Y%m%d}_{now():%H%M}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
