import re
import threading
import time
from collections.abc import Callable
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


_BUFFER = timedelta(minutes=2)


def compute_wake_time(reset_time: datetime | None, now: datetime) -> datetime:
    if reset_time is not None:
        return reset_time + _BUFFER
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return next_hour + _BUFFER


class QuotaWall:
    def __init__(
        self,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._now = now_fn
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep
        self._cond = threading.Condition()
        self._wake_time: datetime | None = None

    def raise_wall(self, reset_time: datetime) -> bool:
        wake = reset_time + _BUFFER
        with self._cond:
            if self._wake_time is not None and self._now() < self._wake_time:
                if wake > self._wake_time:
                    self._wake_time = wake
                    self._cond.notify_all()
                return False
            self._wake_time = wake
            self._cond.notify_all()
            return True

    def wait_if_blocked(self) -> None:
        while True:
            with self._cond:
                if self._wake_time is None or self._now() >= self._wake_time:
                    self._wake_time = None
                    self._cond.notify_all()
                    return
                deadline = self._wake_time
            remaining = (deadline - self._now()).total_seconds()
            if remaining > 0:
                self._sleep(remaining)

    def is_active(self) -> bool:
        with self._cond:
            return self._wake_time is not None and self._now() < self._wake_time
