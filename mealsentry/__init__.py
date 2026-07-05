"""MealSentry — single-user nutrition/gym/sleep enforcement bot.

The application is *MealSentry*; the aggressive personality delivering the messages
is a swappable *coach* (the first one being "Chad Coach"). Business logic lives in
``mealsentry.engine.*`` with zero Telegram imports; ``bot.py`` and ``api.py`` are thin
adapters over the same SQLite database.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
