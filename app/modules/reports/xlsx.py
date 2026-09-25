"""Minimal XLSX writer (Office Open XML) - no third-party dependency.

One worksheet: a title block, the summary lines, then the table with a bold header row, frozen header,
auto filter and column widths. Numbers are written as numbers, dates / datetimes as text (dd/mm/yyyy [hh:mm]).
"""

import io
import zipfile
from datetime import date, datetime
from xml.sax.saxutils import escape


def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref, value, style=0):
    st = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{st}/>'
    if isinstance(value, bool):
        value = "YES" if value else "NO"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"{st}><v>{value}</v></c>'
    if isinstance(value, datetime):
        value = value.strftime("%d/%m/%Y %H:%M")
    elif isinstance(value, date):
        value = value.strftime("%d/%m/%Y")
    text = escape(str(value)).replace("\x00", "")
    return f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{text}</t></is></c>'


def build(title, subtitle, summary, columns, rows):
    """columns: [(key, header)] ; summary: [(label, value)] ; rows: [dict]."""
    lines = []
    r = 1
    lines.append(f'<row r="{r}">{_cell(f"A{r}", title, 1)}</row>')
    r += 1
    lines.append(f'<row r="{r}">{_cell(f"A{r}", subtitle, 3)}</row>')
    r += 1
    for label, value in summary:
        lines.append(f'<row r="{r}">{_cell(f"A{r}", label, 3)}{_cell(f"B{r}", value, 2)}</row>')
        r += 1
    r += 1
    header_row = r
    lines.append(f'<row r="{r}">' + "".join(_cell(f"{_col(i)}{r}", h, 2) for i, (_, h) in enumerate(columns)) + "</row>")
    for row in rows:
        r += 1
        lines.append(f'<row r="{r}">' + "".join(_cell(f"{_col(i)}{r}", row.get(k)) for i, (k, _) in enumerate(columns)) + "</row>")
    widths = []
    for i, (k, h) in enumerate(columns):
        longest = max([len(str(h))] + [len(str(x.get(k) if x.get(k) is not None else "")) for x in rows[:500]])
        widths.append(f'<col min="{i + 1}" max="{i + 1}" width="{min(max(longest + 2, 8), 60)}" customWidth="1"/>')
    last = f"{_col(max(len(columns) - 1, 0))}{r}"
    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
             f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="{header_row}" topLeftCell="A{header_row + 1}" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
             f'<cols>{"".join(widths)}</cols><sheetData>{"".join(lines)}</sheetData>'
             f'<autoFilter ref="A{header_row}:{last}"/></worksheet>')
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="3"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="14"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
              '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFE8F1FF"/><bgColor indexed="64"/></patternFill></fill></fills>'
              '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
              '<xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
              '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs></styleSheet>')
    sheet_name = escape((title or "Report")[:31].replace("/", "-").replace("\\", "-").replace("?", "").replace("*", "").replace("[", "(").replace("]", ")").replace(":", " "))
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
                            f'<definedNames><definedName name="_xlnm._FilterDatabase" localSheetId="0" hidden="1">\'{sheet_name}\'!$A${header_row}:${_col(max(len(columns) - 1, 0))}${r}</definedName></definedNames></workbook>'),
        "xl/_rels/workbook.xml.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                                       '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>'),
        "xl/worksheets/sheet1.xml": sheet,
        "xl/styles.xml": styles,
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
