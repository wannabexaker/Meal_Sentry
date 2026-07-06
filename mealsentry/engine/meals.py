"""Meal system: preset access, portion logging, /newmeal macro calc, and the rules
engine (yogurt_bomb weekly cap, salmon reward lock).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..db import Database
from ..util import date_str, week_bounds
from .foods import normalize

YOGURT_BOMB_ID = "yogurt_bomb"
HONEY_VARIANT_NOTE = "Variant 25g μέλι αντί 50g (−~80 kcal)"


class MealError(Exception):
    """Base for meal rule violations (carry a user-facing Greek reason)."""


class MealNotFound(MealError):
    pass


class MealLocked(MealError):
    pass


class MealCapReached(MealError):
    def __init__(self, message: str, alternative: str | None = None):
        super().__init__(message)
        self.alternative = alternative


@dataclass
class Meal:
    id: str
    name: str
    contents: str
    kcal: float
    protein_g: float
    max_per_week: int | None
    locked: bool
    enabled: bool
    tags: str


def _row_to_meal(row) -> Meal:
    return Meal(
        id=row["id"], name=row["name"], contents=row["contents"],
        kcal=row["kcal"], protein_g=row["protein_g"],
        max_per_week=row["max_per_week"], locked=bool(row["locked"]),
        enabled=bool(row["enabled"]), tags=row["tags"],
    )


# Food-DB lookup (find_food / compute_macros) moved to engine/foods.py (now DB-backed).


# --------------------------------------------------------------------------- queries
async def get_meal(db: Database, meal_id: str) -> Meal:
    row = await db.fetchone("SELECT * FROM meals WHERE id = ?", (meal_id,))
    if row is None:
        raise MealNotFound(f"Δεν υπάρχει γεύμα «{meal_id}».")
    return _row_to_meal(row)


async def list_meals(db: Database, *, include_disabled: bool = False) -> list[Meal]:
    sql = "SELECT * FROM meals"
    if not include_disabled:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY locked, name"
    rows = await db.fetchall(sql)
    return [_row_to_meal(r) for r in rows]


async def find_meal(db: Database, query: str) -> Meal | None:
    """Fuzzy-resolve a combo/meal by id, name, or substring (accent-insensitive)."""
    norm = normalize(query)
    rows = await db.fetchall("SELECT * FROM meals WHERE enabled = 1")
    for r in rows:  # exact id or name first
        if r["id"] == query or normalize(r["name"]) == norm:
            return _row_to_meal(r)
    for r in rows:  # then substring either way
        nm = normalize(r["name"])
        if norm and (norm in nm or nm in norm):
            return _row_to_meal(r)
    return None


async def weekly_count(db: Database, meal_id: str, when: datetime) -> int:
    start, end = week_bounds(when)
    return await db.fetchval(
        "SELECT COUNT(*) FROM meal_log WHERE meal_id = ? AND date BETWEEN ? AND ?",
        (meal_id, start, end), default=0,
    )


async def today_totals(db: Database, when: datetime) -> tuple[float, float]:
    """(kcal, protein) consumed on ``when``'s date."""
    row = await db.fetchone(
        "SELECT COALESCE(SUM(kcal),0) k, COALESCE(SUM(protein_g),0) p "
        "FROM meal_log WHERE date = ?",
        (date_str(when),),
    )
    return (row["k"], row["p"])


# --------------------------------------------------------------------------- logging
async def log_meal(db: Database, meal_id: str, when: datetime, fraction: float = 1.0) -> dict:
    """Log a (portion of a) meal after enforcing the rules engine.

    Returns the logged macros. Raises ``MealLocked`` / ``MealCapReached`` on violations.
    """
    meal = await get_meal(db, meal_id)
    if meal.locked:
        raise MealLocked(
            f"🔒 «{meal.name}» είναι κλειδωμένο έπαθλο. Ξεκλειδώνεται με perfect week ή level-up."
        )
    if meal.max_per_week is not None:
        used = await weekly_count(db, meal_id, when)
        if used >= meal.max_per_week:
            raise MealCapReached(
                f"❌ «{meal.name}» {used}/{meal.max_per_week} αυτή τη βδομάδα. Όριο.",
                alternative=HONEY_VARIANT_NOTE,
            )
    fraction = max(0.05, min(fraction, 3.0))
    kcal = round(meal.kcal * fraction, 1)
    protein = round(meal.protein_g * fraction, 1)
    await db.execute(
        "INSERT INTO meal_log(ts, date, meal_id, fraction, kcal, protein_g, note) "
        "VALUES (?, ?, ?, ?, ?, ?, '')",
        (when.isoformat(timespec="seconds"), date_str(when), meal_id, fraction, kcal, protein),
    )
    return {"meal_id": meal_id, "name": meal.name, "fraction": fraction,
            "kcal": kcal, "protein_g": protein}


# --------------------------------------------------------------------------- management
async def add_meal(
    db: Database, meal_id: str, name: str, contents: str,
    kcal: float, protein_g: float, *, tags: str = "custom",
    max_per_week: int | None = None,
) -> Meal:
    await db.execute(
        """INSERT INTO meals(id, name, contents, kcal, protein_g, max_per_week, locked, enabled, tags)
           VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, contents=excluded.contents, kcal=excluded.kcal,
             protein_g=excluded.protein_g, tags=excluded.tags""",
        (meal_id, name, contents, round(kcal, 1), round(protein_g, 1), max_per_week, tags),
    )
    return await get_meal(db, meal_id)


async def set_enabled(db: Database, meal_id: str, enabled: bool) -> None:
    await db.execute("UPDATE meals SET enabled = ? WHERE id = ?", (int(enabled), meal_id))


async def unlock_meal(db: Database, meal_id: str) -> None:
    await db.execute("UPDATE meals SET locked = 0 WHERE id = ?", (meal_id,))


async def duplicate_meal(db: Database, meal_id: str, new_id: str) -> Meal:
    src = await get_meal(db, meal_id)
    return await add_meal(
        db, new_id, f"{src.name} (copy)", src.contents, src.kcal, src.protein_g,
        tags=src.tags, max_per_week=src.max_per_week,
    )
