"""Minimal XLSX writer (Office Open XML) - no third-party dependency.

One worksheet: title, subtitle, the summary lines, then the table with a filled header row, thin borders,
banded rows, frozen header, auto filter, column widths and fit-to-width printing. Numbers (incl. Decimal)
stay numbers and dates / datetimes are real Excel dates (dd/mm/yyyy [hh:mm]), so sums, sorting and
filters work in Excel. Same look as the frontend exports (src/utils/xlsxExport.js).
"""

import io
import math
import zipfile
from datetime import date, datetime
from decimal import Decimal
from xml.sax.saxutils import escape

BRAND = "FF045C9C"
# cellXfs indexes (see _STYLES); the banded twin of TEXT..DT is index + BAND, the summary-value twin + PLAIN
TITLE, META, HEAD, TEXT, NUM, DEC, DATE, DT, LABEL = 1, 2, 3, 4, 5, 6, 7, 8, 14
BAND, PLAIN = 5, 11
_EPOCH = datetime(1899, 12, 30)

_BODY_ALIGN = (
    (0, '<alignment vertical="center" wrapText="1" indent="1"/>'),
    (164, '<alignment horizontal="right" vertical="center" indent="1"/>'),
    (165, '<alignment horizontal="right" vertical="center" indent="1"/>'),
    (166, '<alignment horizontal="center" vertical="center"/>'),
    (167, '<alignment horizontal="center" vertical="center"/>'),
)
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="4"><numFmt numFmtId="164" formatCode="#,##0"/><numFmt numFmtId="165" formatCode="#,##0.##"/>'
    '<numFmt numFmtId="166" formatCode="dd/mm/yyyy"/><numFmt numFmtId="167" formatCode="dd/mm/yyyy hh:mm"/></numFmts>'
    '<fonts count="5"><font><sz val="10"/><color rgb="FF1F2937"/><name val="Arial"/></font>'
    f'<font><b/><sz val="14"/><color rgb="{BRAND}"/><name val="Arial"/></font>'
    '<font><i/><sz val="9"/><color rgb="FF6B7280"/><name val="Arial"/></font>'
    '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Arial"/></font>'
    '<font><b/><sz val="10"/><color rgb="FF374151"/><name val="Arial"/></font></fonts>'
    '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    f'<fill><patternFill patternType="solid"><fgColor rgb="{BRAND}"/><bgColor indexed="64"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF3F6FA"/><bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>'
    '<border><left style="thin"><color rgb="FFD9DEE5"/></left><right style="thin"><color rgb="FFD9DEE5"/></right>'
    '<top style="thin"><color rgb="FFD9DEE5"/></top><bottom style="thin"><color rgb="FFD9DEE5"/></bottom><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="20"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="0" fontId="3" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">'
    '<alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
    + "".join(f'<xf numFmtId="{fmt}" fontId="0" fillId="{fill}" borderId="1" xfId="0" applyNumberFormat="1" applyFill="1" '
              f'applyBorder="1" applyAlignment="1">{align}</xf>' for fill in (0, 3) for fmt, align in _BODY_ALIGN)
    + '<xf numFmtId="0" fontId="4" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    # summary values: plain (no border), left aligned next to their label
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment horizontal="left"/></xf>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="left"/></xf>'
    '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="left"/></xf>'
    '<xf numFmtId="166" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="left"/></xf>'
    '<xf numFmtId="167" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1" applyAlignment="1"><alignment horizontal="left"/></xf>'
    '</cellXfs></styleSheet>'
)


def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _serial(value):
    if isinstance(value, datetime):
        return (value.replace(tzinfo=None) - _EPOCH).total_seconds() / 86400
    return (datetime(value.year, value.month, value.day) - _EPOCH).days


def _classify(value):
    """-> (cell value, style, display width)"""
    if value is None or value == "":
        return None, TEXT, 0
    if isinstance(value, bool):
        value = "YES" if value else "NO"
    if isinstance(value, Decimal):
        value = int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (int, float)):
        return value, NUM if float(value).is_integer() else DEC, len(f"{value:,.2f}".rstrip("0").rstrip("."))
    if isinstance(value, datetime):
        return _serial(value), DT, 16
    if isinstance(value, date):
        return _serial(value), DATE, 10
    text = str(value)
    # capitals are ~30% wider than the average character
    return text, TEXT, max(len(x) + sum(c.isupper() for c in x) * 0.3 for x in text.split("\n"))


