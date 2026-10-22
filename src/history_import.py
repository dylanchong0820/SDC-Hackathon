"""Parse text-only WhatsApp chat exports for safe, offline history import."""
from datetime import datetime
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


MAX_IMPORT_MESSAGES = 50_000
MAX_MESSAGE_CHARS = 65_536
_EXPORT_LINE = re.compile(
    r"^(?:[\u200e\u200f])?\[?(?P<date>\d{1,4}[./-]\d{1,2}[./-]\d{2,4}),\s*"
    r"(?P<clock>\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp][Mm])?)\]?\s*"
    r"(?:-\s*)?(?P<text>.*)$"
)


class HistoryImportError(ValueError):
    """An export cannot be safely interpreted as a one-to-one text history."""


def _timestamp(date_value, clock_value, date_order, timezone):
    parts = re.split(r"[./-]", date_value)
    if len(parts) != 3:
        raise ValueError("Invalid date")

    if len(parts[0]) == 4:
        year, month, day = (int(part) for part in parts)
    else:
        first, second, year = (int(part) for part in parts)
        if len(parts[2]) == 2:
            year += 2000 if year < 70 else 1900
        if first > 12:
            day, month = first, second
        elif second > 12:
            month, day = first, second
        elif date_order == "MDY":
            month, day = first, second
        else:
            day, month = first, second

    clock = clock_value.strip().upper()
    if "AM" in clock or "PM" in clock:
        compact = re.sub(r"\s+", "", clock)
        fmt = "%I:%M:%S%p" if compact.count(":") == 2 else "%I:%M%p"
    else:
        fmt = "%H:%M:%S" if clock.count(":") == 2 else "%H:%M"
        compact = clock
    parsed_clock = datetime.strptime(compact, fmt).time()
    return int(datetime(year, month, day, parsed_clock.hour, parsed_clock.minute,
                        parsed_clock.second, tzinfo=timezone).timestamp())


def parse_whatsapp_export(content, date_order="DMY", timezone_name="Asia/Kuala_Lumpur",
                          business_name=""):
    if date_order not in {"DMY", "MDY", "YMD"}:
        raise HistoryImportError("Choose a supported date order.")
    if not isinstance(timezone_name, str) or len(timezone_name) > 80 or not re.fullmatch(
            r"[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*", timezone_name):
        raise HistoryImportError("Enter a valid time zone, such as Asia/Kuala_Lumpur.")
    try:
        timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise HistoryImportError("That time zone is not available. Check its spelling.") from None

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HistoryImportError("The export must be a UTF-8 WhatsApp .txt file.") from None

    own_names = {"you", "me"}
    if business_name.strip():
        own_names.add(business_name.strip().casefold())
    messages = []
    senders = set()
    current = None
    skipped = 0

    def finish():
        nonlocal current, skipped
        if current is None:
            return
        body = "\n".join(current["lines"]).strip("\n").replace("\x00", "")
        if not body:
            skipped += 1
            current = None
            return
        if len(body) > MAX_MESSAGE_CHARS:
            raise HistoryImportError("A message in this export is too large to import.")
        author = current["author"].strip().strip("\u200e\u200f")
        normalized_author = author.casefold()
        if author and normalized_author not in own_names and normalized_author != "whatsapp":
            senders.add(author)
        messages.append({
            "timestamp": current["timestamp"],
            "direction": "outbound" if normalized_author in own_names else "inbound",
            "body": body,
            "author": author,
        })
        current = None

    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip("\u200e\u200f")
        match = _EXPORT_LINE.match(line)
        if match:
            finish()
            try:
                timestamp = _timestamp(match["date"], match["clock"], date_order, timezone)
            except ValueError:
                skipped += 1
                continue
            rest = match["text"].lstrip()
            author, separator, body = rest.partition(": ")
            if not separator:
                author, body = "", rest
            current = {"timestamp": timestamp, "author": author, "lines": [body]}
        elif current is not None:
            current["lines"].append(line)
        else:
            skipped += 1
        if len(messages) > MAX_IMPORT_MESSAGES:
            raise HistoryImportError(
                f"An export can contain at most {MAX_IMPORT_MESSAGES:,} messages."
            )
    finish()
    if len(messages) > MAX_IMPORT_MESSAGES:
        raise HistoryImportError(
            f"An export can contain at most {MAX_IMPORT_MESSAGES:,} messages."
        )
    if not messages:
        raise HistoryImportError(
            "No WhatsApp messages were recognized. Use WhatsApp's plain-text chat export."
        )
    if len(senders) > 1:
        raise HistoryImportError(
            "This export has multiple message senders. Import a one-to-one chat, not a group."
        )
    return {
        "messages": messages,
        "contact_sender": next(iter(senders), ""),
        "skipped_lines": skipped,
    }
