"""Nutrition math — pure, deterministic, no I/O.

Targets are recomputed after every ``/weight`` entry from the profile plus the last
week of logged gym/steps data. Kept side-effect-free so it is trivially unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass

ACTIVITY_BASE = 1.35
ACTIVITY_STEP = 0.05
ACTIVITY_CAP = 1.55
GYM_SESSIONS_FOR_BONUS = 2
STEPS_FOR_BONUS = 10_000
STALL_MIN_LOSS_KG = 0.5


@dataclass(frozen=True)
class Targets:
    bmr: float
    activity_factor: float
    tdee: float
    calorie_target: int
    protein_floor_g: int


def bmr_mifflin(sex: str, weight_kg: float, height_cm: float, age: int) -> float:
    """Mifflin-St Jeor basal metabolic rate (kcal/day)."""
    base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age
    return base + (5.0 if sex.lower().startswith("m") else -161.0)


def activity_factor(gym_sessions_this_week: int, avg_steps_7d: float) -> float:
    """Activity multiplier derived from logged data (1.35 base .. 1.55 cap)."""
    factor = ACTIVITY_BASE
    if gym_sessions_this_week >= GYM_SESSIONS_FOR_BONUS:
        factor += ACTIVITY_STEP
    if avg_steps_7d >= STEPS_FOR_BONUS:
        factor += ACTIVITY_STEP
    return min(factor, ACTIVITY_CAP)


def tdee(bmr: float, factor: float) -> float:
    return bmr * factor


def calorie_target(tdee_val: float, deficit_kcal: int) -> int:
    return int(round(tdee_val - deficit_kcal))


def protein_floor(weight_kg: float, protein_factor: float) -> int:
    """The non-negotiable protein floor in grams ('death limit')."""
    return int(round(weight_kg * protein_factor))


def compute_targets(
    *,
    sex: str,
    weight_kg: float,
    height_cm: float,
    age: int,
    protein_factor: float,
    deficit_kcal: int,
    gym_sessions_this_week: int = 0,
    avg_steps_7d: float = 0.0,
) -> Targets:
    """Full recompute after a weigh-in. Returns all derived targets in one shot."""
    bmr = bmr_mifflin(sex, weight_kg, height_cm, age)
    factor = activity_factor(gym_sessions_this_week, avg_steps_7d)
    total = tdee(bmr, factor)
    return Targets(
        bmr=round(bmr, 1),
        activity_factor=round(factor, 2),
        tdee=round(total, 1),
        calorie_target=calorie_target(total, deficit_kcal),
        protein_floor_g=protein_floor(weight_kg, protein_factor),
    )


def weekly_average(weights: list[float]) -> float | None:
    """Average of a week's weigh-ins (e.g. Tue/Thu/Sat mornings)."""
    return round(sum(weights) / len(weights), 2) if weights else None


def is_stalled(weekly_avgs: list[float], min_loss: float = STALL_MIN_LOSS_KG) -> bool:
    """True if the last two week-over-week changes both show < ``min_loss`` kg loss.

    ``weekly_avgs`` is ordered oldest→newest; needs at least three points to see two
    consecutive deltas.
    """
    if len(weekly_avgs) < 3:
        return False
    loss_a = weekly_avgs[-3] - weekly_avgs[-2]
    loss_b = weekly_avgs[-2] - weekly_avgs[-1]
    return loss_a < min_loss and loss_b < min_loss


@dataclass(frozen=True)
class CutProposal:
    item: str
    kcal: int
    new_deficit: int


def propose_cut(current_deficit: int, cut_kcal: int = 200) -> CutProposal:
    """Name a concrete −kcal cut when weight stalls (honey vs protein bar, alternating)."""
    # Alternate the named item based on the resulting deficit so it does not always
    # suggest the same thing.
    new_deficit = current_deficit + cut_kcal
    item = "μέλι (10g λιγότερο)" if (new_deficit // cut_kcal) % 2 == 0 else "protein bar (μισή)"
    return CutProposal(item=item, kcal=cut_kcal, new_deficit=new_deficit)