def _cell(ref, value, style=0):
    st = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{st}/>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{st}><v>{value}</v></c>'
    text = escape(str(value)).replace("\x00", "")
    return f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{text}</t></is></c>'


def build(title, subtitle, summary, columns, rows):
    """columns: [(key, header)] ; summary: [(label, value)] ; rows: [dict]."""
    lines = []
    r = 1
    lines.append(f'<row r="{r}" ht="24" customHeight="1">{_cell(f"A{r}", title, TITLE)}</row>')
    r += 1
    lines.append(f'<row r="{r}">{_cell(f"A{r}", subtitle, META)}</row>')
    r += 1
    data = [[_classify(row.get(k)) for k, _ in columns] for row in rows]
    widths = [min(len(str(h)), 24) for _, h in columns]
    for cells in data[:1000]:
        for i, (_, _, w) in enumerate(cells):
            widths[i] = max(widths[i], w)
    widths = [min(max(math.ceil(w * 1.1) + 4, 9), 50) for w in widths]
    # summary values line up in the first column past the end of the longest (overflowing) bold label
    longest = max([len(str(label)) for label, _ in summary] or [0])
    at, room = 0, 0
    while at < len(widths) - 1 and room < longest * 1.1 + 2:
        room += widths[at]
        at += 1
    for label, value in summary:
        v, style, _ = _classify(value)
        lines.append(f'<row r="{r}">{_cell(f"A{r}", label, LABEL)}{_cell(f"{_col(max(at, 1))}{r}", v, style + PLAIN)}</row>')
        r += 1
    r += 1
    header_row = r
    lines.append(f'<row r="{r}" ht="30" customHeight="1">' + "".join(_cell(f"{_col(i)}{r}", h, HEAD) for i, (_, h) in enumerate(columns)) + "</row>")
    for n, cells in enumerate(data):
        r += 1
        band = BAND if n % 2 else 0
        tall = max([1] + [v.count("\n") + 1 for v, _, _ in cells if isinstance(v, str)])
        lines.append(f'<row r="{r}" ht="{18 * tall}" customHeight="1">'
                     + "".join(_cell(f"{_col(i)}{r}", v, style + band) for i, (v, style, _) in enumerate(cells)) + "</row>")
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in enumerate(widths))
    last_col = _col(max(len(columns) - 1, 0))
    last_row = max(r, header_row + 1)
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
             '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
             f'<sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="{header_row}" topLeftCell="A{header_row + 1}" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
             f'<sheetFormatPr defaultRowHeight="16"/><cols>{cols}</cols><sheetData>{"".join(lines)}</sheetData>'
             f'<autoFilter ref="A{header_row}:{last_col}{last_row}"/>'
             '<pageMargins left="0.4" right="0.4" top="0.5" bottom="0.5" header="0.3" footer="0.3"/>'
             '<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>')
    sheet_name = escape((title or "Report")[:31].replace("/", "-").replace("\\", "-").replace("?", "").replace("*", "").replace("[", "(").replace("]", ")").replace(":", " "))
    quoted = sheet_name.replace("'", "''")
    files = {
        "[Content_Types].xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                                '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                                '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'),
        "_rels/.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'),
        "xl/workbook.xml": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                            f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets>'
                            f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">\'{quoted}\'!$A${header_row}:${last_col}${last_row}</definedName>'
                            f'<definedName name="_xlnm.Print_Titles" localSheetId="0">\'{quoted}\'!${header_row}:${header_row}</definedName></definedNames></workbook>'),
        "xl/_rels/workbook.xml.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                                       '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'),
        "xl/worksheets/sheet1.xml": sheet,
        "xl/styles.xml": _STYLES,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def build_csv(columns, rows):
    import csv
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([h for _, h in columns])
    for row in rows:
        vals = []
        for k, _ in columns:
            v = row.get(k)
            if isinstance(v, datetime):
                v = v.strftime("%d/%m/%Y %H:%M")
            elif isinstance(v, date):
                v = v.strftime("%d/%m/%Y")
            vals.append("" if v is None else v)
        w.writerow(vals)
    return ("﻿" + out.getvalue()).encode("utf-8")
