"""Inventory, burn-rate prediction, auto shopping list, and spend tracking (spec §7).

Burn-rate is derived from logged meals via a meal→ingredient consumption map (grams per
full portion). Shopping runs are Tue 18:15 and Sat morning; Sundays the store is closed,
which is baked into every prediction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..db import Database
from ..util import date_str, month_bounds

# Grams of a tracked inventory item consumed per FULL portion of a meal.
# Keys on the right are the free-form labels used with /stock (e.g. /stock chicken 800).
MEAL_CONSUMPTION: dict[str, dict[str, float]] = {
    "chicken": {"chicken": 200},
    "beef": {"beef": 200},
    "pork": {"pork": 200},
    "omelette": {"eggs": 250, "cottage": 100, "bacon": 40},
    "oats": {"oats": 80},
    "shake": {"whey": 30},
    "cottage_lazy": {"cottage": 200},
    "yogurt_bomb": {"yogurt": 200},
    "yogurt_snack": {"yogurt": 200},
    "tuna_lazy": {"tuna": 150, "cottage": 100},
    "salmon": {"salmon": 250},
}

# Shopping runs: weekday → label. Tuesday=1, Saturday=5 (Mon=0).
SHOP_DAYS = {1: "18:15", 5: "πρωί"}
DEFAULT_HORIZON_DAYS = 4


async def set_stock(db: Database, item: str, grams: float, when: datetime) -> float:
    item = item.strip().lower()
    await db.execute(
        "INSERT INTO inventory(item, grams, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(item) DO UPDATE SET grams = excluded.grams, updated_at = excluded.updated_at",
        (item, max(0.0, grams), when.isoformat(timespec="seconds")),
    )
    return grams


async def add_stock(db: Database, item: str, grams: float, when: datetime) -> float:
    item = item.strip().lower()
    current = await db.fetchval("SELECT grams FROM inventory WHERE item = ?", (item,), default=0.0)
    return await set_stock(db, item, (current or 0.0) + grams, when)


async def get_all_stock(db: Database) -> dict[str, float]:
    rows = await db.fetchall("SELECT item, grams FROM inventory ORDER BY item")
    return {r["item"]: r["grams"] for r in rows}


async def burn_rate_per_day(
    db: Database, item: str, when: datetime, lookback_days: int = 14
) -> float:
    """Average grams/day of ``item`` consumed, inferred from logged meals."""
    start = (datetime.fromisoformat(date_str(when)).date()
             - timedelta(days=lookback_days - 1)).isoformat()
    rows = await db.fetchall(
        "SELECT meal_id, fraction FROM meal_log WHERE date BETWEEN ? AND ?",
        (start, date_str(when)),
    )
    total = 0.0
    for r in rows:
        per_portion = MEAL_CONSUMPTION.get(r["meal_id"], {}).get(item, 0.0)
        total += per_portion * r["fraction"]
    return round(total / lookback_days, 1)


@dataclass
class RunoutPrediction:
    item: str
    stock_g: float
    burn_per_day: float
    days_left: float | None
    runout_date: str | None


async def predict_runout(db: Database, item: str, when: datetime) -> RunoutPrediction:
    item = item.strip().lower()
    stock = await db.fetchval("SELECT grams FROM inventory WHERE item = ?", (item,), default=0.0)
    burn = await burn_rate_per_day(db, item, when)
    if burn <= 0:
        return RunoutPrediction(item, stock or 0.0, burn, None, None)
    days = round((stock or 0.0) / burn, 1)
    runout = (datetime.fromisoformat(date_str(when)).date() + timedelta(days=int(days))).isoformat()
    return RunoutPrediction(item, stock or 0.0, burn, days, runout)


def upcoming_runs(when: datetime, n: int = 2) -> list[tuple[str, str]]:
    """The next ``n`` shopping-run (date, label) pairs, including today if applicable."""
    out: list[tuple[str, str]] = []
    d = datetime.fromisoformat(date_str(when)).date()
    for offset in range(0, 14):
        day = d + timedelta(days=offset)
        if day.weekday() in SHOP_DAYS:
            out.append((day.isoformat(), SHOP_DAYS[day.weekday()]))
            if len(out) >= n:
                break
    return out


def next_shop_run(when: datetime) -> tuple[str, str] | None:
    runs = upcoming_runs(when, 1)
    return runs[0] if runs else None


async def shopping_list(db: Database, when: datetime) -> list[dict]:
    """Items to buy = burn_rate × horizon − current stock (positive shortfalls only).

    Horizon = days until the shop-after-next run, so bought stock lasts until the next
    opportunity (Sunday-closed already reflected in the run schedule).
    """
    runs = upcoming_runs(when, 2)
    today = datetime.fromisoformat(date_str(when)).date()
    if len(runs) >= 2:
        horizon = max(1, (datetime.fromisoformat(runs[1][0]).date() - today).days)
    else:
        horizon = DEFAULT_HORIZON_DAYS

    items = set()
    for consumption in MEAL_CONSUMPTION.values():
        items.update(consumption.keys())

    result: list[dict] = []
    stock = await get_all_stock(db)
    for item in sorted(items):
        burn = await burn_rate_per_day(db, item, when)
        need = burn * horizon
        have = stock.get(item, 0.0)
        shortfall = round(need - have, 0)
        if shortfall > 0:
            result.append({"item": item, "need_g": shortfall,
                           "burn_per_day": burn, "stock_g": have, "horizon_days": horizon})
    return result


# --------------------------------------------------------------------------- spend
async def add_spend(
    db: Database, amount: float, category: str, when: datetime
) -> float:
    await db.execute(
        "INSERT INTO spend_log(ts, date, amount, category) VALUES (?, ?, ?, ?)",
        (when.isoformat(timespec="seconds"), date_str(when), round(amount, 2), category.lower()),
    )
    return await monthly_spend(db, when, category)


async def monthly_spend(db: Database, when: datetime, category: str | None = None) -> float:
    start, end = month_bounds(when)
    if category:
        val = await db.fetchval(
            "SELECT COALESCE(SUM(amount),0) FROM spend_log WHERE date BETWEEN ? AND ? "
            "AND category = ?", (start, end, category.lower()), default=0.0)
    else:
        val = await db.fetchval(
            "SELECT COALESCE(SUM(amount),0) FROM spend_log WHERE date BETWEEN ? AND ?",
            (start, end), default=0.0)
    return round(val, 2)


async def spend_report(db: Database, when: datetime) -> dict:
    start, end = month_bounds(when)
    rows = await db.fetchall(
        "SELECT category, COALESCE(SUM(amount),0) total FROM spend_log "
        "WHERE date BETWEEN ? AND ? GROUP BY category ORDER BY total DESC",
        (start, end),
    )
    by_cat = {r["category"]: round(r["total"], 2) for r in rows}
    return {"month_start": start, "month_end": end, "by_category": by_cat,
            "total": round(sum(by_cat.values()), 2)}
