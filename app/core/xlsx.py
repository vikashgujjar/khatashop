"""Reading and writing Excel .xlsx files with the standard library (zipfile + XML).

Offers the small part of openpyxl's interface that the app uses:
    Workbook(), wb.active, wb.create_sheet(), wb.save(), ws.append(), ws.cell(), ws["A1"], ws[2], ws["H"],
    cell.font / fill / alignment / number_format, column_dimensions[...].width, freeze_panes,
    DataValidation (drop-down lists), get_column_letter, load_workbook(...).worksheets[0].iter_rows(values_only=True)
Strings that start with "=" are written as formulas (same as openpyxl).
"""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as _xml_escape

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
EPOCH = datetime(1899, 12, 30)


def get_column_letter(n: int) -> str:
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def column_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def _split_ref(ref: str) -> tuple[int, int]:
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", ref)
    if not m:
        raise ValueError(f"Bad cell reference {ref!r}")
    return int(m.group(2)), column_index(m.group(1))


def _esc(text) -> str:
    return _xml_escape(_ILLEGAL.sub("", str(text)), {'"': "&quot;"})


# ------------------------------------------------------------------ styles --

class Font:
    def __init__(self, bold=False, size=None, color=None, italic=False, name=None):
        self.bold, self.size, self.color, self.italic, self.name = bold, size, color, italic, name

    def key(self):
        return ("font", self.bold, self.size, self.color, self.italic, self.name)

    def xml(self):
        parts = []
        if self.bold:
            parts.append("<b/>")
        if self.italic:
            parts.append("<i/>")
        parts.append(f'<sz val="{self.size or 11}"/>')
        if self.color:
            parts.append(f'<color rgb="{_argb(self.color)}"/>')
        parts.append(f'<name val="{_esc(self.name or "Calibri")}"/><family val="2"/>')
        return "<font>" + "".join(parts) + "</font>"


class PatternFill:
    def __init__(self, fill_type=None, fgColor=None, start_color=None, patternType=None, **_kw):  # noqa: N803 - openpyxl names
        self.fill_type = fill_type or patternType
        self.color = fgColor or start_color

    def key(self):
        return ("fill", self.fill_type, self.color)

    def xml(self):
        if not self.fill_type:
            return '<fill><patternFill patternType="none"/></fill>'
        color = f'<fgColor rgb="{_argb(self.color)}"/><bgColor indexed="64"/>' if self.color else ""
        return f'<fill><patternFill patternType="{self.fill_type}">{color}</patternFill></fill>'


class Alignment:
    def __init__(self, horizontal=None, vertical=None, wrap_text=None, **_kw):
        self.horizontal, self.vertical, self.wrap_text = horizontal, vertical, wrap_text

    def key(self):
        return ("align", self.horizontal, self.vertical, self.wrap_text)

    def xml(self):
        attrs = []
        if self.horizontal:
            attrs.append(f'horizontal="{self.horizontal}"')
        if self.vertical:
            attrs.append(f'vertical="{self.vertical}"')
        if self.wrap_text:
            attrs.append('wrapText="1"')
        return f"<alignment {' '.join(attrs)}/>"


def _argb(color: str) -> str:
    color = str(color).lstrip("#").upper()
    return color if len(color) == 8 else "FF" + color


class DataValidation:
    def __init__(self, type="list", formula1=None, allow_blank=True, showErrorMessage=True,  # noqa: A002,N803
                 errorTitle=None, error=None, **_kw):  # noqa: N803
        self.type = type
        self.formula1 = formula1
        self.allow_blank = allow_blank
        self.show_error = showErrorMessage
        self.error_title = errorTitle
        self.error = error
        self.ranges = []

    def add(self, cell_range: str):
        self.ranges.append(cell_range)

    def xml(self):
        attrs = [f'type="{self.type}"']
        if self.allow_blank:
            attrs.append('allowBlank="1"')
        if self.show_error:
            attrs.append('showErrorMessage="1"')
        if self.error_title:
            attrs.append(f'errorTitle="{_esc(self.error_title)}"')
        if self.error:
            attrs.append(f'error="{_esc(self.error)}"')
        attrs.append(f'sqref="{" ".join(self.ranges)}"')
        formula = f"<formula1>{_esc(self.formula1)}</formula1>" if self.formula1 is not None else ""
        return f"<dataValidation {' '.join(attrs)}>{formula}</dataValidation>"


