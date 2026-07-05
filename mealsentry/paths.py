"""Filesystem locations for bundled assets, resolved relative to the repo root.

The Python package lives in ``<root>/mealsentry`` while data/coach/db assets live at
``<root>/{data,coaches,tone,db}``. Resolving from ``__file__`` keeps asset loading
independent of the current working directory (important under systemd).
"""

from __future__ import annotations

from pathlib import Path

ROOT: Path = Path(__file__).resolve().parent.parent

DATA_DIR: Path = ROOT / "data"
COACHES_DIR: Path = ROOT / "coaches"
TONE_DIR: Path = ROOT / "tone"
DB_DIR: Path = ROOT / "db"

MEALS_SEED: Path = DATA_DIR / "meals.json"
FOODS_DB: Path = DATA_DIR / "foods.json"
FACTS_SEED: Path = DATA_DIR / "facts_gr.json"
SCHEMA_SQL: Path = DB_DIR / "schema.sql"

CONFIG_FILE: Path = ROOT / "config.yaml"
CONFIG_EXAMPLE: Path = ROOT / "config.yaml.example"
