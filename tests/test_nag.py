"""Nag engine state machine + receipts + boot recovery."""

from datetime import timedelta

from mealsentry.engine import nag


async def test_escalation_to_failed_with_receipts(db, monday):
    t0 = monday
    r1 = await nag.advance(db, "meal2", t0)
    await nag.record_warning(db, "meal2", t0, r1.level, "ping1")
    r2 = await nag.advance(db, "meal2", t0 + timedelta(minutes=30))
    await nag.record_warning(db, "meal2", t0 + timedelta(minutes=30), r2.level, "ping2")
    r3 = await nag.advance(db, "meal2", t0 + timedelta(minutes=60))
    await nag.record_warning(db, "meal2", t0 + timedelta(minutes=60), r3.level, "ping3")
    r4 = await nag.advance(db, "meal2", t0 + timedelta(minutes=90))

    assert [r1.new_state, r2.new_state, r3.new_state, r4.new_state] == [
        nag.NAGGED_1, nag.NAGGED_2, nag.NAGGED_3, nag.FAILED]
    assert [r1.level, r2.level, r3.level] == [1, 2, 3]
    assert r4.kind == "failed"
    assert r4.warn_times == ["19:30", "20:00", "20:30"]  # receipts quoted back


async def test_confirm_stops_escalation(db, monday):
    await nag.advance(db, "meal1", monday)
    changed = await nag.confirm(db, "meal1", monday)
    assert changed is True
    # confirming again is a no-op
    assert await nag.confirm(db, "meal1", monday) is False
    # advancing a DONE task does nothing
    res = await nag.advance(db, "meal1", monday + timedelta(minutes=30))
    assert res.notify is False and res.terminal is True


async def test_snooze_delays_next_ts(db, monday):
    await nag.advance(db, "steps", monday)
    await nag.snooze(db, "steps", monday, 30)
    # immediately after snooze nothing is due
    due_now = await nag.due_for_escalation(db, monday + timedelta(minutes=5))
    assert "steps" not in due_now
    # after the snooze window it is due again
    due_later = await nag.due_for_escalation(db, monday + timedelta(minutes=31))
    assert "steps" in due_later


async def test_due_for_escalation_recovers_after_reboot(db, monday):
    # ping sets next_ts +30; a "reboot" tick 31 min later must find it
    await nag.advance(db, "prep_evening", monday)
    due = await nag.due_for_escalation(db, monday + timedelta(minutes=31))
    assert "prep_evening" in due


async def test_ensure_task_idempotent(db, monday):
    await nag.ensure_task(db, "meal1", monday)
    await nag.ensure_task(db, "meal1", monday)
    rows = await db.fetchall("SELECT * FROM tasks WHERE task_key='meal1'")
    assert len(rows) == 1
