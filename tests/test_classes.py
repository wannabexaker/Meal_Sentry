"""RPG classes: epithet computation, best-fit, and the service/dashboard wiring."""

from mealsentry.engine import classes


def test_epithet_troll_labels():
    # 190 cm / 95 kg aspiring to be an Assassin → "Ψηλός Χοντρός Assassin"
    d = classes.describe(190, 95, "assassin")
    assert d["epithet"] == ["Ψηλός", "Χοντρός"]
    assert d["title"] == "Ψηλός Χοντρός Assassin"
    assert d["emoji"] == "🗡️" and d["fit"] is False


def test_good_fit_has_no_epithet():
    d = classes.describe(190, 100, "warrior")   # warrior ideal 178-200 / 85-130
    assert d["fit"] is True
    assert d["title"] == "Επίδοξος Warrior"


def test_short_and_underweight():
    d = classes.describe(155, 55, "warrior")
    assert "Κοντός" in d["epithet"] and "Αδύνατος" in d["epithet"]


def test_best_fit_and_unknown_fallback():
    assert classes.best_fit(190, 100) == "warrior"
    assert classes.best_fit(168, 65) in ("assassin", "monk")
    assert classes.describe(180, 80, "wizard")["class_id"] == classes.DEFAULT_CLASS


async def test_set_class_and_dashboard(service, monday):
    c = await service.set_class("assassin")
    assert c["class_id"] == "assassin" and c["emoji"] == "🗡️"
    d = await service.dashboard(monday)
    assert d["character"]["class"]["class_name"] == "Assassin"
    assert "Χοντρός" in d["character"]["class"]["title"]   # 96 kg default → χοντρός assassin