# ------------------------------------------------------------------- cells --

class Cell:
    __slots__ = ("row", "column", "value", "font", "fill", "alignment", "number_format")

    def __init__(self, row, column, value=None):
        self.row, self.column, self.value = row, column, value
        self.font = self.fill = self.alignment = None
        self.number_format = "General"

    @property
    def coordinate(self):
        return f"{get_column_letter(self.column)}{self.row}"

    @property
    def column_letter(self):
        return get_column_letter(self.column)


class _ColumnDimension:
    def __init__(self):
        self.width = None


class _Dimensions(dict):
    def __missing__(self, key):
        value = _ColumnDimension()
        self[key] = value
        return value


class Worksheet:
    def __init__(self, workbook, title):
        self.parent = workbook
        self._title = title
        self._cells: dict[tuple[int, int], Cell] = {}
        self.column_dimensions = _Dimensions()
        self.freeze_panes = None
        self.data_validations: list[DataValidation] = []
        self._max_row = 0
        self._max_col = 0

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, value):
        value = re.sub(r"[\\/*?:\[\]]", "-", str(value))[:31] or "Sheet"
        self._title = value

    @property
    def max_row(self):
        return max(self._max_row, 1)

    @property
    def max_column(self):
        return max(self._max_col, 1)

    def cell(self, row: int, column: int, value=None) -> Cell:
        key = (row, column)
        c = self._cells.get(key)
        if c is None:
            c = Cell(row, column)
            self._cells[key] = c
            self._max_row = max(self._max_row, row)
            self._max_col = max(self._max_col, column)
        if value is not None:
            c.value = value
        return c

    def append(self, values):
        row = self._max_row + 1
        self._max_row = row
        for col, value in enumerate(values, start=1):
            if value is not None:
                self.cell(row, col, value)
        return row

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.cell(key, c) for c in range(1, self.max_column + 1))
        if re.fullmatch(r"[A-Za-z]+", key):
            col = column_index(key)
            return tuple(self.cell(r, col) for r in range(1, self.max_row + 1))
        row, col = _split_ref(key)
        return self.cell(row, col)

    def add_data_validation(self, dv: DataValidation):
        self.data_validations.append(dv)

    def iter_rows(self, min_row=1, max_row=None, values_only=False):
        last = max_row or self.max_row
        for r in range(min_row, last + 1):
            cells = [self._cells.get((r, c)) for c in range(1, self.max_column + 1)]
            if values_only:
                yield tuple(c.value if c else None for c in cells)
            else:
                yield tuple(c or Cell(r, i + 1) for i, c in enumerate(cells))

    @property
    def values(self):
        return self.iter_rows(values_only=True)


class Workbook:
    def __init__(self):
        self.worksheets: list[Worksheet] = [Worksheet(self, "Sheet")]

    @property
    def active(self) -> Worksheet:
        return self.worksheets[0]

    def create_sheet(self, title=None) -> Worksheet:
        ws = Worksheet(self, title or f"Sheet{len(self.worksheets) + 1}")
        ws.title = ws.title
        self.worksheets.append(ws)
        return ws

    def close(self):
        pass

    def save(self, target):
        data = _write_workbook(self)
        if hasattr(target, "write"):
            target.write(data)
        else:
            with open(target, "wb") as fh:
                fh.write(data)


# ----------------------------------------------------------------- writing --

_BUILTIN_FORMATS = {"General": 0, "0": 1, "0.00": 2, "#,##0": 3, "#,##0.00": 4, "0%": 9, "0.00%": 10,
                    "mm-dd-yy": 14, "d-mmm-yy": 15, "h:mm": 20, "h:mm:ss": 21, "m/d/yy h:mm": 22, "@": 49}


