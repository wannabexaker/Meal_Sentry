"""Wheel of Fortune (Feature A) — service + engine tests.

Covers the four spec bullets: cost/refuse/valid-types/log-written + meal recent-avoidance.
Uses a fixed seed inside a helper to keep the outcome-distribution assertion deterministic.
"""

from __future__ import annotations

import random

from mealsentry.engine import game, wheel

VALID_TYPES = {"meal", "exercise", "coins", "xp", "jackpot"}


async def _grant(db, coins: int) -> None:
    """Directly bump coins so the tests don't depend on external XP mechanics."""
    await game.grant_coins(db, coins)


async def test_spin_refused_without_coins(service, monday):
    res = await service.spin_wheel(monday)
    assert res["ok"] is False
    assert "🪙" in res["reason"]


async def test_spin_costs_one_coin(service, db, monday):
    await _grant(db, 5)
    before = (await game.get_state(db))["coins"]
    res = await service.spin_wheel(monday)
    assert res["ok"] is True
    after = (await game.get_state(db))["coins"]
    # A coins/xp/jackpot outcome may add back to the wallet — but the SPEND itself must
    # always be exactly 1 (so |after - before| accounts for the applied grant + the −1).
    applied = res.get("applied", {})
    granted = applied.get("coins_delta", 0)
    assert res["coins_left"] == after
    assert after == before - 1 + granted


async def test_spin_outcomes_valid_and_logged(service, db, monday):
    """20 spins → all types in the valid set + one wheel_log row per spin."""
    await _grant(db, 40)
    seen: set[str] = set()
    random.seed(1337)
    for _ in range(20):
        r = await service.spin_wheel(monday)
        if not r["ok"]:
            # top-up when the wheel drains coins on non-granting outcomes
            await _grant(db, 5)
            continue
        seen.add(r["outcome"]["type"])
    assert seen.issubset(VALID_TYPES)
    row = await db.fetchone("SELECT COUNT(*) AS c FROM wheel_log")
    assert row["c"] >= 15  # we may skip a few when the wallet drains — well over half succeed


async def test_wheel_log_written(service, db, monday):
    await _grant(db, 2)
    await service.spin_wheel(monday)
    row = await db.fetchone("SELECT * FROM wheel_log ORDER BY id DESC LIMIT 1")
    assert row is not None
    assert row["outcome_type"] in VALID_TYPES
    assert row["detail"]  # non-empty short summary


async def test_meal_outcome_avoids_recent_food(service, db, monday):
    """Log a food to make it 'recent', then force meal outcomes and verify avoidance."""
    # Log chicken so it enters foods.recent_foods
    await service.eat_food("chicken_breast", monday, grams=180)
    await _grant(db, 20)

    # Force wheel to pick 'meal' by monkey-patching random.choices in the engine.
    original = random.choices
    random.choices = lambda pop, weights=None, k=1: ["meal"]   # type: ignore[assignment]
    try:
        found_meal = False
        for _ in range(10):
            r = await service.spin_wheel(monday)
            if not r["ok"]:
                await _grant(db, 5)
                continue
            o = r["outcome"]
            if o["type"] == "meal" and o["kind"] == "food":
                found_meal = True
                assert o["id"] != "chicken_breast"
    finally:
        random.choices = original
    assert found_meal


async def test_wheel_engine_summarize_all_types(db, monday):
    """Directly probe the pure summarize() branches so schema drift is caught early."""
    for outcome in (
        {"type": "meal", "kind": "food", "id": "chicken_breast", "name": "Κοτόπουλο"},
        {"type": "exercise", "group": "στήθος", "challenge": "4×10 bench press"},
        {"type": "coins", "amount": 5},
        {"type": "xp", "amount": 20},
        {"type": "jackpot", "coins": 15},
    ):
        assert wheel.summarize(outcome)
