"""Sleep tracking (spec §4): log bed/wake, compute hours, and detect the
<7h × 3-consecutive-nights escalation trigger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..db import Database
from ..util import date_str, hours_between


@dataclass
class SleepEntry:
    date: str
    bed: str
    wake: str
    hours: float
    below_target: bool


async def log_sleep(
    db: Database, when: datetime, bed: str, wake: str, target_hours: float
) -> SleepEntry:
    hours = hours_between(bed, wake)
    d = date_str(when)
    await db.execute(
        "INSERT INTO sleep_log(ts, date, bed, wake, hours) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(date) DO UPDATE SET bed=excluded.bed, wake=excluded.wake, "
        "hours=excluded.hours, ts=excluded.ts",
        (when.isoformat(timespec="seconds"), d, bed, wake, hours),
    )
    return SleepEntry(d, bed, wake, hours, hours < target_hours)


async def consecutive_short_nights(db: Database, when: datetime, target_hours: float) -> int:
    """Count consecutive nights up to and including ``when`` with hours < target."""
    rows = await db.fetchall(
        "SELECT date, hours FROM sleep_log WHERE date <= ? ORDER BY date DESC LIMIT 14",
        (date_str(when),),
    )
    count = 0
    expected = datetime.fromisoformat(date_str(when)).date()
    for r in rows:
        row_date = datetime.fromisoformat(r["date"]).date()
        if row_date != expected:
            break  # gap in logging → streak ends
        if r["hours"] < target_hours:
            count += 1
            expected = expected - timedelta(days=1)
        else:
            break
    return count


async def avg_hours(db: Database, start: str, end: str) -> float:
    return await db.fetchval(
        "SELECT COALESCE(AVG(hours),0) FROM sleep_log WHERE date BETWEEN ? AND ?",
        (start, end), default=0.0)
