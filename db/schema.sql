-- MealSentry SQLite schema. All access is parameterized (see mealsentry/db.py).
-- Timestamps are ISO-8601 strings in the configured timezone (Europe/Athens).
-- Dates are 'YYYY-MM-DD' local dates.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Single-row biometric/profile state. Seeded from config on first run, updatable at runtime.
CREATE TABLE IF NOT EXISTS user_profile (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    name                 TEXT    NOT NULL,
    sex                  TEXT    NOT NULL DEFAULT 'male',
    age                  INTEGER NOT NULL,
    height_cm            REAL    NOT NULL,
    weight_kg            REAL    NOT NULL,
    start_weight_kg      REAL    NOT NULL,
    steps_target         INTEGER NOT NULL DEFAULT 11000,
    gym_target_sessions  INTEGER NOT NULL DEFAULT 3,
    sleep_target_hours   REAL    NOT NULL DEFAULT 7.0,
    protein_factor       REAL    NOT NULL DEFAULT 1.8,
    deficit_kcal         INTEGER NOT NULL DEFAULT 600,
    updated_at           TEXT    NOT NULL
);

-- Preset + user-created meals. Seeded from data/meals.json.
CREATE TABLE IF NOT EXISTS meals (
    id           TEXT PRIMARY KEY,
    name         TEXT    NOT NULL,
    contents     TEXT    NOT NULL DEFAULT '',
    kcal         REAL    NOT NULL,
    protein_g    REAL    NOT NULL,
    max_per_week INTEGER,            -- NULL = unlimited
    locked       INTEGER NOT NULL DEFAULT 0,  -- reward meals start locked
    enabled      INTEGER NOT NULL DEFAULT 1,
    tags         TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS meal_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    date      TEXT NOT NULL,
    meal_id   TEXT NOT NULL,
    fraction  REAL NOT NULL DEFAULT 1.0,
    kcal      REAL NOT NULL,
    protein_g REAL NOT NULL,
    note      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_meal_log_date ON meal_log(date);

CREATE TABLE IF NOT EXISTS weight_log (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ts   TEXT NOT NULL,
    date TEXT NOT NULL,
    kg   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weight_log_date ON weight_log(date);

CREATE TABLE IF NOT EXISTS steps_log (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    TEXT NOT NULL,
    date  TEXT NOT NULL UNIQUE,
    steps INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS gym_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    date    TEXT NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 60
);
CREATE INDEX IF NOT EXISTS idx_gym_log_date ON gym_log(date);

CREATE TABLE IF NOT EXISTS sleep_log (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    TEXT NOT NULL,
    date  TEXT NOT NULL UNIQUE,   -- the wake date
    bed   TEXT NOT NULL,
    wake  TEXT NOT NULL,
    hours REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    item       TEXT PRIMARY KEY,
    grams      REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spend_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    date     TEXT NOT NULL,
    amount   REAL NOT NULL,
    category TEXT NOT NULL DEFAULT 'chicken'
);
CREATE INDEX IF NOT EXISTS idx_spend_log_date ON spend_log(date);

-- Every ping sent. This is the "receipts" ledger: on failure the bot quotes these back.
CREATE TABLE IF NOT EXISTS warnings (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    date     TEXT NOT NULL,
    task_key TEXT NOT NULL,
    level    INTEGER NOT NULL DEFAULT 1,
    text     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_warnings_task ON warnings(date, task_key);

-- Daily task state machine (nag engine). One row per (date, task_key).
CREATE TABLE IF NOT EXISTS tasks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    date      TEXT NOT NULL,
    task_key  TEXT NOT NULL,
    state     TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|NAGGED_1|NAGGED_2|NAGGED_3|DONE|FAILED
    due_ts    TEXT NOT NULL,
    next_ts   TEXT,                              -- when the next escalation is allowed
    nag_count INTEGER NOT NULL DEFAULT 0,
    done_ts   TEXT,
    meta      TEXT NOT NULL DEFAULT '{}',
    UNIQUE (date, task_key)
);
CREATE INDEX IF NOT EXISTS idx_tasks_open ON tasks(state);

-- Single-row gamification state.
CREATE TABLE IF NOT EXISTS game_state (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    xp           INTEGER NOT NULL DEFAULT 0,
    level        INTEGER NOT NULL DEFAULT 1,
    respect      INTEGER NOT NULL DEFAULT 50,   -- 0..100, drives tone tier
    cheat_tokens INTEGER NOT NULL DEFAULT 0,
    boss_week    INTEGER NOT NULL DEFAULT 0,
    updated_at   TEXT NOT NULL
);

-- Per-category streaks (meals, protein, gym, steps, sleep, weigh_in).
CREATE TABLE IF NOT EXISTS streaks (
    category  TEXT PRIMARY KEY,
    count     INTEGER NOT NULL DEFAULT 0,
    best      INTEGER NOT NULL DEFAULT 0,
    last_date TEXT
);

-- Fun facts (§13). Seeded from data/facts_gr.json; user additions via /newfact.
CREATE TABLE IF NOT EXISTS facts (
    id      TEXT PRIMARY KEY,
    title   TEXT NOT NULL,
    body    TEXT NOT NULL,
    verdict INTEGER NOT NULL CHECK (verdict BETWEEN 1 AND 5),
    tags    TEXT NOT NULL DEFAULT '',
    source  TEXT NOT NULL DEFAULT '',
    custom  INTEGER NOT NULL DEFAULT 0   -- 1 = added via /newfact
);

CREATE TABLE IF NOT EXISTS facts_seen (
    fact_id    TEXT NOT NULL,
    shown_date TEXT NOT NULL,
    PRIMARY KEY (fact_id, shown_date)
);

-- Generic key/value store for scheduler bookkeeping and misc persistence.
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
