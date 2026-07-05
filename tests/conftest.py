"""Shared pytest fixtures: an isolated temp DB seeded like production, plus a Service."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from mealsentry.config import Config
from mealsentry.db import Database, init_db
from mealsentry.service import Service

ATHENS = ZoneInfo("Europe/Athens")


@pytest.fixture
def cfg(tmp_path) -> Config:
    c = Config()
    c.timezone = "Europe/Athens"
    c.db_path = str(tmp_path / "test.db")
    return c


@pytest_asyncio.fixture
async def db(cfg):
    d = Database(cfg.db_path)
    await d.connect()
    await init_db(d, cfg)
    yield d
    await d.close()


@pytest_asyncio.fixture
async def service(db, cfg) -> Service:
    return Service(db, cfg)


@pytest.fixture
def monday() -> datetime:
    # 2026-07-06 is a Monday, 19:30 local.
    return datetime(2026, 7, 6, 19, 30, tzinfo=ATHENS)
