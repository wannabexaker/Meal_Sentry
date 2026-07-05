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
    now = config.now().isoformat(timespec="seconds")
    await _seed_profile(db, config, now)
    await _seed_game_state(db, now)
    await _seed_streaks(db)
    await seed_meals(db)
    await seed_facts(db)


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


def utcnow_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
