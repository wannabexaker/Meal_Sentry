"""🎰 Wheel of Fortune (Feature A).

Pure logic: pick a weighted-random segment, resolve it into a concrete outcome that
avoids immediate repeats (via ``wheel_log``) and — for the ``meal`` outcome — foods
already eaten in the last five entries (via ``foods.recent_foods``). The service layer
composes this with the coin economy (spin costs 1 🪙) and applies grants.

Zero telegram/fastapi imports.
"""

from __future__ import annotations

import random
from datetime import datetime

from ..db import Database
from . import foods, meals

# Weights match the spec exactly. Order matters only for readability.
WHEEL_SEGMENTS: list[tuple[str, int]] = [
    ("meal", 40),
    ("exercise", 25),
    ("coins", 15),
    ("xp", 12),
    ("jackpot", 8),
]

# Muscle groups (Greek user strings) + short challenges. Kept small & concrete so the
# outcome is actionable — the user can act on it right after tapping the wheel.
MUSCLE_CHALLENGES: dict[str, list[str]] = {
    "ώμοι": ["3×12 shoulder press", "4×10 lateral raises", "3×15 front raises"],
    "στήθος": ["4×10 bench press", "3×12 push-ups μέχρι αποτυχία", "3×10 dumbbell fly"],
    "πλάτη": ["4×8 pull-ups (ή assisted)", "4×10 barbell row", "3×12 lat pulldown"],
    "πόδια": ["5×5 barbell squat", "4×10 romanian deadlift", "3×15 walking lunges"],
    "χέρια": ["4×10 barbell curl", "4×12 tricep dip", "3×15 hammer curl + skullcrusher"],
    "κοιλιακοί": ["4×20 crunches", "3×45\" plank", "3×15 hanging leg raises"],
    "cardio": ["15′ jog easy", "10 × 30\" sprint / 90\" walk", "20′ bike Z2"],
}
MUSCLE_GROUPS: list[str] = list(MUSCLE_CHALLENGES.keys())

# Ranges (both endpoints inclusive) per outcome type.
COINS_MIN, COINS_MAX = 3, 8
XP_MIN, XP_MAX = 10, 25
JACKPOT_COINS = 15   # small guaranteed reward when the jackpot slot lands


def _weighted_pick(recent: list[str]) -> str:
    """Weighted random over ``WHEEL_SEGMENTS``. Re-roll up to 3 times to avoid picking
    the same outcome type as the previous spin (soft recent-avoidance)."""
    keys = [k for k, _ in WHEEL_SEGMENTS]
    weights = [w for _, w in WHEEL_SEGMENTS]
    last = recent[0] if recent else None
    for _ in range(4):
        pick = random.choices(keys, weights=weights, k=1)[0]
        if pick != last:
            return pick
    return pick   # exhausted retries → accept the repeat


async def _recent_types(db: Database, limit: int = 3) -> list[str]:
    rows = await db.fetchall(
        "SELECT outcome_type FROM wheel_log ORDER BY id DESC LIMIT ?", (limit,))
    return [r["outcome_type"] for r in rows]


async def _pick_meal(db: Database) -> dict:
    """Resolve a ``meal`` segment into a specific food or combo, avoiding recently
    logged foods so the wheel does not just suggest 'what you already ate'."""
    avoid_food_ids = {f["id"] for f in await foods.recent_foods(db, 5)}
    all_foods = await foods.list_foods(db)
    candidates = [f for f in all_foods if f["id"] not in avoid_food_ids]

    all_combos = [m for m in await meals.list_meals(db) if not m.locked]

    # Coin-flip between a granular food and a combo — but only if both pools are non-empty.
    prefer_food = bool(candidates) and (not all_combos or random.random() < 0.5)
    if prefer_food and candidates:
        pick = random.choice(candidates)
        return {"type": "meal", "kind": "food", "id": pick["id"], "name": pick["name"]}
    if all_combos:
        pick = random.choice(all_combos)
        return {"type": "meal", "kind": "combo", "id": pick.id, "name": pick.name}
    # Fallback: no combos and every food was eaten recently → allow any food.
    if all_foods:
        pick = random.choice(all_foods)
        return {"type": "meal", "kind": "food", "id": pick["id"], "name": pick["name"]}
    return {"type": "meal", "kind": None, "id": None, "name": "—"}


def _pick_exercise() -> dict:
    group = random.choice(MUSCLE_GROUPS)
    challenge = random.choice(MUSCLE_CHALLENGES[group])
    return {"type": "exercise", "group": group, "challenge": challenge}


async def spin(db: Database, when: datetime) -> dict:   # noqa: ARG001 - when kept for parity
    """Return a fully-resolved wheel outcome. Does not write to the DB — the service
    layer applies grants and inserts the ``wheel_log`` row."""
    recent = await _recent_types(db, 3)
    seg = _weighted_pick(recent)
    if seg == "meal":
        return await _pick_meal(db)
    if seg == "exercise":
        return _pick_exercise()
    if seg == "coins":
        return {"type": "coins", "amount": random.randint(COINS_MIN, COINS_MAX)}
    if seg == "xp":
        return {"type": "xp", "amount": random.randint(XP_MIN, XP_MAX)}
    # jackpot
    return {"type": "jackpot", "coins": JACKPOT_COINS}


def summarize(outcome: dict) -> str:
    """Short Greek label used both by the bot render and by the ``wheel_log.detail`` field."""
    t = outcome["type"]
    if t == "meal":
        return f"{outcome.get('kind') or '?'}:{outcome.get('id') or '—'}"
    if t == "exercise":
        return f"{outcome['group']} · {outcome['challenge']}"
    if t == "coins":
        return f"+{outcome['amount']}🪙"
    if t == "xp":
        return f"+{outcome['amount']} XP"
    if t == "jackpot":
        return f"🎉 +{outcome['coins']}🪙"
    return ""
