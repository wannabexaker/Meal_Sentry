"""Granular weighed-food logging: default portions, custom grams, meal_log schema, migration."""

import sqlite3

from mealsentry.config import Config
from mealsentry.db import Database, init_db
from mealsentry.engine import foods


async def test_food_has_default_portion(db):
    assert (await foods.get_food(db, "chicken_breast"))["default_g"] == 150
    assert (await foods.get_food(db, "egg"))["default_g"] == 50
    assert (await foods.get_food(db, "olive_oil"))["default_g"] == 10


async def test_eat_food_default_and_custom(service, monday):
    r = await service.eat_food("chicken_breast", monday)          # default 150 g
    assert r["logged"]["grams"] == 150 and r["logged"]["kcal"] == 180
    r2 = await service.eat_food("rice_cooked", monday, 200)        # custom 200 g
    assert r2["logged"]["kcal"] == 260
    assert r2["today"]["kcal"] == 440                             # both counted


async def test_eat_food_records_food_id_and_grams(db, service, monday):
    await service.eat_food("banana", monday)
    row = await db.fetchone("SELECT food_id, grams, note FROM meal_log ORDER BY id DESC LIMIT 1")
    assert row["food_id"] == "banana" and row["grams"] == 120
    assert "120g" in row["note"]


async def test_set_default_g(db, service, monday):
    await foods.set_default_g(db, "chicken_breast", 200)
    r = await service.eat_food("chicken_breast", monday)
    assert r["logged"]["grams"] == 200


async def test_migration_adds_columns_to_old_db(tmp_path):
    """An old DB missing default_g / food_id / grams must migrate cleanly on init_db."""
    p = str(tmp_path / "old.db")
    con = sqlite3.connect(p)
    con.executescript(
        "CREATE TABLE foods (id TEXT PRIMARY KEY, name TEXT, category TEXT, kcal REAL, "
        "protein REAL, carbs REAL, fat REAL, aliases TEXT, custom INTEGER);"
        "CREATE TABLE meal_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, date TEXT, "
        "meal_id TEXT, fraction REAL, kcal REAL, protein_g REAL, note TEXT);"
    )
    # a pre-existing seed food (no default_g column yet) — must be backfilled, not left at 100
    con.execute("INSERT INTO foods VALUES ('chicken_breast','Στήθος','protein',120,23,0,2.6,'',0)")
    con.commit()
    con.close()

    cfg = Config()
    cfg.db_path = p
    db = Database(p)
    await db.connect()
    await init_db(db, cfg)  # runs _migrate
    fcols = {r["name"] for r in await db.fetchall("PRAGMA table_info(foods)")}
    mcols = {r["name"] for r in await db.fetchall("PRAGMA table_info(meal_log)")}
    assert "default_g" in fcols
    assert {"food_id", "grams"} <= mcols
    # seed still worked through the migrated schema
    assert (await foods.get_food(db, "chicken_breast"))["default_g"] == 150
    await db.close()
