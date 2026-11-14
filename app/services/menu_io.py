"""Excel/CSV import and export for menu categories and menu items.

Reading is lenient about header spelling ("Item Name", "item name *", "ITEM_NAME"
all work). Writing produces files in exactly the format the importer reads, so
Export -> edit in Excel -> Import is a safe round trip.
"""
import csv
import io
import re
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from ..errors import UploadError

MAX_ROWS = 5000
HEADER_FILL = PatternFill("solid", fgColor="1A6B3A")
REQUIRED_FILL = PatternFill("solid", fgColor="D61F74")

CATEGORY_COLUMNS = [
    # key, header, required, width, aliases
    ("name", "Category Name", True, 34, ["category", "categoryname", "menucategory", "menucategoryname", "name"]),
    ("sort_order", "Display Order", False, 14, ["order", "displayorder", "sortorder", "sequence"]),
    ("status", "Status", False, 12, ["status", "active", "showhide", "visible"]),
]

MENU_COLUMNS = [
    ("item_id", "Item ID", False, 10, ["itemid", "id"]),
    ("name", "Item Name", True, 38, ["itemname", "item", "name", "menuitem"]),
    ("category", "Category", True, 28, ["category", "categoryname", "menucategory", "menucategoryname"]),
    ("rate", "Price", True, 11, ["price", "rate", "amount", "mrp"]),
    ("gst", "GST %", True, 9, ["gst", "gstpercent", "itemgst", "tax", "gstrate"]),
    ("veg", "Veg", False, 11, ["veg", "vegnonveg", "type", "foodtype"]),
    ("status", "Status", False, 10, ["status", "active", "showhide"]),
    ("description", "Description", False, 50, ["description", "itemdescription", "details"]),
]


def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


# ------------------------------------------------------------------ reading --

def _raw_rows(file_storage) -> list[list]:
    if not file_storage or not file_storage.filename:
        raise UploadError("Choose an Excel (.xlsx) or CSV file to import.", errors={"file": "No file selected."})
    ext = Path(file_storage.filename).suffix.lower()
    data = file_storage.read()
    if not data:
        raise UploadError("The file is empty.")
    if ext == ".xlsx":
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:  # noqa: BLE001 - openpyxl raises many different types for bad files
            raise UploadError("The file could not be read as an Excel workbook. Save it as .xlsx and try again.") from exc
        ws = wb.worksheets[0]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        wb.close()
        return rows
    if ext == ".csv":
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                return list(csv.reader(io.StringIO(data.decode(encoding))))
            except UnicodeDecodeError:
                continue
        raise UploadError("The CSV file has an unknown text encoding. Save it as 'CSV UTF-8' and try again.")
    if ext == ".xls":
        raise UploadError("Old .xls files are not supported. In Excel use File > Save As > Excel Workbook (.xlsx).")
    raise UploadError("Only .xlsx and .csv files can be imported.")


def read_rows(file_storage, columns) -> list[dict]:
    """Returns [{'_row': n, key: text, ...}] for every non-empty data row."""
    rows = _raw_rows(file_storage)
    header_index = None
    mapping: dict[int, str] = {}
    for i, row in enumerate(rows[:10]):  # header may sit below a title line
        found = {}
        for col_no, value in enumerate(row):
            n = _norm(value)
            for key, header, _req, _w, aliases in columns:
                if n and (n == _norm(header) or n in aliases) and key not in found.values():
                    found[col_no] = key
                    break
        required = {c[0] for c in columns if c[2]}
        if required <= set(found.values()):
            header_index, mapping = i, found
            break
    if header_index is None:
        needed = ", ".join(f"'{c[1]}'" for c in columns if c[2])
        raise UploadError(f"Could not find the header row. The first sheet needs columns named {needed}. Download the template to see the format.")

    result = []
    for offset, row in enumerate(rows[header_index + 1:], start=header_index + 2):
        record = {"_row": offset}
        for col_no, key in mapping.items():
            record[key] = _cell(row[col_no]) if col_no < len(row) else ""
        if any(v for k, v in record.items() if k != "_row"):
            result.append(record)
    if not result:
        raise UploadError("The file has a header row but no data rows.")
    if len(result) > MAX_ROWS:
        raise UploadError(f"The file has {len(result)} rows; the limit is {MAX_ROWS} per import. Split it into smaller files.")
    return result


