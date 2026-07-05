"""Sleep tracking + consecutive-short-night escalation."""

from datetime import timedelta

from mealsentry.engine import sleep


async def test_log_sleep_computes_hours(db, monday):
    entry = await sleep.log_sleep(db, monday, "23:30", "07:00", target_hours=7.0)
    assert entry.hours == 7.5
    assert entry.below_target is False


async def test_consecutive_short_nights(db, monday):
    # three consecutive nights under target
    for i in range(3):
        day = monday + timedelta(days=i)
        await sleep.log_sleep(db, day, "01:00", "06:00", target_hours=7.0)  # 5h
    count = await sleep.consecutive_short_nights(db, monday + timedelta(days=2), 7.0)
    assert count == 3


async def test_good_night_breaks_short_streak(db, monday):
    await sleep.log_sleep(db, monday, "01:00", "06:00", 7.0)               # short
    await sleep.log_sleep(db, monday + timedelta(days=1), "23:00", "07:30", 7.0)  # good
    count = await sleep.consecutive_short_nights(db, monday + timedelta(days=1), 7.0)
    assert count == 0
