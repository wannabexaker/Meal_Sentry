"""Rewards catalog: seed, CRUD, affordability."""

from mealsentry.engine import rewards


async def test_rewards_seeded(db):
    ids = {r["id"] for r in await rewards.list_rewards(db)}
    assert {"araga", "gaming", "concert", "rw_halva"} <= ids


async def test_rewards_crud_and_affordability(db):
    await rewards.add_reward(db, "spa", "Spa day", 200, emoji="💆")
    assert (await rewards.get_reward(db, "spa"))["cost"] == 200
    await rewards.set_cost(db, "spa", 120)
    assert (await rewards.get_reward(db, "spa"))["cost"] == 120
    await rewards.delete_reward(db, "spa")
    assert await rewards.get_reward(db, "spa") is None

    priced = {r["id"]: r for r in await rewards.list_rewards(db, coins=30)}
    assert priced["araga"]["affordable"] is True     # 25 ≤ 30
    assert priced["concert"]["affordable"] is False   # 150 > 30
