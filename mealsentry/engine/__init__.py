"""Engine layer — all business logic, zero Telegram/HTTP imports.

Each module exposes plain functions or small classes that operate on primitives or the
``Database`` wrapper, so both the Telegram bot and the FastAPI backend reuse them and the
whole layer is unit-testable in isolation.
"""
