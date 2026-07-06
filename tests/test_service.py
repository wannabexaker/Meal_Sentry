"""Service layer orchestration: ate flow + floor award once, weight recompute, steps."""

import pytest

from mealsentry.engine import meals


async def test_treat_locked_until_protein_and_room(service, monday):
    # Empty day → the cheat treat is locked (protein floor not met).
    with pytest.raises(meals.MealLocked):
        await service.ate("halva", monday)
    # Hit the protein floor with calorie headroom (4 shakes = 176 g protein, 1280 kcal).
    for _ in range(4):
        await service.ate("shake", monday)
    status = {t["id"]: t for t in await service.treat_status(monday)}
    assert status["halva"]["available"] is True
    res = await service.ate("halva", monday)  # now allowed
    assert res["logged"]["kcal"] == 235


async def test_ate_awards_meal_and_floor_once(service, monday):
    # Log enough protein to cross the 173 g floor across several meals.
    floor_awards = 0
    for meal_id in ("chicken", "beef", "pork", "omelette"):  # 62+55+58+49 = 224g
        res = await service.ate(meal_id, monday)
        assert res["award"]["xp_delta"] == 10
        if res["floor_award"] is not None:
            floor_awards += 1
    assert res["today"]["floor_hit"] is True
    assert floor_awards == 1  # protein_floor XP granted exactly once/day


async def test_log_weight_recomputes_targets(service, monday):
    res = await service.log_weight(95.0, monday)
    assert res["targets"]["protein_floor_g"] == round(95.0 * 1.8)  # 171
    p = await service.profile()
    assert p["weight_kg"] == 95.0  # profile updated


async def test_log_steps_award_once(service, monday):
    r1 = await service.log_steps(11000, monday)
    assert r1["hit"] is True and r1["award"]["xp_delta"] == 15
    r2 = await service.log_steps(12000, monday)
    assert r2["hit"] is True and r2["award"] is None  # already awarded today


async def test_status_shape(service, monday):
    await service.ate("shake", monday)
    st = await service.status(monday)
    assert st["today"]["protein_g"] == 44
    assert st["targets"]["protein_floor_g"] == 173
    assert "respect_tier" in st["game"]


async def test_apply_cut_changes_target(service, monday):
    before = (await service.compute_targets(monday)).calorie_target
    await service.apply_cut(800, monday)
    after = (await service.compute_targets(monday)).calorie_target
    assert after == before - 200  # deficit 600 -> 800
