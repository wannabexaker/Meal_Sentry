"""Coach persona rendering, tier selection, variant coverage, safe formatting."""

from mealsentry.tone import Coach


def test_load_and_render():
    c = Coach.load("chad_coach", intensity=2)
    assert c.display_name == "Chad Coach"
    text = c.render("meal_reminder", respect=20, meal="Κοτόπουλο")
    assert "Κοτόπουλο" in text


def test_tier_selection_by_respect():
    c = Coach.load("chad_coach", intensity=2)
    low = c.render("praise", respect=10, xp=10)
    high = c.render("praise", respect=90, xp=10)
    assert low and high  # both tiers render


def test_missing_placeholder_is_safe():
    c = Coach.load("chad_coach")
    # meal_failed expects {warn_times}; omit it -> no crash, placeholder becomes '?'
    out = c.render("meal_failed", respect=20, meal="X")
    assert "?" in out


def test_all_situations_have_five_variants():
    c = Coach.load("chad_coach")
    for key, entry in c.templates.items():
        if isinstance(entry, dict):
            for tier, variants in entry.items():
                assert len(variants) >= 5, f"{key}:{tier} has {len(variants)}"
        else:
            assert len(entry) >= 5, f"{key} has {len(entry)}"


def test_intensity_biases_harsher():
    hard = Coach.load("chad_coach", intensity=3)
    # MID respect with intensity 3 should resolve to the LOW tier internally
    assert hard._effective_tier("MID") == "LOW"
