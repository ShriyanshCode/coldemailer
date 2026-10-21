"""Time helpers. Storage is UTC text; reporting uses IST days; windows use the prospect's zone."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")
FMT = "%Y-%m-%dT%H:%M:%SZ"


@lru_cache(maxsize=64)
def zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def to_ts(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime(FMT)


def from_ts(s: str) -> datetime:
    return datetime.strptime(s, FMT).replace(tzinfo=UTC)


def add(ts: str, **kwargs) -> str:
    return to_ts(from_ts(ts) + timedelta(**kwargs))


def seconds_between(a: str, b: str) -> float:
    return (from_ts(b) - from_ts(a)).total_seconds()


def ist_day(ts: str) -> str:
    return from_ts(ts).astimezone(IST).strftime("%Y-%m-%d")


def ist_day_bounds(day: str) -> tuple[str, str]:
    """UTC [start, end) of an IST calendar day."""
    d = date.fromisoformat(day)
    start = datetime.combine(d, time(0, 0), tzinfo=IST)
    return to_ts(start), to_ts(start + timedelta(days=1))


def shift_day(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def fmt_ist(ts: str | None) -> str | None:
    if not ts:
        return None
    return from_ts(ts).astimezone(IST).strftime("%b %d, %H:%M")


def local(ts: str, tz: str) -> datetime:
    return from_ts(ts).astimezone(zone(tz))


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def window_minutes(start: str = "09:00", end: str = "18:00") -> int:
    a, b = _hm(start), _hm(end)
    return (b.hour * 60 + b.minute) - (a.hour * 60 + a.minute)


def in_window(ts: str, tz: str, start: str = "09:00", end: str = "18:00") -> bool:
    t = local(ts, tz).time()
    return _hm(start) <= t < _hm(end)


def window_time_on(local_date: date, tz: str, minute_of_window: int,
                   start: str = "09:00", end: str = "18:00") -> str:
    """UTC timestamp of `minute_of_window` minutes after the window opens on a local date."""
    minute_of_window = max(0, min(minute_of_window, window_minutes(start, end) - 1))
    open_dt = datetime.combine(local_date, _hm(start), tzinfo=zone(tz))
    return to_ts(open_dt + timedelta(minutes=minute_of_window))


def window_end_on(local_date: date, tz: str, end: str = "18:00") -> str:
    return to_ts(datetime.combine(local_date, _hm(end), tzinfo=zone(tz)))


def next_window_open(ts: str, tz: str, start: str = "09:00", end: str = "18:00") -> str:
    """ts itself if inside the window, else the next time the prospect's window opens."""
    if in_window(ts, tz, start, end):
        return ts
    loc = local(ts, tz)
    d = loc.date() if loc.time() < _hm(start) else loc.date() + timedelta(days=1)
    return window_time_on(d, tz, 0, start, end)
