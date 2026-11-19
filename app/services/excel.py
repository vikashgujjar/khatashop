"""Builds the bill list as an .xlsx workbook."""
from __future__ import annotations
import re
from io import BytesIO

from ..core.xlsx import Alignment, Font, PatternFill, Workbook, get_column_letter

HEADERS = [
    "Sr No", "Bill Number", "Billing Date", "Order Type", "Customer", "Mobile", "Total Quantity", "CGST", "SGST",
    "Service", "Total", "Payment Mode", "Counter", "Session", "Quarter", "Items",
]


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def bills_workbook(bills: list[dict], title: str) -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Bills"
    ws.append([title])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(HEADERS)
    header_fill = PatternFill("solid", fgColor="1A6B3A")
    for cell in ws[2]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center")

    for n, bill in enumerate(bills, start=1):
        items = ", ".join(f"{i['item_name']} Q[{i['quantity']}]" for i in bill.get("items", []))
        ws.append([
            n,
            bill["bill_no"],
            bill["billing_date"],
            bill.get("order_type_label") or "Dine-in",
            bill.get("customer_name") or "",
            bill.get("customer_mb_no") or "",
            int(_num(bill["total_quantity"])),
            _num(bill["cgst"]),
            _num(bill["sgst"]),
            _num(bill["service_cost"]),
            _num(bill["total"]),
            bill.get("payment_mode") or "",
            bill.get("counter_name") or "",
            bill.get("financial_session") or "",
            bill.get("financial_quarter") or "",
            items,
        ])
    last = ws.max_row
    if bills:
        ws.append([])
        ws.append(["", "Total", "", "", "", "", f"=SUM(G3:G{last})", f"=SUM(H3:H{last})", f"=SUM(I3:I{last})",
                   f"=SUM(J3:J{last})", f"=SUM(K3:K{last})"])
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)

    for col in "HIJK":
        for cell in ws[col][2:]:
            cell.number_format = "#,##0.00"
    for cell in ws["C"][2:]:
        cell.number_format = "dd-mm-yyyy"
    widths = [7, 12, 12, 11, 20, 13, 9, 10, 10, 10, 12, 14, 18, 9, 9, 60]
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A3"

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def _text(value):
    """Text that starts with '=' would be stored as an Excel formula; keep it as plain text."""
    if isinstance(value, str) and value.startswith("="):
        return "'" + value
    return value


def report_workbook(report: dict, title: str) -> BytesIO:
    """Any report from models.report as a formatted sheet with a totals row."""
    wb = Workbook()
    ws = wb.active
    ws.title = re.sub(r"[\\/*?:\[\]]", "-", report["title"])[:31] or "Report"  # Excel forbids \ / * ? : [ ]
    ws.append([title])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([label for _k, label, _t in report["columns"]])
    fill = PatternFill("solid", fgColor="1A6B3A")
    for cell in ws[2]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
    for row in report["rows"]:
        ws.append([_num(row[k]) if kind != "text" else _text(row[k]) for k, _l, kind in report["columns"]])
    if report["rows"] and not report.get("no_totals"):
        ws.append([("Total" if i == 0 else (_num(report["totals"][k]) if k in report["totals"] else ""))
                   for i, (k, _l, _kind) in enumerate(report["columns"])])
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)
    for i, (_k, label, kind) in enumerate(report["columns"], start=1):
        letter = get_column_letter(i)
        ws.column_dimensions[letter].width = max(12, min(45, len(label) + 6)) if kind != "text" else 32
        if kind == "money":
            for cell in ws[letter][2:]:
                cell.number_format = "#,##0.00"
    ws.freeze_panes = "A3"
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer
