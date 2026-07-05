"""Inventory: burn-rate, runout prediction, shopping list, spend, Sunday-closed runs."""

from mealsentry.engine import inventory, meals


async def test_burn_rate_and_runout(db, monday):
    await inventory.set_stock(db, "chicken", 800, monday)
    # three chicken portions logged in the lookback window (200g each)
    for _ in range(3):
        await meals.log_meal(db, "chicken", monday)
    burn = await inventory.burn_rate_per_day(db, "chicken", monday)
    assert burn == round(3 * 200 / 14, 1)  # ~42.9 g/day
    pred = await inventory.predict_runout(db, "chicken", monday)
    assert pred.days_left is not None and pred.runout_date is not None


async def test_shopping_list_shortfall(db, monday):
    # heavy chicken consumption, empty stock -> chicken appears on the list
    for _ in range(5):
        await meals.log_meal(db, "chicken", monday)
    lst = await inventory.shopping_list(db, monday)
    assert any(i["item"] == "chicken" and i["need_g"] > 0 for i in lst)


def test_upcoming_runs_skip_sunday(monday):
    runs = inventory.upcoming_runs(monday, 3)
    from datetime import datetime
    for date_str, _label in runs:
        wd = datetime.fromisoformat(date_str).weekday()
        assert wd in (1, 5)  # Tue or Sat only, never Sunday(6)


async def test_monthly_spend(db, monday):
    await inventory.add_spend(db, 23.50, "chicken", monday)
    total = await inventory.add_spend(db, 20.00, "chicken", monday)
    assert total == 43.50
    report = await inventory.spend_report(db, monday)
    assert report["by_category"]["chicken"] == 43.50
