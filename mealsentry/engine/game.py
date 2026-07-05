"""Gamification: XP, levels, respect meter, per-category streaks, cheat tokens,
boss weeks, and the weekly-report data aggregation.

Loss aversion is intentional (penalties are large). Respect drives the coach tone tier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..db import Database
from ..util import date_str, week_bounds

# --- XP economy (spec §8) ---
XP_REWARDS = {"meal": 10, "protein_floor": 25, "gym": 40, "steps": 15, "sleep": 15, "weigh_in": 5}
XP_PENALTIES = {"failed_task": 15, "ignored_escalation": 25, "failed_week": 50}
RESPECT_REWARD = {"meal": 2, "protein_floor": 5, "gym": 6, "steps": 3, "sleep": 3, "weigh_in": 2}
RESPECT_PENALTY = {"failed_task": 5, "ignored_escalation": 10, "failed_week": 20}
EVENT_CATEGORY = {
    "meal": "meals", "protein_floor": "protein", "gym": "gym",
    "steps": "steps", "sleep": "sleep", "weigh_in": "weigh_in",
}

# --- Levels: 1 Αρχάριος → 10 Σπαρτιάτης, exponential cumulative thresholds ---
LEVELS: list[tuple[int, str]] = [
    (0, "Αρχάριος"), (100, "Δόκιμος"), (250, "Μαχητής"), (460, "Σκληραγωγημένος"),
    (750, "Πολεμιστής"), (1150, "Βετεράνος"), (1700, "Οπλίτης"), (2450, "Λοχαγός"),
    (3500, "Ήρωας"), (5000, "Σπαρτιάτης"),
]
LEVEL_UNLOCKS = {2: "charts", 3: "custom_tone", 4: "cheat_token", 5: "salmon", 6: "boss_week"}

# --- Respect → tone tier ---
RESPECT_LOW = 34
RESPECT_HIGH = 67


def streak_multiplier(count: int) -> float:
    if count >= 21:
        return 2.0
    if count >= 7:
        return 1.5
    return 1.0


def level_for_xp(xp: int) -> tuple[int, str]:
    """Return (level_number, level_name) for a total XP value."""
    level, name = 1, LEVELS[0][1]
    for i, (threshold, lname) in enumerate(LEVELS, start=1):
        if xp >= threshold:
            level, name = i, lname
        else:
            break
    return level, name


def xp_to_next(xp: int) -> int | None:
    """XP remaining to the next level, or None at max level."""
    for threshold, _ in LEVELS:
        if xp < threshold:
            return threshold - xp
    return None


def respect_tier(respect: int) -> str:
    if respect < RESPECT_LOW:
        return "LOW"
    if respect >= RESPECT_HIGH:
        return "HIGH"
    return "MID"


@dataclass
class AwardResult:
    event: str
    xp_delta: int
    xp_total: int
    level: int
    level_name: str
    level_up: bool
    respect: int
    unlocks: list[str] = field(default_factory=list)


async def _state_row(db: Database):
    return await db.fetchone("SELECT * FROM game_state WHERE id = 1")


async def get_state(db: Database) -> dict:
    row = await _state_row(db)
    level, name = level_for_xp(row["xp"])
    return {
        "xp": row["xp"], "level": level, "level_name": name,
        "xp_to_next": xp_to_next(row["xp"]), "respect": row["respect"],
        "respect_tier": respect_tier(row["respect"]),
        "cheat_tokens": row["cheat_tokens"], "boss_week": bool(row["boss_week"]),
    }


async def update_streak(db: Database, category: str, when: datetime, hit: bool) -> int:
    """Advance or reset a per-category streak; returns the new count."""
    row = await db.fetchone("SELECT count, best, last_date FROM streaks WHERE category = ?",
                            (category,))
    if row is None:
        await db.execute("INSERT OR IGNORE INTO streaks(category, count, best) VALUES(?,0,0)",
                         (category,))
        row = await db.fetchone("SELECT count, best, last_date FROM streaks WHERE category = ?",
                                (category,))
    today = date_str(when)
    if not hit:
        await db.execute("UPDATE streaks SET count = 0, last_date = ? WHERE category = ?",
                         (today, category))
        return 0
    if row["last_date"] == today:
        return row["count"]  # already counted today
    new_count = row["count"] + 1
    best = max(row["best"], new_count)
    await db.execute("UPDATE streaks SET count = ?, best = ?, last_date = ? WHERE category = ?",
                     (new_count, best, today, category))
    return new_count


async def award(db: Database, event: str, when: datetime, *, count_streak: bool = True) -> AwardResult:
    """Grant XP for a positive event, applying streak + boss-week multipliers."""
    row = await _state_row(db)
    old_xp, respect, boss = row["xp"], row["respect"], row["boss_week"]
    old_level, _ = level_for_xp(old_xp)

    base = XP_REWARDS.get(event, 0)
    mult = 1.0
    if count_streak and event in EVENT_CATEGORY:
        streak = await update_streak(db, EVENT_CATEGORY[event], when, hit=True)
        mult = streak_multiplier(streak)
    if boss:
        mult *= 2.0
    xp_delta = int(round(base * mult))
    new_xp = old_xp + xp_delta
    new_respect = _clamp(respect + RESPECT_REWARD.get(event, 1))

    new_level, level_name = level_for_xp(new_xp)
    unlocks: list[str] = []
    if new_level > old_level:
        for lvl in range(old_level + 1, new_level + 1):
            if lvl in LEVEL_UNLOCKS:
                unlocks.append(LEVEL_UNLOCKS[lvl])

    extra_tokens = 1 if "cheat_token" in unlocks else 0
    await db.execute(
        "UPDATE game_state SET xp = ?, level = ?, respect = ?, "
        "cheat_tokens = cheat_tokens + ?, updated_at = ? WHERE id = 1",
        (new_xp, new_level, new_respect, extra_tokens, when.isoformat(timespec="seconds")),
    )
    return AwardResult(event, xp_delta, new_xp, new_level, level_name,
                       new_level > old_level, new_respect, unlocks)


async def penalize(db: Database, event: str, when: datetime) -> AwardResult:
    """Subtract XP for a failure and drop respect. XP floored at 0."""
    row = await _state_row(db)
    old_xp, respect = row["xp"], row["respect"]
    xp_delta = -XP_PENALTIES.get(event, 0)
    new_xp = max(0, old_xp + xp_delta)
    new_respect = _clamp(respect - RESPECT_PENALTY.get(event, 5))
    new_level, level_name = level_for_xp(new_xp)
    # a failure breaks the relevant streak
    if event == "failed_task":
        pass  # streak reset handled by the specific category caller when known
    await db.execute(
        "UPDATE game_state SET xp = ?, level = ?, respect = ?, updated_at = ? WHERE id = 1",
        (new_xp, new_level, new_respect, when.isoformat(timespec="seconds")),
    )
    return AwardResult(event, new_xp - old_xp, new_xp, new_level, level_name,
                       False, new_respect)


async def break_streak(db: Database, category: str, when: datetime) -> None:
    await update_streak(db, category, when, hit=False)


async def spend_cheat_token(db: Database) -> bool:
    row = await _state_row(db)
    if row["cheat_tokens"] <= 0:
        return False
    await db.execute("UPDATE game_state SET cheat_tokens = cheat_tokens - 1 WHERE id = 1")
    return True


async def grant_cheat_token(db: Database, n: int = 1) -> None:
    await db.execute("UPDATE game_state SET cheat_tokens = cheat_tokens + ? WHERE id = 1", (n,))


async def set_boss_week(db: Database, active: bool) -> None:
    await db.execute("UPDATE game_state SET boss_week = ? WHERE id = 1", (int(active),))


async def get_streaks(db: Database) -> dict[str, dict]:
    rows = await db.fetchall("SELECT category, count, best FROM streaks ORDER BY category")
    return {r["category"]: {"count": r["count"], "best": r["best"]} for r in rows}


def _clamp(value: int, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, value))


# --------------------------------------------------------------------------- reporting
async def weekly_report_data(db: Database, when: datetime) -> dict:
    """Aggregate the ruthless numbers for the Sunday 22:00 weekly report."""
    start, end = week_bounds(when)

    done = await db.fetchval(
        "SELECT COUNT(*) FROM tasks WHERE date BETWEEN ? AND ? AND state = 'DONE'",
        (start, end), default=0)
    failed = await db.fetchval(
        "SELECT COUNT(*) FROM tasks WHERE date BETWEEN ? AND ? AND state = 'FAILED'",
        (start, end), default=0)
    total = done + failed
    adherence = round(100.0 * done / total, 1) if total else 0.0

    gym_sessions = await db.fetchval(
        "SELECT COUNT(*) FROM gym_log WHERE date BETWEEN ? AND ?", (start, end), default=0)

    avg_protein = await db.fetchval(
        "SELECT AVG(daily) FROM (SELECT SUM(protein_g) daily FROM meal_log "
        "WHERE date BETWEEN ? AND ? GROUP BY date)", (start, end)) or 0.0

    weight_rows = await db.fetchall(
        "SELECT date, AVG(kg) kg FROM weight_log WHERE date BETWEEN ? AND ? "
        "GROUP BY date ORDER BY date", (start, end))
    weight_points = [(r["date"], round(r["kg"], 2)) for r in weight_rows]

    ignored = await db.fetchval(
        "SELECT COUNT(*) FROM warnings WHERE date BETWEEN ? AND ? AND level >= 3",
        (start, end), default=0)

    state = await get_state(db)
    streaks = await get_streaks(db)

    # delta since last report (snapshot in kv)
    prev_xp = int(await db.kv_get("report_prev_xp", "0"))
    prev_respect = int(await db.kv_get("report_prev_respect", "50"))
    await db.kv_set("report_prev_xp", str(state["xp"]))
    await db.kv_set("report_prev_respect", str(state["respect"]))

    return {
        "week_start": start, "week_end": end,
        "adherence_pct": adherence, "tasks_done": done, "tasks_failed": failed,
        "gym_sessions": gym_sessions, "gym_target": 3,
        "avg_protein": round(avg_protein, 1),
        "weight_points": weight_points,
        "ignored_warnings": ignored,
        "xp": state["xp"], "xp_delta": state["xp"] - prev_xp,
        "level": state["level"], "level_name": state["level_name"],
        "respect": state["respect"], "respect_delta": state["respect"] - prev_respect,
        "streaks": streaks,
    }
