"""Gamification: XP, streaks, levels, respect, boss weeks, penalties."""

from mealsentry.engine import game


def test_streak_multiplier():
    assert game.streak_multiplier(1) == 1.0
    assert game.streak_multiplier(7) == 1.5
    assert game.streak_multiplier(21) == 2.0


def test_level_for_xp():
    assert game.level_for_xp(0) == (1, "Αρχάριος")
    assert game.level_for_xp(99)[0] == 1
    assert game.level_for_xp(100)[0] == 2
    assert game.level_for_xp(5000) == (10, "Σπαρτιάτης")


def test_respect_tier():
    assert game.respect_tier(33) == "LOW"
    assert game.respect_tier(34) == "MID"
    assert game.respect_tier(66) == "MID"
    assert game.respect_tier(67) == "HIGH"


async def test_award_meal_and_respect(db, monday):
    res = await game.award(db, "meal", monday)
    assert res.xp_delta == 10
    assert res.xp_total == 10
    assert res.respect == 52  # 50 + 2


async def test_boss_week_doubles_xp(db, monday):
    await game.set_boss_week(db, True)
    res = await game.award(db, "gym", monday)
    assert res.xp_delta == 80  # 40 * 2 (streak x1 first time)


async def test_penalize_floors_at_zero(db, monday):
    await game.award(db, "meal", monday)      # xp 10
    res = await game.penalize(db, "failed_week", monday)  # -50
    assert res.xp_total == 0
    assert res.respect == 32  # 50 (+2 meal) - 20 (failed_week)


async def test_streak_increments_and_multiplies(db, monday):
    from datetime import timedelta
    xp_first = (await game.award(db, "steps", monday)).xp_delta
    # 6 more consecutive days -> streak hits 7 -> x1.5
    day = monday
    last = None
    for _ in range(6):
        day = day + timedelta(days=1)
        last = await game.award(db, "steps", day)
    assert xp_first == 15
    assert last.xp_delta == 22  # round(15 * 1.5)


async def test_cheat_token_spend(db):
    await game.grant_cheat_token(db, 1)
    assert await game.spend_cheat_token(db) is True
    assert await game.spend_cheat_token(db) is False
