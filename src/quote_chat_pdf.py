"""Generate a temporary, downloadable Quote AI PDF without storing it on disk."""
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import os
import shutil
import subprocess
import tempfile


COLUMNS = (
    ("company_name", "Company name"),
    ("item_name", "Item name"),
    ("item_price", "Item price"),
    ("additional_requirement", "Additional requirement"),
)


class QuoteChatPdfError(RuntimeError):
    """A safe error raised when the server cannot render a PDF."""


def _html_document(reply, items, comparison_count, quote_count, price_reference=None):
    rows = list(items or [])
    if not rows:
        rows = [{
            "company_name": "Not specified",
            "item_name": "No quotation item identified",
            "item_price": "Not stated",
            "additional_requirement": "No item-specific requirement was identified in this answer.",
        }]

    headers = "".join(f"<th scope='col'>{escape(label)}</th>" for _, label in COLUMNS)
    body_rows = []
    for row in rows:
        cells = "".join(
            f"<td>{escape(str(row.get(field) or 'Not stated'))}</td>"
            for field, _ in COLUMNS
        )
        body_rows.append(f"<tr>{cells}</tr>")
    created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    reference_html = ""
    if price_reference:
        reference_rows = "".join(
            "<tr>" + "".join(f"<td>{escape(str(row.get(field) or 'Not stated'))}</td>"
                              for field, _ in COLUMNS) + "</tr>"
            for row in price_reference
        )
        reference_html = (
            "<h2>Saved quotation totals — MYR reference</h2>"
            "<p>Original prices are preserved. Reference conversions exclude bank fees "
            "and may differ from actual payment rates. These are whole-quotation totals, "
            "not AI-generated item prices.</p>"
            f"<table aria-label='MYR price reference'><thead><tr>{headers}</tr></thead>"
            f"<tbody>{reference_rows}</tbody></table>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Quote Bot AI — Quotation output</title>
  <style>
    @page {{ size: A4 landscape; margin: 13mm 12mm; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: #1d2927; font: 10px/1.45 Arial, "Noto Sans CJK SC", "Noto Sans", sans-serif; }}
    .brand {{ margin: 0 0 7px; color: #26734a; font-size: 9px; font-weight: 700; letter-spacing: .15em; }}
    h1 {{ margin: 0; color: #193e39; font-size: 22px; letter-spacing: -.04em; }}
    .meta {{ margin: 6px 0 16px; color: #66716f; font-size: 9px; }}
    .summary-label {{ margin: 0 0 4px; color: #26734a; font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: .08em; }}
    .summary {{ margin: 0 0 15px; white-space: pre-wrap; overflow-wrap: anywhere; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    col.company {{ width: 19%; }} col.item {{ width: 22%; }}
    col.price {{ width: 14%; }} col.requirement {{ width: 45%; }}
    thead {{ display: table-header-group; }}
    th {{ padding: 8px 9px; border: 1px solid #194642; background: #194642; color: #fff; text-align: left; font-size: 9px; }}
    td {{ padding: 8px 9px; border: 1px solid #d0d7d3; vertical-align: top; overflow-wrap: anywhere; word-break: normal; }}
    tbody tr:nth-child(even) {{ background: #f2f5f3; }}
    tr {{ break-inside: avoid; page-break-inside: avoid; }}
    .footnote {{ margin-top: 12px; color: #78817f; font-size: 8px; }}
  </style>
</head>
<body>
  <p class="brand">QUOTE BOT AI</p>
  <h1>Quotation output</h1>
  <p class="meta">Based on {int(comparison_count)} completed comparisons and {int(quote_count)} quotations · {escape(created)}</p>
  <p class="summary-label">Quote AI response</p>
  <p class="summary">{escape(reply)}</p>
  <table aria-label="Quotation comparison">
    <colgroup><col class="company"><col class="item"><col class="price"><col class="requirement"></colgroup>
    <thead><tr>{headers}</tr></thead>
    <tbody>{"".join(body_rows)}</tbody>
  </table>
  {reference_html}
  <p class="footnote">Internal staff reference. Confirm source quotation details before making a purchase decision.</p>
</body>
</html>"""


def build_quote_chat_pdf(reply, items, comparison_count, quote_count, price_reference=None):
    """Render a response summary and the requested four-column quote table."""
    chromium = shutil.which("chromium")
    if not chromium:
        raise QuoteChatPdfError("PDF generation is unavailable because this server has no Chromium renderer.")

    with tempfile.TemporaryDirectory(prefix="quote-ai-pdf-") as directory:
        temp = Path(directory)
        html_path = temp / "report.html"
        pdf_path = temp / "report.pdf"
        html_path.write_text(
            _html_document(reply, items, comparison_count, quote_count, price_reference),
            encoding="utf-8",
        )
        environment = {
            "PATH": f"{Path(chromium).parent}:/usr/bin:/bin",
            "HOME": directory,
            "TMPDIR": directory,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        # Nix fonts are exposed through XDG_DATA_DIRS, not necessarily through
        # /usr/share/fonts. Keep credentials out of the renderer's environment
        # while explicitly registering the packaged CJK font directories.
        font_dirs = [Path(value) / "fonts" for value in
                     os.getenv("XDG_DATA_DIRS", "").split(":") if value]
        font_dirs = [path for path in font_dirs if path.is_dir()]
        if font_dirs:
            font_config = temp / "fonts.conf"
            font_config.write_text(
                '<?xml version="1.0"?><fontconfig>'
                '<include ignore_missing="yes">/etc/fonts/fonts.conf</include>'
                + "".join(f"<dir>{escape(str(path))}</dir>" for path in font_dirs)
                + f"<cachedir>{escape(str(temp / 'font-cache'))}</cachedir></fontconfig>",
                encoding="utf-8",
            )
            environment["FONTCONFIG_FILE"] = str(font_config)
        command = [
            chromium,
            "--headless=new",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--disable-extensions",
            "--disable-crash-reporter",
            "--no-first-run",
            f"--user-data-dir={temp / 'profile'}",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ]
        try:
            result = subprocess.run(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=25, check=False, env=environment,
            )
        except subprocess.TimeoutExpired:
            raise QuoteChatPdfError("PDF generation timed out. Please try the message again.") from None
        except OSError:
            raise QuoteChatPdfError("The server could not start its PDF renderer.") from None
        if result.returncode != 0 or not pdf_path.is_file():
            raise QuoteChatPdfError("The PDF could not be generated. Please try the message again.")
        pdf = pdf_path.read_bytes()
        if not pdf.startswith(b"%PDF-") or len(pdf) > 8_000_000:
            raise QuoteChatPdfError("The PDF renderer returned an invalid document.")
        return pdf
