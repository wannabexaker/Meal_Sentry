"""DB-backed ingredient macro database (per 100 g).

Foods live in the ``foods`` table (seeded from ``data/foods.json``) so the user can add
their own — salads, snacks, whatever — at runtime without a code change. Used by ``/newmeal``
and by the natural-language parser. Replaces the old static-JSON lookup that lived in
``engine/meals.py``.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from ..db import Database


def normalize(text: str) -> str:
    """Lowercase + strip Greek accents for fuzzy ingredient matching."""
    nfd = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").strip()


# Default logged portion (grams) per food — one tap logs this, adjustable afterwards.
DEFAULT_PORTIONS: dict[str, float] = {
    "egg": 50, "egg_white": 33, "chicken_breast": 150, "beef_mince": 150, "pork_tender": 150,
    "pork_sausage": 100, "salmon": 150, "tuna_water": 100, "turkey_breast": 150, "bacon": 30,
    "cottage": 150, "yogurt_2": 170, "yogurt_0": 170, "milk_15": 200, "parmesan": 15, "feta": 50,
    "whey": 30, "pea_protein": 20, "creatine": 5, "oats": 60, "rice_dry": 60, "rice_cooked": 150,
    "pasta_ww": 70, "pasta_cooked": 150, "potato": 200, "sweet_potato": 200, "bread_ww": 30,
    "pita": 60, "rice_cakes": 20, "quinoa": 150, "chickpeas": 150, "lentils": 150, "banana": 120,
    "apple": 150, "berries": 100, "lettuce": 80, "spinach": 60, "tomato": 100, "cucumber": 100,
    "carrot": 80, "pepper": 80, "onion": 50, "broccoli": 100, "corn": 100, "avocado": 70,
    "olives": 30, "olive_oil": 10, "peanut_butter": 20, "mixed_nuts": 30, "tahini": 15,
    "caesar_dressing": 20, "croutons": 15, "mustard": 10, "balsamic": 10, "honey": 15,
    "protein_bar": 60, "insalatissime": 150, "halva": 50, "ice_cream": 100,
}
CATEGORY_PORTIONS: dict[str, float] = {
    "protein": 150, "dairy": 150, "carb": 100, "fat": 20, "veg": 100, "fruit": 120,
    "legume": 150, "sauce": 20, "supplement": 30, "snack": 60, "treat": 50,
    "sweetener": 15, "other": 100,
}


def default_portion(food_id: str, category: str) -> float:
    return DEFAULT_PORTIONS.get(food_id, CATEGORY_PORTIONS.get(category, 100))


def _row_to_food(row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "category": row["category"],
        "kcal": row["kcal"], "protein": row["protein"], "carbs": row["carbs"],
        "fat": row["fat"], "default_g": row["default_g"],
        "aliases": [a for a in (row["aliases"] or "").split(",") if a],
        "custom": bool(row["custom"]),
    }


async def food_index(db: Database) -> dict[str, dict]:
    """Build a normalized-key → food lookup from the DB (small table, built per call)."""
    rows = await db.fetchall("SELECT * FROM foods")
    index: dict[str, dict] = {}
    for row in rows:
        food = _row_to_food(row)
        for key in {food["id"], food["name"], *food["aliases"]}:
            index[normalize(key)] = food
    return index


async def find_food(db: Database, query: str) -> dict | None:
    """Resolve an ingredient name/alias to a food record (per-100 g macros)."""
    idx = await food_index(db)
    norm = normalize(query)
    if norm in idx:
        return idx[norm]
    # substring fallback: 'στηθος κοτοπουλο' -> 'κοτοπουλο'
    for key, food in idx.items():
        if key and (key in norm or norm in key):
            return food
    return None


@dataclass
class MacroTotals:
    kcal: float
    protein: float
    carbs: float
    fat: float
    unresolved: list[str]

    def rounded(self) -> MacroTotals:
        return MacroTotals(round(self.kcal), round(self.protein, 1), round(self.carbs, 1),
                           round(self.fat, 1), self.unresolved)


async def compute_macros(db: Database, ingredients: list[tuple[str, float]]) -> MacroTotals:
    """Sum macros for a list of (ingredient, grams) using the DB food table (per 100 g)."""
    idx = await food_index(db)
    kcal = protein = carbs = fat = 0.0
    unresolved: list[str] = []
    for name, grams in ingredients:
        food = idx.get(normalize(name))
        if food is None:
            for key, cand in idx.items():
                if key and (key in normalize(name) or normalize(name) in key):
                    food = cand
                    break
        if food is None:
            unresolved.append(name)
            continue
        f = grams / 100.0
        kcal += food["kcal"] * f
        protein += food["protein"] * f
        carbs += food["carbs"] * f
        fat += food["fat"] * f
    return MacroTotals(kcal, protein, carbs, fat, unresolved).rounded()


# --------------------------------------------------------------------------- CRUD
async def list_foods(db: Database, category: str | None = None) -> list[dict]:
    if category:
        rows = await db.fetchall("SELECT * FROM foods WHERE category = ? ORDER BY name",
                                 (category,))
    else:
        rows = await db.fetchall("SELECT * FROM foods ORDER BY category, name")
    return [_row_to_food(r) for r in rows]


async def get_food(db: Database, food_id: str) -> dict | None:
    row = await db.fetchone("SELECT * FROM foods WHERE id = ?", (food_id,))
    return _row_to_food(row) if row else None


async def add_food(
    db: Database, food_id: str, name: str, kcal: float, protein: float,
    carbs: float = 0.0, fat: float = 0.0, *, category: str = "other",
    default_g: float = 100, aliases: list[str] | None = None,
) -> dict:
    await db.execute(
        """INSERT INTO foods(id, name, category, kcal, protein, carbs, fat, default_g, aliases, custom)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
           ON CONFLICT(id) DO UPDATE SET
             name=excluded.name, category=excluded.category, kcal=excluded.kcal,
             protein=excluded.protein, carbs=excluded.carbs, fat=excluded.fat,
             default_g=excluded.default_g, aliases=excluded.aliases""",
        (food_id, name, category, kcal, protein, carbs, fat, default_g, ",".join(aliases or [])),
    )
    return await get_food(db, food_id)


async def set_default_g(db: Database, food_id: str, grams: float) -> None:
    await db.execute("UPDATE foods SET default_g = ? WHERE id = ?", (grams, food_id))


async def delete_food(db: Database, food_id: str) -> None:
    await db.execute("DELETE FROM foods WHERE id = ?", (food_id,))
