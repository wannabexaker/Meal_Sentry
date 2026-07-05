"""Meal system: macro calc, rules engine (yogurt cap, salmon lock), logging."""

import pytest

from mealsentry.engine import meals


def test_compute_macros_known_foods():
    m = meals.compute_macros([("αυγα", 250), ("κοτοπουλο", 200), ("cottage", 100)])
    assert m.unresolved == []
    assert m.kcal > 600
    assert m.protein > 80


def test_compute_macros_reports_unresolved():
    m = meals.compute_macros([("κοτοπουλο", 200), ("δρακοντόσουπα", 100)])
    assert "δρακοντόσουπα" in m.unresolved


async def test_log_meal_totals(db, monday):
    logged = await meals.log_meal(db, "chicken", monday, 1.0)
    assert logged["kcal"] == 600 and logged["protein_g"] == 62
    half = await meals.log_meal(db, "chicken", monday, 0.5)
    assert half["kcal"] == 300
    kcal, protein = await meals.today_totals(db, monday)
    assert kcal == 900 and protein == 93


async def test_yogurt_bomb_weekly_cap(db, monday):
    await meals.log_meal(db, "yogurt_bomb", monday)
    await meals.log_meal(db, "yogurt_bomb", monday)
    with pytest.raises(meals.MealCapReached) as exc:
        await meals.log_meal(db, "yogurt_bomb", monday)
    assert exc.value.alternative  # offers the honey variant


async def test_salmon_locked(db, monday):
    with pytest.raises(meals.MealLocked):
        await meals.log_meal(db, "salmon", monday)
    await meals.unlock_meal(db, "salmon")
    logged = await meals.log_meal(db, "salmon", monday)
    assert logged["kcal"] > 0


async def test_add_and_list_meal(db):
    await meals.add_meal(db, "myshake", "Δικό μου shake", "whey+milk", 300, 40)
    m = await meals.get_meal(db, "myshake")
    assert m.protein_g == 40
    listing = await meals.list_meals(db)
    assert any(x.id == "myshake" for x in listing)
