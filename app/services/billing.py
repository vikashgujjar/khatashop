"""Bill total calculation, independent of the web layer and the database.

Matches the Angular billing screen:
  * GST is charged on top of the menu price, per line: round2(line total x GST%)
  * CGST = SGST = round2(total GST / 2)
  * Service charge = subtotal x service-mode % rounded to whole rupees
  * Container charge = % of subtotal or a fixed amount (optional)
  * Grand total = subtotal + CGST + SGST + service + container, rounded to whole rupees
    (half up); the difference is the round-off shown on the bill
"""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from ..utils import round2

ZERO = Decimal("0")
MAX_LINES = 200
MAX_QUANTITY = 999


@dataclass
class BillLine:
    item_id: int
    name: str
    rate: Decimal
    quantity: int
    gst_percent: Decimal
    total: Decimal = field(init=False)
    gst_amount: Decimal = field(init=False)

    def __post_init__(self):
        self.rate = round2(self.rate)
        self.gst_percent = Decimal(str(self.gst_percent))
        self.total = round2(self.rate * self.quantity)
        self.gst_amount = round2(self.total * self.gst_percent / 100)


@dataclass
class BillTotals:
    lines: list[BillLine]
    subtotal: Decimal
    cgst: Decimal
    sgst: Decimal
    gst_total: Decimal
    service_cost: Decimal
    container_charges: Decimal
    grand_total: Decimal
    total_quantity: int
    round_off: Decimal = ZERO


def container_amount(subtotal: Decimal, mode: str | None, value: Decimal) -> Decimal:
    if not mode or subtotal <= 0:
        return ZERO
    value = max(ZERO, Decimal(str(value)))
    if mode == "percent":
        return round2(subtotal * value / 100)
    if mode == "amount":
        return round2(value)
    raise ValueError(f"Unknown container charge mode: {mode}")


def calculate(lines: list[BillLine], service_percent=ZERO, container_mode: str | None = None, container_value=ZERO) -> BillTotals:
    subtotal = round2(sum((line.total for line in lines), ZERO))
    gst_sum = sum((line.gst_amount for line in lines), ZERO)
    half = round2(gst_sum / 2)
    service = (subtotal * Decimal(str(service_percent)) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    container = container_amount(subtotal, container_mode, Decimal(str(container_value)))
    exact = round2(subtotal + half + half + service + container)
    grand = exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP).quantize(Decimal("0.01"))
    return BillTotals(
        lines=lines,
        subtotal=subtotal,
        cgst=half,
        sgst=half,
        gst_total=half + half,
        service_cost=round2(service),
        container_charges=container,
        grand_total=grand,
        total_quantity=sum(line.quantity for line in lines),
        round_off=grand - exact,
    )


def next_bill_number(last_bill_no: str | None, abbreviation: str) -> str:
    """Same scheme as the PHP app: abbreviation + (last number + 1), padded to 2 digits."""
    number = 1
    if last_bill_no:
        digits = ""
        for ch in reversed(last_bill_no.strip()):
            if not ch.isdigit():
                break
            digits = ch + digits
        if digits:
            number = int(digits) + 1
    return f"{abbreviation}{number:02d}"
