"""🔔 Data-driven notification management (Feature B).

Read/write helpers for the ``notif_config`` table plus the ``is_active`` gate the
scheduler consults at fire-time. Semantics per project spec:

* ``enabled`` — off means the schedule slot is not registered at boot (retime + enable
  apply after a restart). ``is_active`` also returns ``False`` when disabled so a stray
  callback path still skips.
* ``muted`` — runtime skip: the cron job stays registered but the notifier no-ops.

Unknown keys return ``True`` so ad-hoc situations (``shopping_countdown``,
``gym_lastcall``, etc.) are never silenced by accident.
"""

from __future__ import annotations

from ..db import Database
from ..util import parse_hhmm


async def list_notifs(db: Database) -> list[dict]:
    rows = await db.fetchall(
        "SELECT key, label, time, enabled, muted FROM notif_config ORDER BY key")
    return [
        {"key": r["key"], "label": r["label"], "time": r["time"],
         "enabled": bool(r["enabled"]), "muted": bool(r["muted"])}
        for r in rows
    ]


async def get_notif(db: Database, key: str) -> dict | None:
    row = await db.fetchone(
        "SELECT key, label, time, enabled, muted FROM notif_config WHERE key = ?", (key,))
    if row is None:
        return None
    return {"key": row["key"], "label": row["label"], "time": row["time"],
            "enabled": bool(row["enabled"]), "muted": bool(row["muted"])}


async def set_enabled(db: Database, key: str, enabled: bool) -> None:
    await db.execute(
        "UPDATE notif_config SET enabled = ? WHERE key = ?",
        (1 if enabled else 0, key),
    )


async def set_muted(db: Database, key: str, muted: bool) -> None:
    await db.execute(
        "UPDATE notif_config SET muted = ? WHERE key = ?",
        (1 if muted else 0, key),
    )


async def set_time(db: Database, key: str, time_str: str) -> None:
    """Validate 'HH:MM' (or literal 'random') before storing."""
    clean = time_str.strip()
    if clean.lower() != "random":
        hh, mm = parse_hhmm(clean)   # raises ValueError on bad input
        clean = f"{hh:02d}:{mm:02d}"
    await db.execute(
        "UPDATE notif_config SET time = ? WHERE key = ?",
        (clean, key),
    )


async def is_active(db: Database, key: str) -> bool:
    """Return ``True`` when the notification should actually fire.

    Unknown keys default to active so the scheduler's ad-hoc pings (e.g. shopping
    countdown, gym last-call) are never silenced through configuration errors.
    """
    row = await db.fetchone(
        "SELECT enabled, muted FROM notif_config WHERE key = ?", (key,))
    if row is None:
        return True
    return bool(row["enabled"]) and not bool(row["muted"])


async def get_time(db: Database, key: str, default_hour: int, default_minute: int) -> tuple[int, int]:
    """Return the configured (hour, minute) for ``key`` or the caller's defaults.

    Used by the scheduler at boot to build cron triggers from the DB rather than a
    hardcoded schedule. 'random' or an unparseable value falls back to the defaults.
    """
    row = await db.fetchone("SELECT time FROM notif_config WHERE key = ?", (key,))
    if row is None or not row["time"] or row["time"].lower() == "random":
        return default_hour, default_minute
    try:
        return parse_hhmm(row["time"])
    except ValueError:
        return default_hour, default_minute


async def is_enabled_default(db: Database, key: str) -> bool:
    """Startup gate (does this slot's cron get registered at all?). Unknown keys → True
    so a code path referencing a not-yet-seeded key still schedules."""
    row = await db.fetchone("SELECT enabled FROM notif_config WHERE key = ?", (key,))
    return True if row is None else bool(row["enabled"])
