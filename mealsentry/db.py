"""Async SQLite access (aiosqlite) plus schema init and seeding.

All queries are parameterized. A thin ``Database`` wrapper exposes fetch helpers so the
engine modules never touch a raw cursor.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import aiosqlite

from . import paths
from .config import Config

DEFAULT_STREAK_CATEGORIES = ("meals", "protein", "gym", "steps", "sleep", "weigh_in")


class Database:
    """Minimal async wrapper around a single aiosqlite connection."""

    def __init__(self, path: str):
        self.path = str(path)
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected; call connect() first.")
        return self._conn

    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def executescript(self, script: str) -> None:
        await self.conn.executescript(script)
        await self.conn.commit()

    async def execute(self, sql: str, params: tuple = ()) -> None:
        await self.conn.execute(sql, params)
        await self.conn.commit()

    async def executemany(self, sql: str, seq: list[tuple]) -> None:
        await self.conn.executemany(sql, seq)
        await self.conn.commit()

    async def fetchone(self, sql: str, params: tuple = ()) -> aiosqlite.Row | None:
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())

    async def fetchval(self, sql: str, params: tuple = (), default: Any = None) -> Any:
        row = await self.fetchone(sql, params)
        if row is None:
            return default
        return row[0]

    # --- key/value helpers (scheduler bookkeeping) ---
    async def kv_get(self, key: str, default: str | None = None) -> str | None:
        val = await self.fetchval("SELECT value FROM kv WHERE key = ?", (key,))
        return val if val is not None else default

    async def kv_set(self, key: str, value: str) -> None:
        await self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


async def init_db(db: Database, config: Config) -> None:
    """Create the schema (idempotent) and seed reference + initial rows."""
    schema = paths.SCHEMA_SQL.read_text(encoding="utf-8")
    await db.executescript(schema)
    await _migrate(db)
    now = config.now().isoformat(timespec="seconds")
    await _seed_profile(db, config, now)
    await _seed_game_state(db, now)
    await _seed_streaks(db)
    await seed_meals(db)
    await seed_facts(db)
    await seed_foods(db)
    await seed_rewards(db)
    await seed_notifs(db)


async def _migrate(db: Database) -> None:
    """Additive schema migrations for DBs created before a column existed (Pi already has data).

    ``CREATE TABLE IF NOT EXISTS`` does not add columns to existing tables, so we add them
    here idempotently.
    """
    async def columns(table: str) -> set[str]:
        rows = await db.fetchall(f"PRAGMA table_info({table})")
        return {r["name"] for r in rows}

    adds = {
        "foods": [("default_g", "REAL NOT NULL DEFAULT 100")],
        "meal_log": [("food_id", "TEXT"), ("grams", "REAL")],
        "game_state": [("coins", "INTEGER NOT NULL DEFAULT 0")],
        "user_profile": [("desired_class", "TEXT NOT NULL DEFAULT 'warrior'")],
    }
    for table, cols in adds.items():
        existing = await columns(table)
        for name, decl in cols:
            if name not in existing:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    # One-time backfill: a plain ADD COLUMN defaults every pre-existing food's default_g to 100.
    # Set sensible per-food portions for the seed foods once (skip user-added/edited ones).
    if not await db.kv_get("mig_default_g_v2"):
        from .engine.foods import default_portion
        for r in await db.fetchall("SELECT id, category, custom FROM foods"):
            if not r["custom"]:
                await db.execute("UPDATE foods SET default_g = ? WHERE id = ?",
                                 (default_portion(r["id"], r["category"]), r["id"]))
        await db.kv_set("mig_default_g_v2", "1")


async def _seed_profile(db: Database, config: Config, now: str) -> None:
    exists = await db.fetchval("SELECT 1 FROM user_profile WHERE id = 1")
    if exists:
        return
    await db.execute(
        """INSERT INTO user_profile
           (id, name, sex, age, height_cm, weight_kg, start_weight_kg,
            steps_target, gym_target_sessions, sleep_target_hours,
            protein_factor, deficit_kcal, updated_at)
           VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            config.name, config.sex, config.age, config.height_cm,
            config.weight_kg, config.start_weight_kg, config.steps_target,
            config.gym_target_sessions, config.sleep_target_hours,
            config.protein_factor, config.deficit_kcal, now,
        ),
    )