class _StyleBook:
    def __init__(self):
        self.fonts = [Font().xml()]
        self.fills = ['<fill><patternFill patternType="none"/></fill>', '<fill><patternFill patternType="gray125"/></fill>']
        self.numfmts: dict[str, int] = {}
        self.xfs = ['<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>']
        self._font_ids = {Font().key(): 0}
        self._fill_ids = {}
        self._xf_ids = {(0, 0, 0, None): 0}

    def _font(self, font):
        if font is None:
            return 0
        k = font.key()
        if k not in self._font_ids:
            self._font_ids[k] = len(self.fonts)
            self.fonts.append(font.xml())
        return self._font_ids[k]

    def _fill(self, fill):
        if fill is None or not fill.fill_type:
            return 0
        k = fill.key()
        if k not in self._fill_ids:
            self._fill_ids[k] = len(self.fills)
            self.fills.append(fill.xml())
        return self._fill_ids[k]

    def _numfmt(self, fmt):
        if not fmt or fmt == "General":
            return 0
        if fmt in _BUILTIN_FORMATS:
            return _BUILTIN_FORMATS[fmt]
        if fmt not in self.numfmts:
            self.numfmts[fmt] = 164 + len(self.numfmts)
        return self.numfmts[fmt]

    def xf_for(self, cell: Cell) -> int:
        fmt = cell.number_format
        if (fmt == "General" or not fmt) and isinstance(cell.value, datetime):
            fmt = "yyyy-mm-dd h:mm:ss"
        elif (fmt == "General" or not fmt) and isinstance(cell.value, date):
            fmt = "yyyy-mm-dd"
        key = (self._font(cell.font), self._fill(cell.fill), self._numfmt(fmt), cell.alignment.key() if cell.alignment else None)
        if key not in self._xf_ids:
            font_id, fill_id, fmt_id, _align = key
            attrs = f'numFmtId="{fmt_id}" fontId="{font_id}" fillId="{fill_id}" borderId="0" xfId="0"'
            if fmt_id:
                attrs += ' applyNumberFormat="1"'
            if font_id:
                attrs += ' applyFont="1"'
            if fill_id:
                attrs += ' applyFill="1"'
            if cell.alignment:
                self.xfs.append(f'<xf {attrs} applyAlignment="1">{cell.alignment.xml()}</xf>')
            else:
                self.xfs.append(f"<xf {attrs}/>")
            self._xf_ids[key] = len(self.xfs) - 1
        return self._xf_ids[key]

    def xml(self):
        numfmts = ""
        if self.numfmts:
            items = "".join(f'<numFmt numFmtId="{i}" formatCode="{_esc(f)}"/>' for f, i in self.numfmts.items())
            numfmts = f'<numFmts count="{len(self.numfmts)}">{items}</numFmts>'
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<styleSheet xmlns="{NS}">{numfmts}'
                f'<fonts count="{len(self.fonts)}">{"".join(self.fonts)}</fonts>'
                f'<fills count="{len(self.fills)}">{"".join(self.fills)}</fills>'
                '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                f'<cellXfs count="{len(self.xfs)}">{"".join(self.xfs)}</cellXfs>'
                '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                "</styleSheet>")


def _serial(value) -> float:
    if isinstance(value, datetime):
        delta = value - EPOCH
    elif isinstance(value, date):
        delta = datetime(value.year, value.month, value.day) - EPOCH
    elif isinstance(value, time):
        return (value.hour * 3600 + value.minute * 60 + value.second) / 86400
    else:
        delta = value
    return delta.days + delta.seconds / 86400 + delta.microseconds / 86400e6


