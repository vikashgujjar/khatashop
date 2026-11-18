"""Quick date ranges for report pages: Today ... This quarter, Last quarter ... or custom dates.

Quarters and years follow the Indian financial year (Q1 = Apr-Jun, Q4 = Jan-Mar).
A page reads `?range=<key>` for a quick range, or `date_from` / `date_to` for custom dates.
When custom dates happen to equal a quick range, that range is shown as selected, so
other filters on the page (source, period...) keep the quick range highlighted.
"""
from datetime import date, timedelta

from .utils import parse_date, today

LABELS = {
    "today": "Today",
    "yesterday": "Yesterday",
    "this_month": "This month",
    "last_month": "Last month",
    "this_quarter": "This quarter",
    "last_quarter": "Last quarter",
    "this_fy": "This FY",
    "last_fy": "Last FY",
}


def _quarter_start(d: date) -> date:
    # Financial quarters start in Apr, Jul, Oct, Jan.
    month = ((d.month - 4) % 12) // 3 * 3 + 4
    month = month - 12 if month > 12 else month
    return date(d.year, month, 1)


def _fy_start(d: date) -> date:
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def presets(on: date | None = None) -> dict[str, tuple[date, date]]:
    t = on or today()
    month_start = t.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    q_start = _quarter_start(t)
    last_q_start = _quarter_start(q_start - timedelta(days=1))
    fy = _fy_start(t)
    return {
        "today": (t, t),
        "yesterday": (t - timedelta(days=1), t - timedelta(days=1)),
        "this_month": (month_start, t),
        "last_month": (last_month_end.replace(day=1), last_month_end),
        "this_quarter": (q_start, t),
        "last_quarter": (last_q_start, q_start - timedelta(days=1)),
        "this_fy": (fy, t),
        "last_fy": (fy.replace(year=fy.year - 1), fy - timedelta(days=1)),
    }


def resolve(args, default: str | tuple[date, date] = "this_quarter", max_days: int | None = None):
    """Returns (start, end, active_key, note). active_key is a LABELS key or "custom"."""
    ranges = presets()
    note = None
    key = args.get("range")
    if key in ranges:
        start, end = ranges[key]
    elif args.get("date_from") or args.get("date_to"):
        end = parse_date(args.get("date_to")) or today()
        start = parse_date(args.get("date_from")) or end.replace(day=1)
        if start > end:
            start, end = end, start
            note = "The dates were the wrong way round, so they were swapped."
    elif isinstance(default, tuple):
        start, end = default
    else:
        start, end = ranges[default]
    if max_days and (end - start).days > max_days:
        start = end - timedelta(days=max_days)
        note = f"This page covers at most {max_days + 1} days; the start date was moved."
    active = next((k for k, (s, e) in ranges.items() if (s, e) == (start, end)), "custom")
    return start, end, active, note
