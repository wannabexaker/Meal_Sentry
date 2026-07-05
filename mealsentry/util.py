"""Small date/parsing helpers shared across engine modules."""

from __future__ import annotations

from datetime import date, datetime, timedelta


def to_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def date_str(value: str | date | datetime) -> str:
    return to_date(value).isoformat()


def week_bounds(value: str | date | datetime) -> tuple[str, str]:
    """Monday..Sunday (inclusive) ISO date strings for the week containing ``value``."""
    d = to_date(value)
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def month_bounds(value: str | date | datetime) -> tuple[str, str]:
    """First..last ISO date strings for the month containing ``value``."""
    d = to_date(value)
    first = d.replace(day=1)
    if d.month == 12:
        next_first = first.replace(year=d.year + 1, month=1)
    else:
        next_first = first.replace(month=d.month + 1)
    last = next_first - timedelta(days=1)
    return first.isoformat(), last.isoformat()


def parse_hhmm(text: str) -> tuple[int, int]:
    """Parse 'HH:MM' (or 'H.MM') into (hour, minute); raises ValueError on garbage."""
    cleaned = text.strip().replace(".", ":")
    hh, mm = cleaned.split(":")
    hour, minute = int(hh), int(mm)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time: {text}")
    return hour, minute


def hours_between(bed: str, wake: str) -> float:
    """Sleep duration in hours from bed→wake 'HH:MM', handling past-midnight wrap."""
    bh, bm = parse_hhmm(bed)
    wh, wm = parse_hhmm(wake)
    start = bh * 60 + bm
    end = wh * 60 + wm
    if end <= start:  # crossed midnight
        end += 24 * 60
    return round((end - start) / 60.0, 2)
