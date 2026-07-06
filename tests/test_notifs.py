"""Notification management (Feature B) — engine + retroactive-complete tests."""

from __future__ import annotations

from mealsentry.engine import nag, notifs

CANONICAL_KEYS = {
    "prep_morning", "meal1", "protein_pace", "protein_pace_aggressive", "meal2",
    "steps", "protein_verdict", "prep_evening", "sleep_winddown", "screens_off",
    "gym_pressure", "weekly_verdict", "shopping", "facts",
}


async def test_seeded_from_json(db):
    ns = await notifs.list_notifs(db)
    keys = {n["key"] for n in ns}
    assert CANONICAL_KEYS.issubset(keys)
    # Every seed row is enabled and un-muted by default.
    for n in ns:
        assert n["enabled"] is True
        assert n["muted"] is False


async def test_toggle_enabled_and_muted(db):
    await notifs.set_enabled(db, "meal1", False)
    n = await notifs.get_notif(db, "meal1")
    assert n["enabled"] is False
    assert n["muted"] is False

    await notifs.set_muted(db, "meal1", True)
    n = await notifs.get_notif(db, "meal1")
    assert n["muted"] is True


async def test_is_active_respects_both_flags(db):
    # baseline
    assert await notifs.is_active(db, "meal2") is True

    await notifs.set_enabled(db, "meal2", False)
    assert await notifs.is_active(db, "meal2") is False

    await notifs.set_enabled(db, "meal2", True)
    await notifs.set_muted(db, "meal2", True)
    assert await notifs.is_active(db, "meal2") is False

    await notifs.set_muted(db, "meal2", False)
    assert await notifs.is_active(db, "meal2") is True


async def test_is_active_unknown_key_defaults_true(db):
    # Ad-hoc situations without a config row must never be silenced by accident.
    assert await notifs.is_active(db, "shopping_countdown_unseeded") is True


async def test_set_time_validates_and_persists(db):
    await notifs.set_time(db, "meal1", "13:45")
    n = await notifs.get_notif(db, "meal1")
    assert n["time"] == "13:45"

    # 'random' passes through unchanged.
    await notifs.set_time(db, "facts", "random")
    assert (await notifs.get_notif(db, "facts"))["time"] == "random"


async def test_get_time_falls_back_on_missing(db):
    h, m = await notifs.get_time(db, "does_not_exist", 8, 30)
    assert (h, m) == (8, 30)
    # 'random' → defaults
    h, m = await notifs.get_time(db, "facts", 12, 0)
    assert (h, m) == (12, 0)


async def test_retroactive_complete_marks_task_done(db, monday):
    """Fire a task via nag.advance so it lands in the tasks table, then confirm it."""
    await nag.advance(db, "meal1", monday)
    open_before = await nag.open_tasks(db, monday)
    assert any(r["task_key"] == "meal1" for r in open_before)

    changed = await nag.confirm(db, "meal1", monday)
    assert changed is True

    open_after = await nag.open_tasks(db, monday)
    assert not any(r["task_key"] == "meal1" for r in open_after)
