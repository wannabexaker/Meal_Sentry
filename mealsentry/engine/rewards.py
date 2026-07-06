"""Rewards catalog — spend coins on cheat foods and lifestyle rewards (άραγμα, gaming,
συναυλίες, έξοδοι). Seeded from data/rewards.json; editable at runtime like foods.
"""

from __future__ import annotations

from ..db import Database


def _row(r) -> dict:
    return {"id": r["id"], "name": r["name"], "emoji": r["emoji"], "cost": r["cost"],
            "kind": r["kind"], "meal_id": r["meal_id"], "enabled": bool(r["enabled"]),
            "custom": bool(r["custom"])}


async def list_rewards(db: Database, coins: int | None = None) -> list[dict]:
    rows = await db.fetchall("SELECT * FROM rewards WHERE enabled = 1 ORDER BY kind, cost")
    out = [_row(r) for r in rows]
    if coins is not None:
        for r in out:
            r["affordable"] = coins >= r["cost"]
    return out


async def get_reward(db: Database, reward_id: str) -> dict | None:
    r = await db.fetchone("SELECT * FROM rewards WHERE id = ?", (reward_id,))
    return _row(r) if r else None


async def add_reward(
    db: Database, reward_id: str, name: str, cost: int, *, emoji: str = "🎁",
    kind: str = "leisure", meal_id: str | None = None,
) -> dict:
    await db.execute(
        """INSERT INTO rewards(id, name, emoji, cost, kind, meal_id, enabled, custom)
           VALUES (?, ?, ?, ?, ?, ?, 1, 1)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, emoji=excluded.emoji, cost=excluded.cost,
             kind=excluded.kind, meal_id=excluded.meal_id""",
        (reward_id, name, emoji, int(cost), kind, meal_id),
    )
    return await get_reward(db, reward_id)


async def set_cost(db: Database, reward_id: str, cost: int) -> None:
    await db.execute("UPDATE rewards SET cost = ? WHERE id = ?", (int(cost), reward_id))


async def delete_reward(db: Database, reward_id: str) -> None:
    await db.execute("DELETE FROM rewards WHERE id = ?", (reward_id,))
