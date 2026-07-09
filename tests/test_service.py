"""Service layer orchestration: ate flow + floor award once, weight recompute, steps."""

from mealsentry.engine import game


async def test_coins_earned_and_reward_redeemed(service, db, monday):
    # Logging meals earns coins (meal = +1 each).
    await service.ate("chicken", monday)
    await service.ate("beef", monday)
    assert (await game.get_state(db))["coins"] >= 2
    # A pricey reward is refused without enough coins.
    assert (await service.redeem_reward("concert", monday))["ok"] is False
    # Grant coins, redeem a cheat reward → it logs the linked meal's macros and spends coins.
    await game.grant_coins(db, 50)
    before = (await game.get_state(db))["coins"]
    res = await service.redeem_reward("rw_halva", monday)
    assert res["ok"] is True and res["meal"]["kcal"] == 235
    assert res["coins_left"] == before - 20


async def test_ate_awards_meal_and_floor_once(service, monday):
    # Log enough protein to cross the 153 g floor across several meals.
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
    assert st["targets"]["protein_floor_g"] == 153
    assert "respect_tier" in st["game"]


async def test_apply_cut_changes_target(service, monday):
    before = (await service.compute_targets(monday)).calorie_target
    await service.apply_cut(800, monday)
    after = (await service.compute_targets(monday)).calorie_target
    assert after == before - 200  # deficit 600 -> 800
