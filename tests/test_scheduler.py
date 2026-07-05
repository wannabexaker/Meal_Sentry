"""Scheduler dry-run: fire a task, escalate it through the tick, and confirm the coach
renders + the failure penalty lands — all without APScheduler or a real clock.
"""

from datetime import timedelta

import pytest

from mealsentry.engine import game
from mealsentry.scheduler import NagScheduler
from mealsentry.tone import Coach


class FakeNotifier:
    def __init__(self):
        self.messages = []

    async def __call__(self, text, *, buttons=None, photo=None):
        self.messages.append({"text": text, "buttons": buttons})


@pytest.fixture
def sched(db, cfg):
    coach = Coach.load("chad_coach", intensity=2)
    notifier = FakeNotifier()
    s = NagScheduler(cfg, db, coach, notifier)
    return s, notifier


async def test_fire_task_pings_with_buttons(sched, monday):
    s, notifier = sched
    s.now = lambda: monday
    await s.fire_task("meal1")
    assert len(notifier.messages) == 1
    msg = notifier.messages[0]
    assert msg["buttons"] is not None            # one-tap confirm keyboard
    assert "Γεύμα 1" in msg["text"]
    # the ping was recorded as a receipt
    warns = await s.db.fetchall("SELECT * FROM warnings WHERE task_key='meal1'")
    assert len(warns) == 1


async def test_escalation_to_failure_penalizes(sched, monday):
    s, notifier = sched
    respect_before = (await game.get_state(s.db))["respect"]

    for minute in (0, 30, 60, 90):
        s.now = lambda m=minute: monday + timedelta(minutes=m)
        if minute == 0:
            await s.fire_task("meal2")
        else:
            await s._escalation_tick()

    # 3 pings (with buttons) + 1 failure message (no buttons)
    assert len(notifier.messages) == 4
    assert notifier.messages[-1]["buttons"] is None
    assert ("19:30" in notifier.messages[-1]["text"]
            or "20:" in notifier.messages[-1]["text"])  # receipts quoted

    respect_after = (await game.get_state(s.db))["respect"]
    assert respect_after < respect_before  # failure dropped respect


async def test_push_fact_sends_message(sched, monday):
    s, notifier = sched
    s.now = lambda: monday
    await s.push_fact()
    assert len(notifier.messages) == 1
    assert "verdict" in notifier.messages[0]["text"].lower()
