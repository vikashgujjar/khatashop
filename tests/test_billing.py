"""Unit tests for bill maths. Run: .venv\\Scripts\\python -m unittest discover tests"""
from __future__ import annotations
import sys
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.billing import BillLine, calculate, next_bill_number  # noqa: E402
from app.utils import financial_period, money  # noqa: E402


def line(rate, qty, gst=5, item_id=1):
    return BillLine(item_id=item_id, name="X", rate=D(str(rate)), quantity=qty, gst_percent=D(str(gst)))


class TotalsMatchExistingBills(unittest.TestCase):
    """Figures taken from real bills on the Bill Detail screen."""

    def test_op15(self):  # MILK SHAKES x1, MILK SHAKES x1, PLAIN TEA x7 -> 114.46, CGST 2.73
        t = calculate([line(2, 1, item_id=1), line(2, 1, item_id=2), line(15, 7, item_id=3)])
        self.assertEqual(t.subtotal, D("109.00"))
        self.assertEqual(t.cgst, D("2.73"))
        self.assertEqual(t.sgst, D("2.73"))
        self.assertEqual(t.grand_total - t.round_off, D("114.46"))  # same as the old bill before rounding
        self.assertEqual((t.grand_total, t.round_off), (D("114.00"), D("-0.46")))
        self.assertEqual(t.total_quantity, 9)

    def test_op14(self):  # 2 milk shakes -> 4.20, CGST 0.10
        t = calculate([line(2, 1, item_id=1), line(2, 1, item_id=2)])
        self.assertEqual((t.grand_total - t.round_off, t.cgst), (D("4.20"), D("0.10")))
        self.assertEqual(t.grand_total, D("4.00"))

    def test_op13(self):  # 1 plain tea -> 15.76, CGST 0.38
        t = calculate([line(15, 1)])
        self.assertEqual((t.grand_total - t.round_off, t.cgst), (D("15.76"), D("0.38")))
        self.assertEqual(t.grand_total, D("16.00"))


class RoundOff(unittest.TestCase):
    def test_rounds_down_below_half(self):  # 289 + 14.46 GST = 303.46 -> 303
        t = calculate([line(40, 1), line(249, 1)])
        self.assertEqual((t.grand_total, t.round_off), (D("303.00"), D("-0.46")))

    def test_half_rounds_up(self):  # 10 + 0.50 GST = 10.50 -> 11
        t = calculate([line(10, 1)])
        self.assertEqual((t.grand_total, t.round_off), (D("11.00"), D("0.50")))

    def test_whole_amount_has_no_round_off(self):
        t = calculate([line(20, 1)])  # 21.00
        self.assertEqual((t.grand_total, t.round_off), (D("21.00"), D("0.00")))


class Charges(unittest.TestCase):
    def test_service_rounds_to_whole_rupees(self):
        t = calculate([line(99, 1, gst=0)], service_percent=D("10"))
        self.assertEqual(t.service_cost, D("10.00"))  # 9.9 -> 10
        self.assertEqual(t.grand_total, D("109.00"))

    def test_container_percent_and_amount(self):
        self.assertEqual(calculate([line(200, 1, gst=0)], container_mode="percent", container_value=D("10")).container_charges, D("20.00"))
        self.assertEqual(calculate([line(200, 1, gst=0)], container_mode="amount", container_value=D("15")).grand_total, D("215.00"))

    def test_no_container_on_empty_bill(self):
        self.assertEqual(calculate([], container_mode="amount", container_value=D("20")).grand_total, D("0.00"))

    def test_zero_gst(self):
        t = calculate([line(50, 3, gst=0)])
        self.assertEqual((t.cgst, t.grand_total), (D("0.00"), D("150.00")))


class BillNumbers(unittest.TestCase):
    def test_sequence(self):
        self.assertEqual(next_bill_number("Op15", "Op"), "Op16")
        self.assertEqual(next_bill_number("Op09", "Op"), "Op10")
        self.assertEqual(next_bill_number("Sd24163", "Sd"), "Sd24164")

    def test_first_bill(self):
        self.assertEqual(next_bill_number(None, "Op"), "Op01")
        self.assertEqual(next_bill_number("", "Op"), "Op01")


class Periods(unittest.TestCase):
    def test_quarters(self):
        self.assertEqual(financial_period(date(2026, 11, 14)), (2026, "Q3"))
        self.assertEqual(financial_period(date(2025, 7, 8)), (2025, "Q2"))
        self.assertEqual(financial_period(date(2026, 4, 1)), (2026, "Q1"))
        self.assertEqual(financial_period(date(2027, 2, 1)), (2027, "Q4"))  # same as the PHP app


class Formatting(unittest.TestCase):
    def test_indian_grouping(self):
        self.assertEqual(money(D("1234567.8")), "12,34,567.80")
        self.assertEqual(money(999), "999.00")
        self.assertEqual(money(None), "0.00")


if __name__ == "__main__":
    unittest.main()
