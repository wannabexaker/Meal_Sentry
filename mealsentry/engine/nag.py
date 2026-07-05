"""Nag engine (core): per-task state machine with escalation and receipts.

State machine per (date, task_key):

    PENDING → NAGGED_1 → NAGGED_2 → NAGGED_3 → FAILED
                                              ↘ (any non-terminal) → DONE

Every ping is written to the ``warnings`` table with a timestamp. Those rows are the
*receipts*: on failure the bot quotes the exact times it warned the user.

This module is tone-agnostic and Telegram-agnostic. ``advance()`` transitions the state
and reports *what* should happen; the caller renders the message via the active coach and
calls ``record_warning()`` to persist the receipt.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..db import Database
from ..util import date_str

PENDING = "PENDING"
NAGGED_1 = "NAGGED_1"
NAGGED_2 = "NAGGED_2"
NAGGED_3 = "NAGGED_3"
DONE = "DONE"
FAILED = "FAILED"

TERMINAL = {DONE, FAILED}
NEXT_STATE = {PENDING: NAGGED_1, NAGGED_1: NAGGED_2, NAGGED_2: NAGGED_3, NAGGED_3: FAILED}
_PING_LEVEL = {NAGGED_1: 1, NAGGED_2: 2, NAGGED_3: 3}

ESCALATE_AFTER_MIN = 30


@dataclass
class NagResult:
    task_key: str
    notify: bool
    kind: str              # "ping" | "failed" | "noop"
    level: int             # 1..3 (0 for noop)
    new_state: str
    terminal: bool
    warn_times: list[str] = field(default_factory=list)   # "HH:MM" receipts

    @property
    def receipts_text(self) -> str:
        return ", ".join(self.warn_times)


async def ensure_task(
    db: Database, task_key: str, when: datetime, *, meta: dict | None = None
) -> None:
    """Create a PENDING task for ``when``'s date if it does not exist yet."""
    d = date_str(when)
    exists = await db.fetchval(
        "SELECT 1 FROM tasks WHERE date = ? AND task_key = ?", (d, task_key)
    )
    if exists:
        return
    await db.execute(
        "INSERT INTO tasks(date, task_key, state, due_ts, next_ts, nag_count, meta) "
        "VALUES (?, ?, 'PENDING', ?, ?, 0, ?)",
        (d, task_key, when.isoformat(timespec="seconds"),
         when.isoformat(timespec="seconds"), json.dumps(meta or {})),
    )


async def _get(db: Database, task_key: str, when: datetime):
    return await db.fetchone(
        "SELECT * FROM tasks WHERE date = ? AND task_key = ?", (date_str(when), task_key)
    )


async def advance(
    db: Database, task_key: str, when: datetime, *, escalate_after_min: int = ESCALATE_AFTER_MIN
) -> NagResult:
    """Advance the task one escalation step. Creates it (→ first ping) if absent."""
    await ensure_task(db, task_key, when)
    row = await _get(db, task_key, when)
    state = row["state"]
    if state in TERMINAL:
        return NagResult(task_key, notify=False, kind="noop", level=0,
                         new_state=state, terminal=True)

    new_state = NEXT_STATE[state]
    d = date_str(when)
    if new_state == FAILED:
        await db.execute(
            "UPDATE tasks SET state = ?, next_ts = NULL, done_ts = ? "
            "WHERE date = ? AND task_key = ?",
            (FAILED, when.isoformat(timespec="seconds"), d, task_key),
        )
        times = await warning_times(db, task_key, when)
        return NagResult(task_key, notify=True, kind="failed", level=3,
                         new_state=FAILED, terminal=True, warn_times=times)

    level = _PING_LEVEL[new_state]
    next_ts = (when + timedelta(minutes=escalate_after_min)).isoformat(timespec="seconds")
    await db.execute(
        "UPDATE tasks SET state = ?, nag_count = ?, next_ts = ? "
        "WHERE date = ? AND task_key = ?",
        (new_state, level, next_ts, d, task_key),
    )
    return NagResult(task_key, notify=True, kind="ping", level=level,
                     new_state=new_state, terminal=False)


async def confirm(db: Database, task_key: str, when: datetime) -> bool:
    """Mark a task DONE. Returns True if it changed (was open)."""
    row = await _get(db, task_key, when)
    if row is None:
        # allow confirming a task the scheduler has not created yet (early confirm)
        await ensure_task(db, task_key, when)
        row = await _get(db, task_key, when)
    if row["state"] in TERMINAL:
        return False
    await db.execute(
        "UPDATE tasks SET state = 'DONE', next_ts = NULL, done_ts = ? "
        "WHERE date = ? AND task_key = ?",
        (when.isoformat(timespec="seconds"), date_str(when), task_key),
    )
    return True


async def snooze(db: Database, task_key: str, when: datetime, minutes: int = 30) -> None:
    """Delay the next escalation without advancing the level."""
    next_ts = (when + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    await db.execute(
        "UPDATE tasks SET next_ts = ? WHERE date = ? AND task_key = ? "
        "AND state NOT IN ('DONE','FAILED')",
        (next_ts, date_str(when), task_key),
    )


async def record_warning(
    db: Database, task_key: str, when: datetime, level: int, text: str
) -> None:
    """Persist a ping as a receipt."""
    await db.execute(
        "INSERT INTO warnings(ts, date, task_key, level, text) VALUES (?, ?, ?, ?, ?)",
        (when.isoformat(timespec="seconds"), date_str(when), task_key, level, text),
    )


async def warning_times(db: Database, task_key: str, when: datetime) -> list[str]:
    """The 'HH:MM' times we pinged for this task today (for receipts)."""
    rows = await db.fetchall(
        "SELECT ts FROM warnings WHERE date = ? AND task_key = ? ORDER BY ts",
        (date_str(when), task_key),
    )
    out = []
    for r in rows:
        try:
            out.append(datetime.fromisoformat(r["ts"]).strftime("%H:%M"))
        except ValueError:
            continue
    return out


async def due_for_escalation(db: Database, now: datetime) -> list[str]:
    """task_keys whose escalation timer has elapsed (used by the scheduler tick + boot
    recovery so a reboot never drops an in-flight escalation)."""
    rows = await db.fetchall(
        "SELECT task_key FROM tasks "
        "WHERE date = ? AND state NOT IN ('DONE','FAILED') "
        "AND next_ts IS NOT NULL AND next_ts <= ?",
        (date_str(now), now.isoformat(timespec="seconds")),
    )
    return [r["task_key"] for r in rows]


async def open_tasks(db: Database, when: datetime) -> list[dict]:
    rows = await db.fetchall(
        "SELECT task_key, state, nag_count, next_ts FROM tasks "
        "WHERE date = ? AND state NOT IN ('DONE','FAILED')",
        (date_str(when),),
    )
    return [dict(r) for r in rows]


async def day_summary(db: Database, when: datetime) -> dict[str, int]:
    """Counts of DONE/FAILED/open for the day (for reports)."""
    rows = await db.fetchall(
        "SELECT state, COUNT(*) n FROM tasks WHERE date = ? GROUP BY state",
        (date_str(when),),
    )
    counts = {r["state"]: r["n"] for r in rows}
    return {
        "done": counts.get(DONE, 0),
        "failed": counts.get(FAILED, 0),
        "open": sum(v for k, v in counts.items() if k not in TERMINAL),
    }
