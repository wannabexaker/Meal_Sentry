"""Date / parsing helpers."""

import pytest

from mealsentry import util


def test_week_bounds_monday_to_sunday():
    # 2026-07-05 is a Sunday
    assert util.week_bounds("2026-07-05") == ("2026-06-29", "2026-07-05")
    # 2026-07-06 is a Monday
    assert util.week_bounds("2026-07-06") == ("2026-07-06", "2026-07-12")


def test_month_bounds():
    assert util.month_bounds("2026-07-15") == ("2026-07-01", "2026-07-31")
    assert util.month_bounds("2026-02-10") == ("2026-02-01", "2026-02-28")


def test_parse_hhmm():
    assert util.parse_hhmm("23:40") == (23, 40)
    assert util.parse_hhmm("7.05") == (7, 5)
    with pytest.raises(ValueError):
        util.parse_hhmm("25:00")


def test_parse_hhmm_4digit():
    assert util.parse_hhmm("2340") == (23, 40)
    assert util.parse_hhmm("0000") == (0, 0)
    assert util.parse_hhmm("1750") == (17, 50)
    assert util.parse_hhmm("730") == (7, 30)   # HMM
    assert util.parse_hhmm("7") == (7, 0)       # hour only
    assert util.hours_between("2340", "0710") == 7.5
    with pytest.raises(ValueError):
        util.parse_hhmm("2599")


def test_hours_between_wraps_past_midnight():
    assert util.hours_between("23:30", "07:00") == 7.5
    assert util.hours_between("22:00", "06:00") == 8.0
    assert util.hours_between("00:15", "08:15") == 8.0
