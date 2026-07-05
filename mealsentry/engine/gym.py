"""Gym pressure engine (spec §5): weekly target 3 sessions, rolling pressure that
escalates each weekday the flexible session is still missing, Friday last-call, and the
Sunday weekly verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..db import Database
from ..util import date_str, week_bounds

WEEKLY_TARGET = 3
# pressure by weekday (Mon=0..Sun=6) when no weekday/flex session logged yet
_PRESSURE_BY_WEEKDAY = [1, 1, 2, 2, 3, 0, 0]


async def log_session(db: Database, when: datetime, minutes: int = 60) -> int:
    """Record a gym session; returns the session count for the current week."""
    await db.execute(
        "INSERT INTO gym_log(ts, date, minutes) VALUES (?, ?, ?)",
        (when.isoformat(timespec="seconds"), date_str(when), max(1, minutes)),
    )
    return await sessions_this_week(db, when)


async def sessions_this_week(db: Database, when: datetime) -> int:
    start, end = week_bounds(when)
    return await db.fetchval(
        "SELECT COUNT(*) FROM gym_log WHERE date BETWEEN ? AND ?", (start, end), default=0)


async def minutes_this_week(db: Database, when: datetime) -> int:
    start, end = week_bounds(when)
    return await db.fetchval(
        "SELECT COALESCE(SUM(minutes),0) FROM gym_log WHERE date BETWEEN ? AND ?",
        (start, end), default=0)


async def weekday_session_logged(db: Database, when: datetime) -> bool:
    """True if a Mon–Fri (flex) session exists this week."""
    monday, _ = week_bounds(when)
    monday_d = datetime.fromisoformat(monday).date()
    friday = (monday_d + timedelta(days=4)).isoformat()
    n = await db.fetchval(
        "SELECT COUNT(*) FROM gym_log WHERE date BETWEEN ? AND ?", (monday, friday), default=0)
    return n > 0


async def compute_pressure(db: Database, when: datetime) -> int:
    """0..3 pressure level for the still-missing flexible weekday session."""
    if await weekday_session_logged(db, when):
        return 0
    return _PRESSURE_BY_WEEKDAY[when.weekday()]


def pings_per_day(pressure: int) -> int:
    if pressure >= 3:
        return 3
    if pressure == 2:
        return 2
    return 1


def is_last_call(when: datetime) -> bool:
    """Friday = last-call mode (pings at 12:00/15:00/17:00 scheduled elsewhere)."""
    return when.weekday() == 4


@dataclass
class Verdict:
    sessions: int
    target: int
    minutes: int
    prev_sessions: int
    delta: int
    hit_target: bool


async def weekly_verdict(db: Database, when: datetime) -> Verdict:
    """Sunday 22:00 blunt numbers: sessions X/3 vs previous week."""
    sessions = await sessions_this_week(db, when)
    minutes = await minutes_this_week(db, when)
    prev_when = when - timedelta(days=7)
    prev = await sessions_this_week(db, prev_when)
    return Verdict(
        sessions=sessions, target=WEEKLY_TARGET, minutes=minutes,
        prev_sessions=prev, delta=sessions - prev, hit_target=sessions >= WEEKLY_TARGET,
    )