def parse_status(text: str, default=None):
    t = _norm(text)
    if not t:
        return default
    if t in ("show", "1", "yes", "y", "active", "visible", "available", "true", "on"):
        return 1
    if t in ("hide", "0", "no", "n", "inactive", "hidden", "false", "off"):
        return 0
    raise ValueError("Status must be Show or Hide")


def parse_veg(text: str, default=None):
    t = _norm(text)
    if not t:
        return default
    if t in ("veg", "1", "yes", "y", "v", "vegetarian", "true"):
        return "1"
    if t in ("nonveg", "0", "no", "n", "nv", "nonvegetarian", "false"):
        return "0"
    raise ValueError("Veg must be VEG or NON VEG")


# ------------------------------------------------------------------ writing --

def _workbook(title: str, columns, rows: list[list], instructions: list[str], dropdowns: dict[str, list[str]]):
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append([c[1] + (" *" if c[2] else "") for c in columns])
    for col_no, col in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col_no)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = REQUIRED_FILL if col[2] else HEADER_FILL
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(col_no)].width = col[3]
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    last = max(ws.max_row, 500) + 500
    for key, options in dropdowns.items():
        col_no = next(i for i, c in enumerate(columns, start=1) if c[0] == key)
        letter = get_column_letter(col_no)
        dv = DataValidation(type="list", formula1='"' + ",".join(options) + '"', allow_blank=True, showErrorMessage=True,
                            errorTitle="Invalid value", error="Choose " + " or ".join(options))
        ws.add_data_validation(dv)
        dv.add(f"{letter}2:{letter}{last}")

    help_ws = wb.create_sheet("Instructions")
    help_ws.column_dimensions["A"].width = 110
    help_ws.append(["How to use this file"])
    help_ws["A1"].font = Font(bold=True, size=13)
    for line in instructions:
        help_ws.append([line])
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


CATEGORY_HELP = [
    "Fill one category per row on the first sheet. Pink headers (*) are required.",
    "Category Name: matched to existing categories ignoring capital letters. Existing ones are updated, new ones are added.",
    "Display Order: whole number. Lower numbers show first on the billing screen. Leave empty to keep the current order (0 for new).",
    "Status: Show or Hide. Hiding a category also hides its items. Leave empty to keep the current status (Show for new).",
    "Nothing is deleted by an import. If any row has a problem, nothing is imported and the problems are listed.",
]

MENU_HELP = [
    "Fill one menu item per row on the first sheet. Pink headers (*) are required.",
    "Item ID: leave empty for new items. Exported files include it so you can rename items safely.",
    "Without an Item ID, an item is matched by its name within the category (ignoring capital letters).",
    "Category: must match an existing category name, or tick 'Create missing categories' when importing.",
    "Price: before GST, e.g. 120 or 99.50. GST %: usually 5 (0, 5, 12, 18 or 28).",
    "Veg: VEG or NON VEG (default VEG). Status: Show or Hide (default Show).",
    "Nothing is deleted by an import. If any row has a problem, nothing is imported and the problems are listed.",
]


def category_template():
    return _workbook("Menu Categories", CATEGORY_COLUMNS, [["COLD BEVERAGES", 1, "Show"], ["HOT BEVERAGES", 2, "Show"]],
                     CATEGORY_HELP, {"status": ["Show", "Hide"]})


def category_export(categories: list[dict]):
    rows = [[c["name"], c["sort_order"], "Show" if c["active"] else "Hide"] for c in categories]
    return _workbook("Menu Categories", CATEGORY_COLUMNS, rows, CATEGORY_HELP, {"status": ["Show", "Hide"]})


def menu_template():
    rows = [["", "PLAIN TEA", "HOT BEVERAGES", 15, 5, "VEG", "Show", "Fresh masala tea"],
            ["", "MILK SHAKE", "COLD BEVERAGES", 80, 5, "VEG", "Show", ""]]
    return _workbook("Menu", MENU_COLUMNS, rows, MENU_HELP, {"veg": ["VEG", "NON VEG"], "status": ["Show", "Hide"]})


def menu_export(items: list[dict]):
    rows = [
        [i["id"], i["name"], i["category_name"], float(i["rate"]), float(i["gst"]),
         "VEG" if str(i["veg"]) == "1" else "NON VEG", "Show" if i["active"] else "Hide", i["description"] or ""]
        for i in items
    ]
    return _workbook("Menu", MENU_COLUMNS, rows, MENU_HELP, {"veg": ["VEG", "NON VEG"], "status": ["Show", "Hide"]})