def _cell_xml(cell: Cell, styles: _StyleBook, strings: dict) -> str:
    ref = cell.coordinate
    s = styles.xf_for(cell)
    style = f' s="{s}"' if s else ""
    v = cell.value
    if v is None:
        return f'<c r="{ref}"{style}/>' if s else ""
    if isinstance(v, bool):
        return f'<c r="{ref}"{style} t="b"><v>{int(v)}</v></c>'
    if isinstance(v, (int, float, Decimal)):
        return f'<c r="{ref}"{style}><v>{v}</v></c>'
    if isinstance(v, (datetime, date, time, timedelta)):
        return f'<c r="{ref}"{style}><v>{_serial(v)}</v></c>'
    text = str(v)
    if text.startswith("=") and len(text) > 1:
        return f'<c r="{ref}"{style}><f>{_esc(text[1:])}</f></c>'
    idx = strings.setdefault(text, len(strings))
    return f'<c r="{ref}"{style} t="s"><v>{idx}</v></c>'


def _sheet_xml(ws: Worksheet, styles: _StyleBook, strings: dict, selected: bool) -> str:
    rows = {}
    for (r, c), cell in ws._cells.items():
        rows.setdefault(r, []).append(cell)
    row_xml = []
    for r in sorted(rows):
        cells = "".join(_cell_xml(cell, styles, strings) for cell in sorted(rows[r], key=lambda x: x.column))
        if cells:
            row_xml.append(f'<row r="{r}">{cells}</row>')
    view = f'<sheetView workbookViewId="0"{" tabSelected=" + chr(34) + "1" + chr(34) if selected else ""}>'
    if ws.freeze_panes and ws.freeze_panes != "A1":
        frow, fcol = _split_ref(ws.freeze_panes)
        xs, ys = fcol - 1, frow - 1
        pane = "bottomRight" if xs and ys else ("bottomLeft" if ys else "topRight")
        split = (f' xSplit="{xs}"' if xs else "") + (f' ySplit="{ys}"' if ys else "")
        view += f'<pane{split} topLeftCell="{ws.freeze_panes}" activePane="{pane}" state="frozen"/>'
        view += f'<selection pane="{pane}" activeCell="{ws.freeze_panes}" sqref="{ws.freeze_panes}"/>'
    view += "</sheetView>"
    cols = ""
    widths = [(column_index(k), d.width) for k, d in ws.column_dimensions.items() if d.width]
    if widths:
        cols = "<cols>" + "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in sorted(widths)) + "</cols>"
    dv = ""
    if ws.data_validations:
        dv = f'<dataValidations count="{len(ws.data_validations)}">' + "".join(d.xml() for d in ws.data_validations) + "</dataValidations>"
    dim = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="{NS}" xmlns:r="{REL_NS}">'
            f'<dimension ref="{dim}"/><sheetViews>{view}</sheetViews><sheetFormatPr defaultRowHeight="15"/>{cols}'
            f'<sheetData>{"".join(row_xml)}</sheetData>{dv}'
            '<pageMargins left="0.75" right="0.75" top="1" bottom="1" header="0.5" footer="0.5"/></worksheet>')


def _write_workbook(wb: Workbook) -> bytes:
    styles = _StyleBook()
    strings: dict[str, int] = {}
    sheets = [_sheet_xml(ws, styles, strings, i == 0) for i, ws in enumerate(wb.worksheets)]
    n = len(sheets)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        overrides = "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                            for i in range(1, n + 1))
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   f"{overrides}"
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
                   '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                   '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
                   "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                   '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                   "</Relationships>")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        z.writestr("docProps/core.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                   'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>KhataShop</dc:creator>'
                   f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
                   f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified></cp:coreProperties>')
        z.writestr("docProps/app.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                   "<Application>KhataShop</Application></Properties>")
        sheet_list = "".join(f'<sheet name="{_esc(ws.title)}" sheetId="{i}" r:id="rId{i}"/>' for i, ws in enumerate(wb.worksheets, start=1))
        z.writestr("xl/workbook.xml",
                   f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook xmlns="{NS}" xmlns:r="{REL_NS}">'
                   f'<bookViews><workbookView activeTab="0"/></bookViews><sheets>{sheet_list}</sheets></workbook>')
        rels = "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                       f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                   f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}'
                   f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   f'<Relationship Id="rId{n + 2}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
                   "</Relationships>")
        for i, xml in enumerate(sheets, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", xml)
        z.writestr("xl/styles.xml", styles.xml())
        sst = "".join(
            f'<si><t{" xml:space=" + chr(34) + "preserve" + chr(34) if s != s.strip() else ""}>{_esc(s)}</t></si>'
            for s in sorted(strings, key=strings.get))
        z.writestr("xl/sharedStrings.xml",
                   f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<sst xmlns="{NS}" count="{len(strings)}" '
                   f'uniqueCount="{len(strings)}">{sst}</sst>')
    return buf.getvalue()


# ----------------------------------------------------------------- reading --

_DATE_FORMAT_IDS = set(range(14, 23)) | set(range(45, 48))


def _is_date_format(code: str) -> bool:
    code = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code or "").lower()
    return bool(re.search(r"[dmyhs]", code)) and "general" not in code and not re.search(r"[0#]", code)


