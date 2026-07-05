"""Configuration loading: ``config.yaml`` for tunables, environment for secrets.

Secrets never live in the repo. The Telegram token and the single allowed user id come
from ``MEALSENTRY_TOKEN`` / ``MEALSENTRY_USER_ID``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import paths

TOKEN_ENV = "MEALSENTRY_TOKEN"
USER_ID_ENV = "MEALSENTRY_USER_ID"


@dataclass
class Config:
    """Runtime configuration. Biometric defaults seed the DB on first run only."""

    # Identity / biometrics (seed values; overridden by config.yaml)
    name: str = "Athlete"
    sex: str = "male"
    age: int = 38
    height_cm: float = 190.0
    weight_kg: float = 96.0
    start_weight_kg: float = 100.0

    # Targets
    steps_target: int = 11000
    gym_target_sessions: int = 3
    sleep_target_hours: float = 7.0
    protein_factor: float = 1.8
    deficit_kcal: int = 600

    # Eating window
    eat_start: str = "14:30"
    eat_end: str = "22:00"

    # Coach / tone
    active_coach: str = "chad_coach"
    intensity: int = 2

    # Environment
    timezone: str = "Europe/Athens"

    # Storage
    db_path: str = "mealsentry.db"

    # API
    api_host: str = "127.0.0.1"
    api_port: int = 8787

    # Shopping
    chicken_budget_eur: float = 100.0
    shop_store: str = "ΑΒ Βασιλόπουλος"

    # Secrets (from env; not serialized)
    token: str = field(default="", repr=False)
    user_id: int = 0

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def now(self) -> datetime:
        """Timezone-aware current time (DST-safe via zoneinfo)."""
        return datetime.now(self.tz)

    def resolved_db_path(self) -> Path:
        p = Path(self.db_path)
        return p if p.is_absolute() else (paths.ROOT / p)


def load_config(path: Path | None = None, *, require_secrets: bool = True) -> Config:
    """Load config from YAML + environment.

    Falls back to ``config.yaml.example`` if ``config.yaml`` is absent so the app is
    importable/testable without a local config. Secrets are read from the environment.
    """
    cfg_path = path or (paths.CONFIG_FILE if paths.CONFIG_FILE.exists() else paths.CONFIG_EXAMPLE)
    data: dict = {}
    if cfg_path.exists():
        with open(cfg_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}

    known = {f for f in Config.__dataclass_fields__ if f not in {"token", "user_id"}}
    kwargs = {k: v for k, v in data.items() if k in known}
    cfg = Config(**kwargs)

    cfg.token = os.environ.get(TOKEN_ENV, "")
    uid = os.environ.get(USER_ID_ENV, "0")
    try:
        cfg.user_id = int(uid)
    except ValueError:
        cfg.user_id = 0

    if require_secrets:
        missing = []
        if not cfg.token:
            missing.append(TOKEN_ENV)
        if not cfg.user_id:
            missing.append(USER_ID_ENV)
        if missing:
            raise RuntimeError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Set them before starting the bot."
            )
    return cfg
