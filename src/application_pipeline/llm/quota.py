import re
from datetime import datetime, timedelta, timezone

_MONTHS: dict[str, int] = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

_RESET_RE = re.compile(
    r"resets\s+"
    r"(?:([A-Za-z]+)\s+(\d{1,2}),\s*)?"
    r"(\d{1,2})"
    r"(?::(\d{2}))?"
    r"(am|pm)\s*\(UTC\)",
    re.IGNORECASE,
)


def parse_reset_time(result_text: str) -> datetime | None:
    if not result_text:
        return None
    m = _RESET_RE.search(result_text)
    if m is None:
        return None

    month_name, day_str, hour_str, minute_str, ampm = m.groups()
    now_utc = datetime.now(timezone.utc)

    hour = int(hour_str)
    minute = int(minute_str) if minute_str else 0

    if ampm.lower() == "pm" and hour != 12:
        hour += 12
    elif ampm.lower() == "am" and hour == 12:
        hour = 0

    if month_name is not None:
        month_num = _MONTHS.get(month_name.lower())
        if month_num is None:
            return None
        try:
            day = int(day_str)
            dt = datetime(
                now_utc.year, month_num, day, hour, minute, tzinfo=timezone.utc
            )
        except ValueError:
            return None
        if dt < now_utc:
            try:
                dt = datetime(
                    now_utc.year + 1, month_num, day, hour, minute, tzinfo=timezone.utc
                )
            except ValueError:
                return None
    else:
        try:
            dt = datetime(
                now_utc.year,
                now_utc.month,
                now_utc.day,
                hour,
                minute,
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None

    return dt


def compute_wake_time(reset_time: datetime | None, now: datetime) -> datetime:
    if reset_time is not None:
        return reset_time + timedelta(minutes=2)
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return next_hour + timedelta(minutes=2)