def load_workbook(source, read_only=False, data_only=True, **_kw) -> Workbook:
    """Reads an .xlsx (path, bytes or file object). Values only: formulas give their last saved result."""
    if isinstance(source, (bytes, bytearray)):
        source = io.BytesIO(source)
    z = zipfile.ZipFile(source)
    n = {"m": NS}
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", n):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS}}}t")))
    date_styles = set()
    if "xl/styles.xml" in z.namelist():
        st = ET.fromstring(z.read("xl/styles.xml"))
        custom = {int(f.get("numFmtId")): f.get("formatCode", "") for f in st.iter(f"{{{NS}}}numFmt")}
        xfs = st.find("m:cellXfs", n)
        if xfs is not None:
            for i, xf in enumerate(xfs.findall("m:xf", n)):
                fid = int(xf.get("numFmtId", "0"))
                if fid in _DATE_FORMAT_IDS or (fid in custom and _is_date_format(custom[fid])):
                    date_styles.add(i)
    wbx = ET.fromstring(z.read("xl/workbook.xml"))
    rels = {}
    if "xl/_rels/workbook.xml.rels" in z.namelist():
        for rel in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
            target = rel.get("Target", "")
            rels[rel.get("Id")] = target.lstrip("/") if target.startswith("/") else "xl/" + target
    wb = Workbook()
    wb.worksheets = []
    for i, sheet in enumerate(wbx.iter(f"{{{NS}}}sheet"), start=1):
        rid = sheet.get(f"{{{REL_NS}}}id")
        path = rels.get(rid) or f"xl/worksheets/sheet{i}.xml"
        ws = Worksheet(wb, sheet.get("name", f"Sheet{i}"))
        root = ET.fromstring(z.read(path))
        for row_no, row in enumerate(root.iter(f"{{{NS}}}row"), start=1):
            r = int(row.get("r", row_no))
            next_col = 1
            for c in row.findall("m:c", n):
                ref = c.get("r")
                if ref:
                    r, col = _split_ref(ref)
                else:
                    col = next_col
                next_col = col + 1
                t = c.get("t")
                v = c.find("m:v", n)
                text = v.text if v is not None else None
                if t == "s" and text is not None:
                    value = shared[int(text)]
                elif t == "inlineStr":
                    is_ = c.find("m:is", n)
                    value = "".join(x.text or "" for x in is_.iter(f"{{{NS}}}t")) if is_ is not None else ""
                elif t == "b":
                    value = text == "1"
                elif t in ("str", "e"):
                    value = text or ""
                elif text is None:
                    continue
                else:
                    try:
                        value = int(text) if re.fullmatch(r"-?\d+", text) else float(text)
                    except ValueError:
                        value = text
                    if int(c.get("s", "0")) in date_styles and isinstance(value, (int, float)):
                        value = EPOCH + timedelta(days=value)
                ws.cell(r, col, value)
        wb.worksheets.append(ws)
    z.close()
    if not wb.worksheets:
        raise ValueError("The workbook has no sheets.")
    return wb