async def _seed_game_state(db: Database, now: str) -> None:
    exists = await db.fetchval("SELECT 1 FROM game_state WHERE id = 1")
    if not exists:
        await db.execute(
            "INSERT INTO game_state(id, xp, level, respect, cheat_tokens, boss_week, updated_at) "
            "VALUES (1, 0, 1, 50, 0, 0, ?)",
            (now,),
        )


async def _seed_streaks(db: Database) -> None:
    for cat in DEFAULT_STREAK_CATEGORIES:
        await db.execute(
            "INSERT OR IGNORE INTO streaks(category, count, best, last_date) VALUES(?, 0, 0, NULL)",
            (cat,),
        )


async def seed_meals(db: Database) -> None:
    """Insert preset meals from the JSON seed without clobbering user edits."""
    meals = json.loads(paths.MEALS_SEED.read_text(encoding="utf-8"))
    rows = [
        (
            m["id"], m["name"], m.get("contents", ""), float(m["kcal"]),
            float(m["protein_g"]), m.get("max_per_week"), int(m.get("locked", 0)),
            int(m.get("enabled", 1)), m.get("tags", ""),
        )
        for m in meals
    ]
    await db.executemany(
        """INSERT OR IGNORE INTO meals
           (id, name, contents, kcal, protein_g, max_per_week, locked, enabled, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


async def seed_facts(db: Database) -> None:
    """Insert seed facts without overwriting user-added (custom) ones."""
    facts = json.loads(paths.FACTS_SEED.read_text(encoding="utf-8"))
    rows = [
        (
            f["id"], f["title"], f["body"], int(f["verdict"]),
            f.get("tags", ""), f.get("source", ""), 0,
        )
        for f in facts
    ]
    await db.executemany(
        """INSERT OR IGNORE INTO facts (id, title, body, verdict, tags, source, custom)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


async def seed_foods(db: Database) -> None:
    """Insert seed foods (per-100 g macros + default portion) without clobbering user edits."""
    from .engine.foods import default_portion  # local import avoids a circular dependency

    data = json.loads(paths.FOODS_DB.read_text(encoding="utf-8"))
    rows = [
        (
            f["id"], f["name"], f.get("category", "other"), float(f["kcal"]),
            float(f["protein"]), float(f.get("carbs", 0)), float(f.get("fat", 0)),
            float(f.get("default_g") or default_portion(f["id"], f.get("category", "other"))),
            ",".join(f.get("aliases", [])), 0,
        )
        for f in data["foods"]
    ]
    await db.executemany(
        """INSERT OR IGNORE INTO foods
           (id, name, category, kcal, protein, carbs, fat, default_g, aliases, custom)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )


async def seed_rewards(db: Database) -> None:
    """Insert seed rewards (cheat + lifestyle) without overwriting user-added ones."""
    data = json.loads(paths.REWARDS_SEED.read_text(encoding="utf-8"))
    rows = [
        (r["id"], r["name"], r.get("emoji", "🎁"), int(r["cost"]),
         r.get("kind", "leisure"), r.get("meal_id"), 0)
        for r in data
    ]
    await db.executemany(
        "INSERT OR IGNORE INTO rewards(id, name, emoji, cost, kind, meal_id, custom) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


async def seed_notifs(db: Database) -> None:
    """Insert the canonical notification schedule without clobbering user edits.

    Rows survive first-boot with default enabled=1/muted=0; toggles/retimes made via the
    settings UI or /notifs command persist across restarts.
    """
    data = json.loads(paths.NOTIFS_SEED.read_text(encoding="utf-8"))
    rows = [
        (n["key"], n["label"], n["time"], int(n.get("enabled", 1)), int(n.get("muted", 0)))
        for n in data
    ]
    await db.executemany(
        "INSERT OR IGNORE INTO notif_config(key, label, time, enabled, muted) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )


def utcnow_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
