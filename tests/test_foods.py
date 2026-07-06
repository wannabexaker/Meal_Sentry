"""DB-backed food database: lookup, macro calc, and CRUD."""

import pytest

from mealsentry.engine import foods


async def test_find_food_by_alias(db):
    assert (await foods.find_food(db, "κοτοπουλο"))["id"] == "chicken_breast"
    assert (await foods.find_food(db, "στηθος κοτοπουλο"))["id"] == "chicken_breast"
    assert await foods.find_food(db, "δρακοντόσουπα") is None


async def test_compute_macros_known_foods(db):
    m = await foods.compute_macros(db, [("αυγα", 250), ("κοτοπουλο", 200), ("cottage", 100)])
    assert m.unresolved == []
    assert m.kcal > 600
    assert m.protein > 80


async def test_compute_macros_reports_unresolved(db):
    m = await foods.compute_macros(db, [("κοτοπουλο", 200), ("δρακοντόσουπα", 100)])
    assert "δρακοντόσουπα" in m.unresolved


async def test_salads_and_categories_seeded(db):
    salad_ingredients = await foods.list_foods(db, category="veg")
    assert any(f["id"] == "lettuce" for f in salad_ingredients)
    assert await foods.find_food(db, "σος καισαρα") is not None  # salad sauce present


async def test_add_and_delete_custom_food(db):
    await foods.add_food(db, "gyros", "Γύρος χοιρινός", 215, 15, carbs=2, fat=16,
                         category="protein", aliases=["γυρος"])
    assert (await foods.find_food(db, "γυρος"))["custom"] is True
    m = await foods.compute_macros(db, [("γυρος", 200)])
    assert m.protein == pytest.approx(30.0)
    await foods.delete_food(db, "gyros")
    assert await foods.get_food(db, "gyros") is None
