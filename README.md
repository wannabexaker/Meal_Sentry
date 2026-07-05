# MealSentry

Single-user Telegram bot that enforces a nutrition, gym, sleep, and shopping protocol through escalating reminders

## Overview

The bot logs one user's meals, weight, steps, gym sessions, sleep, food inventory, and spending, then escalates Telegram reminders when a target is missed. Calorie and protein targets recompute after every weigh-in from the Mifflin-St Jeor equation and the last week of logged activity. Business logic sits in an engine layer with no Telegram or HTTP imports; the Telegram bot and a FastAPI backend are thin adapters over the same SQLite database. The messaging persona is decoupled from the application: `Chad Coach` is the first of several possible swappable coaches. Bot-facing messages are Greek; code, configuration, and this document are English.

## Features

- Recomputes the calorie target and protein floor after each `/weight` entry using Mifflin-St Jeor plus an activity factor derived from logged gym sessions and step counts.
- Per-task nag state machine (`PENDING → NAGGED_1 → NAGGED_2 → NAGGED_3 → FAILED/DONE`) with 30-minute escalation. Every ping is stored and quoted back verbatim when a task fails.
- Cron schedule for prep checks, meals, protein checkpoints, steps, sleep windows, and Tuesday/Saturday shopping runs, evaluated in Europe/Athens with DST handling.
- Gym pressure model: three sessions per week, rising ping frequency while the weekday session is still unlogged, Friday last-call, Sunday verdict.
- XP, ten levels, a respect meter that selects the message tone tier, per-category streaks, cheat tokens, and boss weeks.
- Inventory burn-rate prediction and an auto-generated shopping list that accounts for the store being closed on Sundays; monthly spend tracking against a category budget.
- Daily nutrition-myth trivia with a 1–5 evidence rating and no repeat within 60 days, seeded with about 60 entries.
- Coach persona defined by a YAML manifest and a message template set with at least five variants per situation.
- Single-user whitelist by Telegram id. Scheduler state is recomputed from the database on restart, so a reboot mid-escalation does not drop a pending nag.
- Read-only FastAPI backend on port 8787 over the same database.

## Architecture

Requests enter through an adapter (`bot.py` for Telegram, `api.py` for HTTP), which calls `service.py`, which composes the engine modules and writes to SQLite. The scheduler runs cron jobs and a 5-minute escalation tick; it renders text through the active coach and sends it through a notifier callback, so it holds no Telegram import itself. All state lives in one SQLite database shared by both adapters.

### Components

| Component | Role |
|---|---|
| `mealsentry/engine/` | Pure logic: `math`, `meals`, `nag`, `gym`, `game`, `inventory`, `sleep`, `facts` |
| `mealsentry/service.py` | Composes engine calls and XP side effects; returns plain data |
| `mealsentry/tone.py` | Loads a coach persona and renders a message for a situation and tone tier |
| `mealsentry/scheduler.py` | APScheduler cron jobs and the escalation tick |
| `mealsentry/bot.py` | Telegram commands, inline keyboards, whitelist |
| `mealsentry/api.py` | FastAPI read-only endpoints |
| `coaches/`, `tone/` | Coach manifest and Greek message templates |
| `db/schema.sql` | SQLite schema |

## Tech Stack

| Technology | Role |
|---|---|
| Python 3.11+ | Runtime |
| `python-telegram-bot` 21+ | Telegram bot (async) |
| APScheduler | Cron scheduling and escalation tick |
| `aiosqlite` | Async SQLite access |
| FastAPI + Uvicorn | REST backend |
| matplotlib | Weight-trend chart |
| PyYAML | Configuration and coach templates |

## Installation

```bash
git clone https://github.com/wannabexaker/Meal_Sentry
cd Meal_Sentry
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp config.yaml.example config.yaml
```

Secrets are read from the environment, never from a file in the repository:

```bash
export MEALSENTRY_TOKEN="<token from @BotFather>"
export MEALSENTRY_USER_ID="<your Telegram numeric id>"
```

On a Raspberry Pi, `bash deploy/install.sh` creates the virtualenv, writes
`mealsentry.env`, and installs the two hardened systemd units.

## Usage

Start the bot and its scheduler:

```bash
python -m mealsentry.bot
```

Start the read-only backend on `http://127.0.0.1:8787`:

```bash
python -m mealsentry.api
```

Run the checks:

```bash
ruff check .
pytest -q
```

Primary commands:

| Command | Action |
|---|---|
| `/status` | Day snapshot: targets, intake, gym, level, respect |
| `/ate <meal_id> [fraction]` | Log a meal or a portion of one |
| `/weight <kg>` | Log weight and recompute targets |
| `/steps <n>`, `/gym done [min]` | Log steps or a gym session |
| `/sleep <bed> <wake>` | Log sleep, e.g. `/sleep 23:40 07:10` |
| `/stock <item> <g>`, `/spent <€> <cat>` | Update inventory or spending |
| `/meals`, `/newmeal` | List meals (buttons) or add one |
| `/report`, `/charts` | Weekly report or weight-trend chart |
| `/fact`, `/newfact` | Nutrition trivia or add an entry |

## Project Structure

```text
Meal_Sentry/
├── mealsentry/
│   ├── bot.py              — Telegram adapter
│   ├── api.py              — FastAPI backend
│   ├── scheduler.py        — cron jobs + escalation tick
│   ├── service.py          — engine composition + XP side effects
│   ├── tone.py             — coach persona renderer
│   ├── config.py, db.py    — config loading, async SQLite
│   ├── charts.py, util.py, paths.py
│   └── engine/             — math, meals, nag, gym, game, inventory, sleep, facts
├── coaches/chad_coach.yaml — coach manifest
├── tone/templates_gr.yaml  — Greek templates, ≥5 variants per situation
├── data/                   — meals.json, foods.json, facts_gr.json
├── db/schema.sql           — SQLite schema
├── deploy/                 — systemd units + install.sh
├── tests/                  — pytest suite
└── config.yaml.example
```

## Notes

- The engine layer imports neither Telegram nor HTTP, so the bot, the API, and the test
  suite exercise the same code paths.
- A coach is data, not code. Adding one is a new `coaches/<id>.yaml` and template file plus
  an `active_coach` change in `config.yaml`; the engine is untouched.
- The FastAPI backend is read-only and binds to loopback. It exists as the backend for a
  future mobile client. If `api_host` is changed to a non-loopback address, it needs an
  authenticating reverse proxy in front of it.
- Bot messages never insult or body-shame; the tone is built from the user's own logged
  numbers and the timestamps of ignored warnings.

## Future Improvements

- Additional coach personas beyond Chad Coach, selected per user via `active_coach`.
- A mobile client against the existing FastAPI backend.
