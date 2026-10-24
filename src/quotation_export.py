"""Dependency-free XLSX export for the staff quotation register."""
from datetime import datetime, timezone
from io import BytesIO
import math
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape


MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

COLUMNS = (
    ("Comparison ID", "comparison_id"),
    ("WhatsApp message ID", "message_id"),
    ("Received (UTC)", "received_at"),
    ("Analyzed (UTC)", "analyzed_at"),
    ("Client", "client_name"),
    ("WhatsApp contact", "wa_id"),
    ("Vendor", "vendor"),
    ("Source file", "filename"),
    ("Message type", "message_type"),
    ("Total price", "total_price"),
    ("Currency", "currency"),
    ("Price (MYR / RM)", "myr_total_price"),
    ("MYR per original currency unit", "exchange_rate"),
    ("Exchange rate date", "exchange_rate_date"),
    ("Exchange rate source", "exchange_rate_source"),
    ("Currency conversion warning", "conversion_error"),
    ("Price basis", "price_basis"),
    ("Delivery days", "delivery_days"),
    ("Delivery unit", "delivery_unit"),
    ("Delivery date", "delivery_date"),
    ("Delivery details", "delivery_text"),
    ("Priority", "priority"),
    ("Rank", "rank"),
    ("AI suggested", "recommended"),
    ("Team choice", "team_choice"),
    ("Confidence", "confidence"),
    ("Evidence", "evidence"),
    ("Notes", "notes"),
    ("Extraction warning", "extraction_error"),
    ("Message excerpt", "message_excerpt"),
)

NUMERIC_FIELDS = {
    "myr_total_price", "exchange_rate",
    "comparison_id", "total_price", "delivery_days", "rank", "confidence"
}
BOOLEAN_FIELDS = {"recommended", "team_choice"}
TIMESTAMP_FIELDS = {"received_at", "analyzed_at"}


def _xml_text(value):
    text = str(value)
    safe = []
    for character in text:
        codepoint = ord(character)
        if (codepoint in (0x9, 0xA, 0xD)
                or 0x20 <= codepoint <= 0xD7FF
                or 0xE000 <= codepoint <= 0xFFFD
                or 0x10000 <= codepoint <= 0x10FFFF):
            safe.append(character)
    return escape("".join(safe))


def _column_name(number):
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _timestamp_text(value):
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _cell_xml(reference, key, value, style=None):
    style_attr = f' s="{style}"' if style is not None else ""
    if value is None or value == "":
        return f'<c r="{reference}"{style_attr}/>'
    if key in BOOLEAN_FIELDS:
        value = "Yes" if value else "No"
    elif key in TIMESTAMP_FIELDS:
        value = _timestamp_text(value)
    elif key in NUMERIC_FIELDS:
        if isinstance(value, bool):
            value = None
        else:
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = None
            if number is not None and math.isfinite(number):
                numeric = str(int(number)) if number.is_integer() else format(number, ".15g")
                return f'<c r="{reference}" t="n"{style_attr}><v>{numeric}</v></c>'
            value = ""
    text = _xml_text(value)
    return (
        f'<c r="{reference}" t="inlineStr"{style_attr}>'
        f'<is><t xml:space="preserve">{text}</t></is></c>'
    )


def _worksheet_xml(rows):
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    last_column = _column_name(len(COLUMNS))
    last_row = len(rows) + 1
    column_widths = "".join(
        f'<col min="{index}" max="{index}" width="{min(max(len(title) + 3, 12), 40)}" customWidth="1"/>'
        for index, (title, _) in enumerate(COLUMNS, start=1)
    )
    header_cells = "".join(
        _cell_xml(f"{_column_name(index)}1", "", title, style=1)
        for index, (title, key) in enumerate(COLUMNS, start=1)
    )
    data_xml = []
    for row_number, row in enumerate(rows, start=2):
        cells = "".join(
            _cell_xml(f"{_column_name(index)}{row_number}", key, row.get(key))
            for index, (_, key) in enumerate(COLUMNS, start=1)
        )
        data_xml.append(f'<row r="{row_number}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{namespace}">'
        '<sheetViews><sheetView workbookViewId="0">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        '</sheetView></sheetViews><sheetFormatPr defaultRowHeight="18"/>'
        f'<cols>{column_widths}</cols><sheetData>'
        f'<row r="1" ht="30" customHeight="1">{header_cells}</row>'
        f'{"".join(data_xml)}</sheetData>'
        f'<autoFilter ref="A1:{last_column}{max(last_row, 1)}"/>'
        '</worksheet>'
    )


def build_quotation_workbook(rows):
    """Return a standards-compliant, filterable one-sheet XLSX workbook."""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as workbook:
        workbook.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>""")
        workbook.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>""")
        workbook.writestr("xl/workbook.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<bookViews><workbookView activeTab="0"/></bookViews>
<sheets><sheet name="Quotations" sheetId="1" r:id="rId1"/></sheets>
</workbook>""")
        workbook.writestr("xl/_rels/workbook.xml.rels", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""")
        workbook.writestr("xl/styles.xml", """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>
<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF164E48"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>""")
        workbook.writestr("xl/worksheets/sheet1.xml", _worksheet_xml(rows))
    return output.getvalue()
