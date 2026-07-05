"""RPG dashboard aggregation: payload shape and level→tier mapping."""

from mealsentry.service import _rpg_tier


def test_rpg_tier_mapping():
    assert _rpg_tier(1)["index"] == 0
    assert _rpg_tier(2)["index"] == 0
    assert _rpg_tier(3)["index"] == 1
    assert _rpg_tier(5)["index"] == 2
    assert _rpg_tier(7)["index"] == 3
    assert _rpg_tier(9)["index"] == 4
    assert _rpg_tier(10)["index"] == 4
    assert _rpg_tier(2)["charts_unlocked"] is True
    assert _rpg_tier(6)["boss_unlocked"] is True


async def test_dashboard_payload(service, monday):
    await service.ate("chicken", monday)
    await service.log_steps(9000, monday)
    d = await service.dashboard(monday)

    assert {"character", "tier", "stats", "quests", "inventory", "loot", "streaks"} <= d.keys()
    assert d["character"]["level"] >= 1
    # protein quest reflects the logged meal
    protein_q = next(q for q in d["quests"] if q["id"] == "protein")
    assert protein_q["cur"] == 62
    # loot lists what was eaten today
    assert any(item["name"] == "Κοτόπουλο κλασικό" for item in d["loot"])
    # steps quest progress
    steps_q = next(q for q in d["quests"] if q["id"] == "steps")
    assert steps_q["cur"] == 9000
