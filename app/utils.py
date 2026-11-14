"""Small shared helpers: dates, money, pagination, form parsing."""
import math
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from flask import current_app

TWO_PLACES = Decimal("0.01")


def now() -> datetime:
    return datetime.now(ZoneInfo(current_app.config["KS"]["app"]["timezone"])).replace(tzinfo=None)


def today() -> date:
    return now().date()


def to_decimal(value, default: Decimal | None = None) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return default
    if not result.is_finite():
        return default
    return result


def round2(value) -> Decimal:
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def money(value) -> str:
    """Indian-style grouping: 1,23,456.78"""
    try:
        amount = round2(value or 0)
    except (InvalidOperation, ValueError):
        return str(value)
    sign = "-" if amount < 0 else ""
    whole, frac = f"{abs(amount):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}{whole}.{frac}"


def to_int(value, default: int | None = None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def financial_period(day: date) -> tuple[int, str]:
    """Quarter and session the way the existing PHP app stores them.

    Quarters follow the Indian financial year (Q1 = Apr-Jun ... Q4 = Jan-Mar).
    The PHP app stores the *calendar* year as the session, so Jan-Mar bills
    are kept under the current year too; we match that so old and new bills
    filter together.
    """
    month = day.month
    if month <= 3:
        quarter = "Q4"
    elif month <= 6:
        quarter = "Q1"
    elif month <= 9:
        quarter = "Q2"
    else:
        quarter = "Q3"
    return day.year, quarter


class Pagination:
    def __init__(self, page: int, per_page: int, total: int):
        self.per_page = max(1, per_page)
        self.total = max(0, total)
        self.pages = max(1, math.ceil(self.total / self.per_page))
        self.page = min(max(1, page), self.pages)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page

    @property
    def first_item(self) -> int:
        return 0 if self.total == 0 else self.offset + 1

    @property
    def last_item(self) -> int:
        return min(self.offset + self.per_page, self.total)

    def window(self, size: int = 2):
        """Page numbers to show, with None for gaps: 1 … 4 5 [6] 7 8 … 20"""
        pages = []
        for p in range(1, self.pages + 1):
            if p in (1, self.pages) or abs(p - self.page) <= size:
                pages.append(p)
            elif pages and pages[-1] is not None:
                pages.append(None)
        return pages
