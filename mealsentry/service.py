"""Service layer: composes engine modules into high-level operations shared by the
Telegram bot and the FastAPI backend.

Returns plain data (dicts / dataclasses), never coach text — the bot renders tone from
these results, the API serializes them to JSON. XP/streak orchestration that spans several
engine modules lives here so it is written once.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from typing import Any

from .config import Config
from .db import Database
from .engine import game, gym, inventory, math, meals, sleep
from .util import date_str


def _asdict(obj: Any) -> Any:
    return asdict(obj) if is_dataclass(obj) else obj


class Service:
    def __init__(self, db: Database, config: Config):
        self.db = db
        self.config = config

    def now(self) -> datetime:
        return self.config.now()

    # ------------------------------------------------------------------ profile
    async def profile(self) -> dict:
        row = await self.db.fetchone("SELECT * FROM user_profile WHERE id = 1")
        return dict(row) if row else {}

    async def _avg_steps_7d(self, when: datetime) -> float:
        start = (datetime.fromisoformat(date_str(when)).date() - timedelta(days=6)).isoformat()
        val = await self.db.fetchval(
            "SELECT AVG(steps) FROM steps_log WHERE date BETWEEN ? AND ?",
            (start, date_str(when)),
        )
        return float(val or 0.0)

    async def compute_targets(self, when: datetime | None = None) -> math.Targets:
        when = when or self.now()
        p = await self.profile()
        gym_sessions = await gym.sessions_this_week(self.db, when)
        avg_steps = await self._avg_steps_7d(when)
        return math.compute_targets(
            sex=p["sex"], weight_kg=p["weight_kg"], height_cm=p["height_cm"],
            age=p["age"], protein_factor=p["protein_factor"], deficit_kcal=p["deficit_kcal"],
            gym_sessions_this_week=gym_sessions, avg_steps_7d=avg_steps,
        )

    # ------------------------------------------------------------------ status
    async def status(self, when: datetime | None = None) -> dict:
        when = when or self.now()
        p = await self.profile()
        targets = await self.compute_targets(when)
        kcal, protein = await meals.today_totals(self.db, when)
        state = await game.get_state(self.db)
        gym_sessions = await gym.sessions_this_week(self.db, when)
        streaks = await game.get_streaks(self.db)
        steps_today = await self.db.fetchval(
            "SELECT steps FROM steps_log WHERE date = ?", (date_str(when),), default=0)
        return {
            "name": p["name"], "weight_kg": p["weight_kg"], "start_weight_kg": p["start_weight_kg"],
            "targets": _asdict(targets),
            "today": {
                "kcal": round(kcal, 1), "protein_g": round(protein, 1),
                "protein_floor_g": targets.protein_floor_g,
                "protein_gap_g": max(0, targets.protein_floor_g - round(protein)),
                "kcal_target": targets.calorie_target,
                "steps": steps_today or 0, "steps_target": p["steps_target"],
            },
            "gym": {"sessions": gym_sessions, "target": p["gym_target_sessions"]},
            "game": state, "streaks": streaks,
        }

    # ------------------------------------------------------------------ logging
    async def _award_once(self, event: str, when: datetime) -> game.AwardResult | None:
        """Award XP for a daily goal at most once per day (idempotent)."""
        key = f"awarded:{event}:{date_str(when)}"
        if await self.db.kv_get(key):
            return None
        await self.db.kv_set(key, "1")
        return await game.award(self.db, event, when)

    async def ate(self, meal_id: str, when: datetime, fraction: float = 1.0) -> dict:
        logged = await meals.log_meal(self.db, meal_id, when, fraction)
        meal_award = await game.award(self.db, "meal", when)
        kcal, protein = await meals.today_totals(self.db, when)
        targets = await self.compute_targets(when)
        floor_award = None
        if protein >= targets.protein_floor_g:
            floor_award = await self._award_once("protein_floor", when)
        return {
            "logged": logged,
            "today": {"kcal": round(kcal, 1), "protein_g": round(protein, 1),
                      "protein_floor_g": targets.protein_floor_g,
                      "floor_hit": protein >= targets.protein_floor_g},
            "award": _asdict(meal_award),
            "floor_award": _asdict(floor_award) if floor_award else None,
        }

    async def log_weight(self, kg: float, when: datetime) -> dict:
        await self.db.execute(
            "INSERT INTO weight_log(ts, date, kg) VALUES (?, ?, ?)",
            (when.isoformat(timespec="seconds"), date_str(when), kg))
        await self.db.execute(
            "UPDATE user_profile SET weight_kg = ?, updated_at = ? WHERE id = 1",
            (kg, when.isoformat(timespec="seconds")))
        await self._award_once("weigh_in", when)
        targets = await self.compute_targets(when)
        stalled, proposal = await self._check_stall(when)
        return {"kg": kg, "targets": _asdict(targets),
                "stalled": stalled, "proposal": _asdict(proposal) if proposal else None}

    async def _weekly_weight_avgs(self, when: datetime, weeks: int = 4) -> list[float]:
        rows = await self.db.fetchall(
            "SELECT strftime('%Y-%W', date) wk, AVG(kg) kg FROM weight_log "
            "GROUP BY wk ORDER BY wk DESC LIMIT ?", (weeks,))
        return [round(r["kg"], 2) for r in reversed(rows)]

    async def _check_stall(self, when: datetime):
        avgs = await self._weekly_weight_avgs(when)
        if math.is_stalled(avgs):
            p = await self.profile()
            return True, math.propose_cut(p["deficit_kcal"])
        return False, None

    async def apply_cut(self, new_deficit: int, when: datetime) -> math.Targets:
        await self.db.execute(
            "UPDATE user_profile SET deficit_kcal = ?, updated_at = ? WHERE id = 1",
            (new_deficit, when.isoformat(timespec="seconds")))
        return await self.compute_targets(when)

    async def log_steps(self, steps: int, when: datetime) -> dict:
        await self.db.execute(
            "INSERT INTO steps_log(ts, date, steps) VALUES (?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET steps = excluded.steps, ts = excluded.ts",
            (when.isoformat(timespec="seconds"), date_str(when), steps))
        p = await self.profile()
        hit = steps >= p["steps_target"]
        award = await self._award_once("steps", when) if hit else None
        return {"steps": steps, "target": p["steps_target"], "hit": hit,
                "award": _asdict(award) if award else None}

    async def log_gym(self, minutes: int, when: datetime) -> dict:
        sessions = await gym.log_session(self.db, when, minutes)
        award = await game.award(self.db, "gym", when)
        return {"sessions": sessions, "target": gym.WEEKLY_TARGET,
                "minutes": minutes, "award": _asdict(award)}

    async def log_sleep(self, bed: str, wake: str, when: datetime) -> dict:
        p = await self.profile()
        entry = await sleep.log_sleep(self.db, when, bed, wake, p["sleep_target_hours"])
        award = None
        if not entry.below_target:
            award = await self._award_once("sleep", when)
        short_nights = await sleep.consecutive_short_nights(
            self.db, when, p["sleep_target_hours"])
        return {"entry": _asdict(entry), "award": _asdict(award) if award else None,
                "short_nights": short_nights,
                "escalate": short_nights >= 3}

    # ------------------------------------------------------------------ inventory
    async def set_stock(self, item: str, grams: float, when: datetime) -> dict:
        await inventory.set_stock(self.db, item, grams, when)
        pred = await inventory.predict_runout(self.db, item, when)
        return {"stock": await inventory.get_all_stock(self.db), "prediction": _asdict(pred)}

    async def shopping_list(self, when: datetime) -> dict:
        items = await inventory.shopping_list(self.db, when)
        run = inventory.next_shop_run(when)
        return {"items": items, "next_run": run, "store": self.config.shop_store}

    async def spend(self, amount: float, category: str, when: datetime) -> dict:
        total = await inventory.add_spend(self.db, amount, category, when)
        budget = self.config.chicken_budget_eur if category.lower() == "chicken" else None
        return {"category": category, "month_total": total, "budget": budget}

    async def spend_report(self, when: datetime) -> dict:
        return await inventory.spend_report(self.db, when)

    # ------------------------------------------------------------------ reporting
    async def weekly_report(self, when: datetime | None = None) -> dict:
        return await game.weekly_report_data(self.db, when or self.now())

    async def weight_series(self, days: int = 60, when: datetime | None = None) -> list[dict]:
        when = when or self.now()
        start = (datetime.fromisoformat(date_str(when)).date() - timedelta(days=days)).isoformat()
        rows = await self.db.fetchall(
            "SELECT date, AVG(kg) kg FROM weight_log WHERE date >= ? GROUP BY date ORDER BY date",
            (start,))
        return [{"date": r["date"], "kg": round(r["kg"], 2)} for r in rows]

    async def recent_logs(self, limit: int = 20) -> dict:
        meals_rows = await self.db.fetchall(
            "SELECT ts, meal_id, fraction, kcal, protein_g FROM meal_log ORDER BY ts DESC LIMIT ?",
            (limit,))
        warn_rows = await self.db.fetchall(
            "SELECT ts, task_key, level, text FROM warnings ORDER BY ts DESC LIMIT ?", (limit,))
        return {"meals": [dict(r) for r in meals_rows], "warnings": [dict(r) for r in warn_rows]}
