"""Small dependency-free PDF writer for Quote AI chat responses."""
from datetime import datetime, timezone
import unicodedata


PAGE_WIDTH = 842
PAGE_HEIGHT = 595
MARGIN_X = 41
BOTTOM_LIMIT = 38
COLUMN_HEADERS = (
    "Company name",
    "Item name",
    "Item price",
    "Additional requirement",
)
COLUMN_WIDTHS = (150, 170, 100, 340)
TABLE_WIDTH = sum(COLUMN_WIDTHS)
TABLE_HEADER_HEIGHT = 25
FONT_SIZE = 8.5
LINE_HEIGHT = 10.5
CELL_PADDING = 6


def _safe_text(value):
    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\r", "\n").replace("\t", " ")
    return "".join(
        char if ord(char) <= 0xFFFF and not 0xD800 <= ord(char) <= 0xDFFF else "?"
        for char in text
    )


def _character_width(char):
    if unicodedata.combining(char):
        return 0
    return 1 if unicodedata.east_asian_width(char) in {"F", "W"} else 0.53


def _wrap(value, width, font_size=FONT_SIZE):
    text = _safe_text(value)
    max_units = max(8, width / font_size)
    lines = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        line = ""
        for word in words:
            chunks = []
            chunk = ""
            chunk_width = 0
            for char in word:
                char_width = _character_width(char)
                if chunk and chunk_width + char_width > max_units:
                    chunks.append(chunk)
                    chunk = ""
                    chunk_width = 0
                chunk += char
                chunk_width += char_width
            if chunk:
                chunks.append(chunk)
            for word_part in chunks[:-1]:
                if line:
                    lines.append(line)
                    line = ""
                lines.append(word_part)
            word = chunks[-1] if chunks else ""
            candidate = f"{line} {word}".strip()
            candidate_width = sum(_character_width(char) for char in candidate)
            if candidate_width > max_units and line:
                lines.append(line)
                line = word
            else:
                line = candidate
        if line:
            lines.append(line)
    return lines or [""]


def _text(x, y, value, size=FONT_SIZE, bold=False, color=(0.10, 0.16, 0.15)):
    safe = _safe_text(value)
    encoded = safe.encode("utf-16-be").hex().upper()
    font = "F2" if bold else "F1"
    red, green, blue = color
    return (
        f"{red:.3f} {green:.3f} {blue:.3f} rg "
        f"BT /{font} {size:g} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm <{encoded}> Tj ET\n"
    )


def _rect(x, y, width, height, color):
    red, green, blue = color
    return f"{red:.3f} {green:.3f} {blue:.3f} rg {x:.2f} {y:.2f} {width:.2f} {height:.2f} re f\n"


def _line(x1, y1, x2, y2, color=(0.80, 0.83, 0.81)):
    red, green, blue = color
    return f"{red:.3f} {green:.3f} {blue:.3f} RG 0.55 w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S\n"


def _start_page(page_number, reply, comparison_count, quote_count):
    commands = []
    commands.append(_text(MARGIN_X, 565, "QUOTE BOT AI", 8, True, (0.18, 0.38, 0.34)))
    if page_number == 0:
        commands.append(_text(MARGIN_X, 540, "Quotation output", 18, True))
        created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        commands.append(_text(
            MARGIN_X, 522,
            f"Based on {comparison_count} completed comparisons and {quote_count} quotations · {created}",
            8.5, False, (0.35, 0.42, 0.40)))
        commands.append(_text(MARGIN_X, 501, "Quote AI response", 9, True))
        reply_lines = _wrap(reply, TABLE_WIDTH, 8.5)
        y = 486
        for line in reply_lines:
            commands.append(_text(MARGIN_X, y, line, 8.5))
            y -= 11
        table_top = y - 8
    else:
        commands.append(_text(MARGIN_X, 542, "Quotation output · continued", 14, True))
        table_top = 518
    return commands, table_top


def _table_header(commands, y):
    row_bottom = y - TABLE_HEADER_HEIGHT
    commands.append(_rect(MARGIN_X, row_bottom, TABLE_WIDTH, TABLE_HEADER_HEIGHT, (0.09, 0.27, 0.25)))
    x = MARGIN_X
    for header, width in zip(COLUMN_HEADERS, COLUMN_WIDTHS):
        commands.append(_text(x + CELL_PADDING, y - 16, header, 8, True, (1, 1, 1)))
        x += width
    commands.append(_line(MARGIN_X, row_bottom, MARGIN_X + TABLE_WIDTH, row_bottom, (0.09, 0.27, 0.25)))
    return row_bottom


