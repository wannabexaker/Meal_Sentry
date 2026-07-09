# MealSentry 🥩🤖

> A single-user Telegram bot that acts as a blunt, no-excuses fitness coach. It tracks your
> nutrition, gym, sleep, steps and shopping, then escalates reminders (in Greek) until you hit
> your targets. Self-host it on a Raspberry Pi — or any always-on machine — and it will message
> you the same way it messaged the person it was built for.

**Unofficial fan project.** Build it, run it for yourself, make it your own. It's a personal
accountability toy, not a product.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Self-hosted](https://img.shields.io/badge/self--hosted-Raspberry%20Pi-red)

---

## ⚠️ Please read first

- **Not medical, nutritional, or fitness advice.** MealSentry only echoes *your own* numbers and
  the timestamps of reminders you ignored. For real health decisions, talk to a professional.
- **Unofficial, provided as-is, no warranty.** Use at your own risk.
- **Single-user by design.** Exactly one Telegram user id is whitelisted — it is your private bot,
  not a shared service.
- **Messages are in Greek** (the "Chad Coach" persona is Greek). Everything is plain YAML, so you
  can translate or replace it — see [Make it yours](#make-it-yours).
- **It is deliberately blunt but never cruel.** The tone is built from your logged numbers; it
  never insults, body-shames, or swears.

---

## What it does

MealSentry logs your meals, weight, steps, gym sessions, sleep, food inventory and spending, then
nags you over Telegram when you drift off-target. Calorie and protein goals recompute after every
weigh-in (Mifflin–St Jeor + your last week of activity). It's gamified so that staying on-protocol
is oddly satisfying.

- **Recomputing targets** — calorie target and protein floor recalculated on each `/weight`.
- **Escalating nags** — a per-task state machine (`PENDING → NAGGED_1 → NAGGED_2 → NAGGED_3 →
  FAILED/DONE`) with 30-minute steps; every ping is stored and quoted back when a task fails.
- **A full cron day** — prep checks, meals, protein checkpoints, steps, sleep windows, and
  Tuesday/Saturday shopping runs (Europe/Athens with DST handling).
- **Gym pressure model** — 3 sessions/week, rising ping frequency while a session is unlogged,
  Friday last-call, Sunday verdict.
- **RPG gamification** — XP and levels, a respect meter that picks the message tone, per-category
  streaks, a **coins economy + rewards shop** (cash in for cheat meals or leisure), a **🎰 wheel of
  fortune**, and body-based **RPG classes** with playful epithets.
- **Data-driven notifications** — enable / mute / retime every reminder from a settings screen or
  Telegram, and complete a passed task retroactively.
- **Shopping brain** — inventory burn-rate prediction and an auto shopping list that knows the
  store is closed on Sundays, plus monthly spend tracking against a budget.
- **Nutrition-myth trivia** — a daily fact with a 1–5 evidence rating, no repeats within 60 days.
- **Tap-driven menu** — most actions are one tap; you don't have to memorise commands.
- **Read-only dashboard** — an optional FastAPI backend on port 8787 over the same database.

---

## Quick start — self-host it

### 1. Create your Telegram bot

1. Message **[@BotFather](https://t.me/BotFather)** → `/newbot` → copy the **bot token**.
2. Get your **numeric Telegram user id** (message **[@userinfobot](https://t.me/userinfobot)** — it
   replies with your id). This is the only id the bot will answer.

### 2. Install

```bash
git clone https://github.com/wannabexaker/Meal_Sentry
cd Meal_Sentry
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
cp config.yaml.example config.yaml     # then edit: your name, biometrics, targets, timezone
```

### 3. Provide secrets via environment (never committed)

```bash
export MEALSENTRY_TOKEN="<token from @BotFather>"
export MEALSENTRY_USER_ID="<your Telegram numeric id>"
```

### 4. Run

```bash
python -m mealsentry.bot      # the bot + its scheduler
python -m mealsentry.api      # optional read-only dashboard on http://127.0.0.1:8787
```

Send `/start` to your bot on Telegram and you're in.

### On a Raspberry Pi (systemd, recommended)

The installer creates the venv, prompts for your token + id, and installs two hardened systemd
units (it won't clash with other bots — distinct package, env vars, units and port):

```bash
bash deploy/install.sh
sudo systemctl start mealsentry mealsentry-api
journalctl -u mealsentry -f          # follow the logs
curl http://127.0.0.1:8787/health    # check the backend
```

---

## Make it yours

- **Profile & targets** — edit `config.yaml`: name, biometrics, `steps_target`, `gym_target_sessions`,
  `protein_factor`, `deficit_kcal`, the eating window, `timezone`, and your local store.
- **Intensity** — `intensity: 1..3` (higher = harsher). The respect meter shifts tone tiers too.
- **Swap the coach** — a coach is *data, not code*. Add `coaches/<id>.yaml` + a template file and set
  `active_coach`. Chad Coach is just the first persona.
- **Translate it** — the persona lives in `tone/templates_gr.yaml` as plain YAML (≥5 variants per
  situation, with `{name}` / `{coach}` placeholders). Rewrite it in any language without touching the
  engine.

---

## Primary commands

Most actions are available as menu buttons, but the classics work too:

| Command | Action |
|---|---|
| `/status` | Day snapshot: targets, intake, gym, level, respect, coins |
| `/ate <meal>` | Log a meal or weighed food |
| `/weight <kg>` | Log weight and recompute targets |
| `/steps <n>` · `/gym done [min]` | Log steps or a gym session |
| `/sleep <bed> <wake>` | Log sleep, e.g. `/sleep 23:40 07:10` |
| `/wheel` · `/class` · `/dashboard` | Wheel of fortune · pick your RPG class · dashboard/settings link |
| `/report` · `/fact` | Weekly report · nutrition trivia |

---

## How it's built

Requests enter through an adapter (`bot.py` for Telegram, `api.py` for HTTP), which calls
`service.py`, which composes the pure engine modules and writes to SQLite. The scheduler runs cron
jobs plus a 5-minute escalation tick and renders text through the active coach — so it holds no
Telegram import itself. The **engine layer imports neither Telegram nor HTTP**, so the bot, the API
and the tests exercise the same code.

```text
Meal_Sentry/
├── mealsentry/
│   ├── bot.py            — Telegram adapter (commands, menu, whitelist)
│   ├── api.py            — FastAPI read-only backend + dashboard
│   ├── scheduler.py      — cron jobs + escalation tick
│   ├── service.py        — engine composition + XP/coins side effects
│   ├── tone.py           — coach persona renderer
│   └── engine/           — math, meals, nag, gym, game, foods, rewards,
│                            wheel, classes, notifs, inventory, sleep, facts
├── coaches/chad_coach.yaml   — coach manifest
├── tone/templates_gr.yaml    — Greek message templates
├── data/                     — meals.json, foods.json, facts_gr.json, rewards.json, notifs.json
├── db/schema.sql             — SQLite schema
├── deploy/                   — systemd units + install.sh
├── tests/                    — pytest suite
└── config.yaml.example
```

**Tech:** Python 3.11+ · `python-telegram-bot` (async) · APScheduler · `aiosqlite` · FastAPI +
Uvicorn · matplotlib · PyYAML.

---

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest -q
```

## Security & privacy

Secrets are read from the environment only — never a file in the repo. `config.yaml`, the SQLite
database, and `*.env` are git-ignored. The FastAPI backend is read-only and binds to loopback by
default; if you expose it (e.g. over Tailscale), put an authenticating proxy in front. See
[SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE). A fan project — share it, fork it, coach yourself.
