"""Fun facts: 60-day no-repeat selection, custom add, verdict stars."""

from mealsentry.engine import facts


def test_verdict_stars():
    assert facts.verdict_stars(1) == "★☆☆☆☆"
    assert facts.verdict_stars(5) == "★★★★★"
    assert facts.verdict_stars(3) == "★★★☆☆"


async def test_no_repeat_within_window(db, monday):
    seen = set()
    for _ in range(15):
        f = await facts.pick_fact(db, monday)
        assert f is not None
        assert f.id not in seen  # never repeats while pool has unseen entries
        seen.add(f.id)


async def test_add_custom_fact(db):
    f = await facts.add_fact(db, "Δοκιμαστικό", "Ένα σώμα κειμένου.", 4)
    assert f.custom is True
    c = await facts.counts(db)
    assert c["custom"] == 1
    assert c["total"] == c["seed"] + 1


async def test_seed_pool_size(db):
    c = await facts.counts(db)
    assert c["seed"] >= 60  # spec: ~60 seed facts