def _table_row(commands, y, row, row_index):
    cells = [row.get(key, "Not stated") for key in (
        "company_name", "item_name", "item_price", "additional_requirement")]
    wrapped = [
        _wrap(value, width - 2 * CELL_PADDING)
        for value, width in zip(cells, COLUMN_WIDTHS)
    ]
    row_height = max(25, max(len(lines) for lines in wrapped) * LINE_HEIGHT + 2 * CELL_PADDING)
    row_bottom = y - row_height
    if row_index % 2:
        commands.append(_rect(MARGIN_X, row_bottom, TABLE_WIDTH, row_height, (0.96, 0.97, 0.96)))
    x = MARGIN_X
    for lines, width in zip(wrapped, COLUMN_WIDTHS):
        baseline = y - CELL_PADDING - FONT_SIZE
        for line in lines:
            commands.append(_text(x + CELL_PADDING, baseline, line, FONT_SIZE))
            baseline -= LINE_HEIGHT
        x += width
    x = MARGIN_X
    commands.append(_line(MARGIN_X, row_bottom, MARGIN_X + TABLE_WIDTH, row_bottom))
    for width in COLUMN_WIDTHS[:-1]:
        x += width
        commands.append(_line(x, y, x, row_bottom, (0.88, 0.90, 0.88)))
    return row_bottom


def _pdf_document(page_streams):
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        None,
        b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UCS2-H /DescendantFonts [4 0 R] >>",
        b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light /CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> /DW 1000 /W [32 [250] 33 126 500] /FontDescriptor 5 0 R >>",
        b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 4 /FontBBox [-25 -254 1000 880] /ItalicAngle 0 /Ascent 880 /Descent -120 /CapHeight 700 /StemV 80 >>",
    ]
    page_ids = [6 + index * 2 for index in range(len(page_streams))]
    objects[1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] "
        f"/Count {len(page_streams)} >>"
    ).encode("ascii")
    for index, stream in enumerate(page_streams):
        page_id = page_ids[index]
        stream_id = page_id + 1
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R /F2 3 0 R >> >> /Contents {stream_id} 0 R >>"
            .encode("ascii")
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        )
    info_id = len(objects) + 1
    objects.append(b"<< /Title <51756F746520426F74204149202D2051756F746174696F6E204F7574707574> /Producer <51756F746520426F74204149> >>")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for object_id, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_id} 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


def build_quote_chat_pdf(reply, items, comparison_count, quote_count):
    """Render a response summary and the requested four-column quote table."""
    rows = list(items or [])
    if not rows:
        rows = [{
            "company_name": "Not specified",
            "item_name": "No quotation item identified",
            "item_price": "Not stated",
            "additional_requirement": "No item-specific requirement was identified in this answer.",
        }]

    pages = []
    commands, y = _start_page(0, reply, comparison_count, quote_count)
    y = _table_header(commands, y)
    for row_index, row in enumerate(rows):
        cell_lines = [
            _wrap(row.get(key, "Not stated"), width - 2 * CELL_PADDING)
            for key, width in zip(
                ("company_name", "item_name", "item_price", "additional_requirement"),
                COLUMN_WIDTHS)
        ]
        row_height = max(25, max(len(lines) for lines in cell_lines) * LINE_HEIGHT + 2 * CELL_PADDING)
        if y - row_height < BOTTOM_LIMIT:
            pages.append(commands)
            commands, y = _start_page(len(pages), reply, comparison_count, quote_count)
            y = _table_header(commands, y)
        y = _table_row(commands, y, row, row_index)
    pages.append(commands)

    page_streams = []
    for page_number, page_commands in enumerate(pages, start=1):
        page_commands.append(_text(
            MARGIN_X + TABLE_WIDTH - 45, 20, f"Page {page_number} of {len(pages)}",
            7, False, (0.42, 0.47, 0.45)))
        page_streams.append("".join(page_commands).encode("ascii"))
    return _pdf_document(page_streams)
