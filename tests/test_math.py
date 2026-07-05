"""Nutrition math engine — pure and deterministic."""

import pytest

from mealsentry.engine import math


def test_bmr_mifflin_male():
    # 10*96 + 6.25*190 - 5*38 + 5 = 960 + 1187.5 - 190 + 5 = 1962.5
    assert math.bmr_mifflin("male", 96, 190, 38) == 1962.5


def test_bmr_mifflin_female_differs_by_166():
    male = math.bmr_mifflin("male", 70, 170, 30)
    female = math.bmr_mifflin("female", 70, 170, 30)
    assert male - female == 166.0


def test_activity_factor_bounds_and_bonuses():
    assert math.activity_factor(0, 0) == pytest.approx(1.35)
    assert math.activity_factor(2, 0) == pytest.approx(1.40)
    assert math.activity_factor(2, 10000) == pytest.approx(1.45)
    # cap never exceeded
    assert math.activity_factor(9, 99999) <= math.ACTIVITY_CAP


def test_calorie_target_and_protein_floor():
    assert math.calorie_target(2800, 600) == 2200
    assert math.protein_floor(96, 1.8) == 173


def test_compute_targets_full():
    t = math.compute_targets(
        sex="male", weight_kg=96, height_cm=190, age=38,
        protein_factor=1.8, deficit_kcal=600,
        gym_sessions_this_week=2, avg_steps_7d=11000,
    )
    assert t.activity_factor == 1.45
    assert t.protein_floor_g == 173
    assert t.calorie_target == math.calorie_target(t.tdee, 600)


def test_is_stalled():
    assert math.is_stalled([96.0, 95.8, 95.6]) is True     # both losses < 0.5
    assert math.is_stalled([96.0, 95.0, 94.0]) is False    # 1.0 kg losses
    assert math.is_stalled([96.0, 95.6]) is False          # not enough points


def test_propose_cut_names_item():
    p = math.propose_cut(600, 200)
    assert p.kcal == 200
    assert p.new_deficit == 800
    assert p.item  # names a concrete item
