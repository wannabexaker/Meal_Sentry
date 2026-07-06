"""Service layer: composes engine modules into high-level operations shared by the
Telegram bot and the FastAPI backend.

Returns plain data (dicts / dataclasses), never coach text — the bot renders tone from
these results, the API serializes them to JSON. XP/streak orchestration that spans several
engine modules lives here so it is written once.
"""

from __future__ import annotations

import secrets
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from typing import Any

from .config import Config
from .db import Database
from .engine import classes, foods, game, gym, inventory, math, meals, rewards, sleep
from .util import date_str


def _asdict(obj: Any) -> Any:
    return asdict(obj) if is_dataclass(obj) else obj


# RPG dashboard helpers -----------------------------------------------------------------
_RPG_TIER_NAMES = ["Χάλκινο", "Ασημένιο", "Χρυσό", "Επικό", "Θρυλικό"]
_TASK_LABELS = {
    "prep_morning": "Prep πρωί", "meal1": "Γεύμα 1", "meal2": "Γεύμα 2",
    "steps": "Βήματα", "prep_evening": "Prep βράδυ", "shopping": "Ψώνια",
}


def _rpg_tier(level: int) -> dict:
    """Visual tier from level: 1-2 Bronze .. 9-10 Legendary. Higher = more panels."""
    idx = max(0, min(4, (level - 1) // 2))
    return {"index": idx, "name": _RPG_TIER_NAMES[idx],
            "charts_unlocked": level >= 2, "boss_unlocked": level >= 6}


def _rarity(grams: float, days_left: float | None) -> str:
    if grams <= 0:
        return "empty"
    if days_left is not None and days_left < 2:
        return "low"
    if days_left is not None and days_left < 5:
        return "mid"
    return "high"


def _hhmm(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M")
    except ValueError:
        return ""


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

    _PROFILE_FIELDS = {
        "name", "sex", "age", "height_cm", "weight_kg", "start_weight_kg", "steps_target",
        "gym_target_sessions", "sleep_target_hours", "protein_factor", "deficit_kcal",
        "desired_class",
    }

    async def set_class(self, class_id: str) -> dict:
        await self.update_profile(desired_class=class_id)
        p = await self.profile()
        return classes.describe(p["height_cm"], p["weight_kg"], p["desired_class"])

    async def update_profile(self, **fields) -> dict:
        sets = {k: v for k, v in fields.items() if k in self._PROFILE_FIELDS and v is not None}
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            await self.db.execute(
                f"UPDATE user_profile SET {cols}, updated_at = ? WHERE id = 1",
                (*sets.values(), self.now().isoformat(timespec="seconds")))
        return await self.profile()

    async def dashboard_secret(self) -> bytes:
        """Per-install HMAC secret for control-page tokens (generated once, stored in kv)."""
        hexs = await self.db.kv_get("dashboard_secret")
        if not hexs:
            hexs = secrets.token_hex(32)
            await self.db.kv_set("dashboard_secret", hexs)
        return bytes.fromhex(hexs)

    async def make_dashboard_token(self) -> str:
        from .token import make_token
        return make_token(await self.dashboard_secret())

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

    # ------------------------------------------------------------------ rewards economy
    async def rewards_shop(self, when: datetime | None = None) -> dict:
        coins = (await game.get_state(self.db))["coins"]
        return {"coins": coins, "rewards": await rewards.list_rewards(self.db, coins)}

    async def redeem_reward(self, reward_id: str, when: datetime) -> dict:
        """Spend coins on a reward (cheat food logs macros; leisure just records it)."""
        reward = await rewards.get_reward(self.db, reward_id)
        if reward is None:
            return {"ok": False, "reason": "Δεν υπάρχει αυτή η ανταμοιβή."}
        coins = (await game.get_state(self.db))["coins"]
        if coins < reward["cost"]:
            return {"ok": False, "reason": f"Χρειάζεσαι {reward['cost']} 🪙 (έχεις {coins})."}
        await game.spend_coins(self.db, reward["cost"])
        await self.db.execute(
            "INSERT INTO reward_log(ts, date, reward_id, name, cost) VALUES (?, ?, ?, ?, ?)",
            (when.isoformat(timespec="seconds"), date_str(when), reward_id,
             reward["name"], reward["cost"]))
        meal = None
        if reward["kind"] == "cheat" and reward["meal_id"]:
            meal = await meals.log_meal(self.db, reward["meal_id"], when)
        coins_left = (await game.get_state(self.db))["coins"]
        return {"ok": True, "reward": reward, "coins_left": coins_left, "meal": meal}

    async def _after_food_log(self, when: datetime) -> dict:
        """Shared post-log bookkeeping: meal XP + once/day protein-floor award + today totals."""
        meal_award = await game.award(self.db, "meal", when)
        kcal, protein = await meals.today_totals(self.db, when)
        targets = await self.compute_targets(when)
        floor_award = None
        if protein >= targets.protein_floor_g:
            floor_award = await self._award_once("protein_floor", when)
        return {
            "today": {"kcal": round(kcal, 1), "protein_g": round(protein, 1),
                      "protein_floor_g": targets.protein_floor_g,
                      "floor_hit": protein >= targets.protein_floor_g},
            "award": _asdict(meal_award),
            "floor_award": _asdict(floor_award) if floor_award else None,
        }

    async def resolve_query(self, query: str) -> tuple[str | None, str | None]:
        """Fuzzy-map free text to a loggable: ('food', id) or ('combo', id) or (None, None).

        Food-first (granular is the primary catalog); combos are the fallback.
        """
        food = await foods.find_food(self.db, query)
        if food:
            return "food", food["id"]
        meal = await meals.find_meal(self.db, query)
        if meal:
            return "combo", meal.id
        return None, None

    async def eat_food(self, food_id: str, when: datetime, grams: float | None = None) -> dict:
        """Log a single weighed food by grams (default portion if grams is None)."""
        food = await foods.get_food(self.db, food_id)
        if food is None:
            raise meals.MealNotFound(f"Δεν υπάρχει τρόφιμο «{food_id}».")
        g = max(1.0, min(grams if grams is not None else food["default_g"], 3000.0))
        factor = g / 100.0
        kcal = round(food["kcal"] * factor, 1)
        protein = round(food["protein"] * factor, 1)
        label = f"{food['name']} {int(round(g))}g"
        await self.db.execute(
            "INSERT INTO meal_log(ts, date, meal_id, food_id, grams, fraction, kcal, protein_g, note) "
            "VALUES (?, ?, ?, ?, ?, 1.0, ?, ?, ?)",
            (when.isoformat(timespec="seconds"), date_str(when), food_id, food_id, g,
             kcal, protein, label))
        result = await self._after_food_log(when)
        result["logged"] = {"name": food["name"], "grams": g, "kcal": kcal, "protein_g": protein}
        return result

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

    # ------------------------------------------------------------------ RPG dashboard
    async def dashboard(self, when: datetime | None = None) -> dict:
        """Aggregate everything the MMORPG-style dashboard renders in one payload."""
        when = when or self.now()
        st = await self.status(when)
        today, g = st["today"], st["game"]
        d = date_str(when)
        p = await self.profile()
        tier = _rpg_tier(g["level"])
        char_class = classes.describe(p["height_cm"], p["weight_kg"],
                                      p.get("desired_class") or classes.DEFAULT_CLASS)

        weighed = bool(await self.db.fetchval("SELECT 1 FROM weight_log WHERE date = ?", (d,)))
        slept = await self.db.fetchone("SELECT hours FROM sleep_log WHERE date = ?", (d,))
        sleep_done = bool(slept) and slept["hours"] >= p["sleep_target_hours"]

        quests = [
            {"id": "protein", "label": "Πρωτεΐνη", "icon": "🥩", "cur": round(today["protein_g"]),
             "max": today["protein_floor_g"], "kind": "bar",
             "done": today["protein_g"] >= today["protein_floor_g"]},
            {"id": "calories", "label": "Θερμίδες", "icon": "🔥", "cur": round(today["kcal"]),
             "max": today["kcal_target"], "kind": "bar",
             "done": 0 < today["kcal"] <= today["kcal_target"]},
            {"id": "steps", "label": "Βήματα", "icon": "👟", "cur": today["steps"],
             "max": today["steps_target"], "kind": "bar",
             "done": today["steps"] >= today["steps_target"]},
            {"id": "gym", "label": "Γυμναστήριο", "icon": "🏋️", "cur": st["gym"]["sessions"],
             "max": st["gym"]["target"], "kind": "bar",
             "done": st["gym"]["sessions"] >= st["gym"]["target"]},
            {"id": "weigh", "label": "Ζύγισμα", "icon": "⚖️", "kind": "check", "done": weighed},
            {"id": "sleep", "label": "Ύπνος", "icon": "😴", "kind": "check", "done": sleep_done},
        ]

        task_rows = await self.db.fetchall(
            "SELECT task_key, state FROM tasks WHERE date = ?", (d,))
        side_quests = [{"id": r["task_key"], "label": _TASK_LABELS.get(r["task_key"], r["task_key"]),
                        "state": r["state"]} for r in task_rows]

        inventory_items = []
        for item, grams in (await inventory.get_all_stock(self.db)).items():
            pred = await inventory.predict_runout(self.db, item, when)
            inventory_items.append({
                "item": item, "grams": round(grams), "runout_date": pred.runout_date,
                "burn": pred.burn_per_day, "rarity": _rarity(grams, pred.days_left)})

        salmon = await self.db.fetchone("SELECT locked FROM meals WHERE id = 'salmon'")
        specials = [
            {"item": "Cheat token", "count": g["cheat_tokens"], "rarity": "epic", "kind": "consumable"},
            {"item": "Σολομός", "locked": bool(salmon["locked"]) if salmon else True,
             "rarity": "legendary", "kind": "reward"},
        ]
        shop = await self.rewards_shop(when)   # {"coins", "rewards": [...]}

        loot_rows = await self.db.fetchall(
            "SELECT ml.ts, ml.note, ml.food_id, COALESCE(m.name, ml.meal_id) mname, "
            "ml.kcal, ml.protein_g, ml.fraction "
            "FROM meal_log ml LEFT JOIN meals m ON ml.meal_id = m.id "
            "WHERE ml.date = ? ORDER BY ml.ts", (d,))
        loot = [{"time": _hhmm(r["ts"]),
                 "name": (r["note"] if r["food_id"] else r["mname"]),
                 "kcal": round(r["kcal"]), "protein": round(r["protein_g"]),
                 "fraction": (1.0 if r["food_id"] else r["fraction"])} for r in loot_rows]

        return {
            "character": {
                "name": st["name"], "level": g["level"], "title": g["level_name"],
                "xp": g["xp"], "xp_to_next": g["xp_to_next"],
                "respect": g["respect"], "respect_tier": g["respect_tier"],
                "cheat_tokens": g["cheat_tokens"], "coins": g["coins"],
                "boss_week": g["boss_week"],
                "weight_kg": st["weight_kg"], "start_weight_kg": st["start_weight_kg"],
                "class": char_class,
            },
            "tier": tier,
            "coins": g["coins"],
            "rewards": shop,
            "stats": {
                "protein": {"cur": round(today["protein_g"]), "max": today["protein_floor_g"]},
                "calories": {"cur": round(today["kcal"]), "max": today["kcal_target"]},
                "steps": {"cur": today["steps"], "max": today["steps_target"]},
                "gym": {"cur": st["gym"]["sessions"], "max": st["gym"]["target"]},
            },
            "quests": quests,
            "side_quests": side_quests,
            "inventory": inventory_items,
            "specials": specials,
            "loot": loot,
            "streaks": st["streaks"],
        }
