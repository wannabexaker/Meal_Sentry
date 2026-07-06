# MealSentry — Native Android Port · Copilot Build Spec

> **Mission.** Build a brand-new, **fully-offline, on-device** native Android app that is a **1:1 behavioural port** of MealSentry + the Chad Coach persona — currently a Python **Telegram bot + FastAPI** backend that runs on a Raspberry Pi. The Android app must run **entirely on the phone**: no server, no Pi, no network, no Telegram, no login. Same domain logic, same features, same rules — professional, tested, and extensible.
>
> **This is a greenfield app in its own repo/module.** It reuses none of the Python code at runtime; it re-implements the same behaviour in Kotlin.

---

## 0. How to use this document

You (Copilot) are given **two inputs**:

1. **This spec** — the target architecture, tech stack, feature list, and phased build order.
2. **The Python reference implementation** — the `MealSentry` repo (`wannabexaker/Meal_Sentry`). It is the **source of truth for behaviour and data**. Whenever this spec says *"port X"*, open the corresponding Python file and replicate its logic and exact values:

| Concern | Python source (canonical values) |
|---|---|
| DB schema (tables/columns) | `db/schema.sql` + additive migrations in `mealsentry/db.py::_migrate` |
| Seed data | `data/*.json` (`foods.json`, `meals`, `facts_gr.json`, `rewards.json`, `notifs.json`) |
| Coach persona + copy | `coaches/chad_coach.yaml`, `coaches/templates_gr.yaml` (tone tiers, all Greek strings) |
| Pure domain logic | `mealsentry/engine/*.py` (`nag`, `game`, `foods`, `meals`, `rewards`, `classes`, `wheel`, `notifs`, `facts`, `gym`, `sleep`) |
| Orchestration | `mealsentry/service.py` |
| Schedule / notifications | `mealsentry/scheduler.py` (cron times, escalation, gating) |

**Rule of thumb:** if a number, weight, threshold, portion, or Greek sentence exists in the Python repo, **copy it exactly** — do not invent your own. Where this spec and the Python source disagree, **the Python source wins** for behaviour; this spec wins for Android architecture.

---

## 1. Non-negotiable principles

1. **Offline-first, single-user.** All state lives on-device in Room (SQLite). No auth, no accounts, no cloud, no analytics, no network permission at all. The app works in airplane mode forever.
2. **Clean Architecture.** Strict `domain ← data ← presentation` dependency direction. The `domain` layer is pure Kotlin (no Android, no Room, no Compose imports) — it is the direct analog of the Python `engine/` purity rule.
3. **Single source of truth.** Room is the only persisted state. UI observes it reactively via Flows. No duplicated caches.
4. **Pluggable coach.** The coach persona (tone, templates, tiers) is data behind a `Coach` interface. Adding a second persona must be a config/asset change, not a rewrite — mirror the Python manifest+templates design.
5. **Greek UI, English code.** Every user-facing string is Greek (port them verbatim from the templates + `strings.xml`). All identifiers, comments, commit messages, and docs are English.
6. **Tested.** Every domain use-case has unit tests. Port the Python `tests/` suites conceptually (same cases). Room migrations are tested.
7. **Extensible & professional.** Modular Gradle, DI, no god-objects, no hardcoded magic scattered in UI. New feature = new module/use-case, not edits across ten files.
8. **No co-author / "Generated with" trailers** in any commit or PR. Ever. Conventional Commits, clean history.

---

## 2. Tech stack (pin these)

- **Language:** Kotlin 2.0+ (K2), JDK 17.
- **Build:** Gradle (Kotlin DSL, `build.gradle.kts`) + version catalog (`gradle/libs.versions.toml`). Android Gradle Plugin latest stable.
- **SDK:** `minSdk 26` (Android 8.0 — covers exact-alarm + notification-channel era cleanly), `targetSdk 35`, `compileSdk 35`.
- **UI:** Jetpack **Compose** (Compose BOM) + **Material 3** + Material 3 dynamic color. Navigation-Compose. No XML layouts except the launcher/splash.
- **Persistence:** **Room** (+ Room KTX, Flow queries). **DataStore (Preferences)** for the `kv`-style singleton flags/prefs.
- **Background/notifications:** **WorkManager** (periodic ticks) + **AlarmManager** exact alarms (time-of-day nags) + **NotificationManagerCompat** (channels). `RECEIVE_BOOT_COMPLETED` receiver to re-arm alarms after reboot (ports the scheduler's boot-recovery).
- **DI:** **Hilt**.
- **Async:** Coroutines + Flow (StateFlow in ViewModels). `viewModelScope`, structured concurrency.
- **Serialization:** `kotlinx.serialization` (parse the bundled seed JSON assets).
- **Images/icons:** Material Icons Extended; Coil only if raster assets appear (avatars are emoji/vector).
- **Quality:** **ktlint** + **detekt** (the ruff analog) wired into Gradle `check`.
- **Testing:** JUnit5, kotlinx-coroutines-test, **Turbine** (Flow assertions), Room in-memory + `MigrationTestHelper`, Robolectric for a few Android-touching units, Compose UI test for critical screens.

---

## 3. Module / package structure

Multi-module Gradle (keeps layers enforced by the compiler):

```
:app                      // Application, DI graph, MainActivity, NavHost, notification wiring
:core:designsystem        // Compose theme, colors, typography, spacing, reusable components
:core:common              // Result types, dispatchers, date/time utils, formatting
:domain                   // PURE Kotlin: entities, repository interfaces, use-cases, Coach interface
:data                     // Room (entities/DAOs/db), repo impls, seed loader, DataStore, mappers
:coach                    // Coach persona impl(s): manifest + templates loader, tone/tier logic
:notifications            // WorkManager workers, AlarmScheduler, NotificationPublisher, BootReceiver
:feature:home             // Today / status
:feature:log              // Food logging (granular grams, combos, recent-first, favourites)
:feature:character        // RPG dashboard: class, level/XP, coins, respect, tiers
:feature:rewards          // Rewards shop (redeem coins)
:feature:wheel            // 🎰 Wheel of fortune
:feature:history          // Calendar / history
:feature:settings         // Profile, foods CRUD, rewards CRUD, notification management
:feature:facts            // Fun facts feed
```

Dependency rule: `feature:* → domain` (+ `core:*`); only `:data`/`:coach`/`:notifications` know about Android/Room. `domain` depends on nothing Android. If a smaller team prefers **one module**, keep the **same package layers** (`domain/`, `data/`, `coach/`, `notifications/`, `feature/…`) and enforce purity with detekt import rules — but multi-module is preferred for this project.

---

## 4. Domain model (Room entities)

Port **every table** from `db/schema.sql` (including the additive columns from `_migrate`) into Room `@Entity` classes, 1:1 on names and types (SQLite `TEXT/INTEGER/REAL` → `String/Long/Int/Double/Boolean`). Do not rename columns — it keeps the port auditable against the Python source.

Entities to create (confirm exact columns against `schema.sql`):

- `user_profile` (incl. `desired_class`, targets, protein_factor, deficit_kcal).
- `meals` (combos: id, name, contents, kcal, protein_g, max_per_week, locked, enabled, tags).
- `foods` (per-100g macros, `category`, `default_g`, `aliases`, `custom`).
- `meal_log` (with `food_id`, `grams`).
- `game_state` (xp, level, respect, coins, cheat_tokens, boss_week).
- `rewards` (id, name, emoji, cost, kind[`leisure`|`cheat`], meal_id, enabled, custom) + `reward_log`.
- `facts` + `facts_seen`.
- `tasks` (nag state machine rows) + `warnings` (receipts) + `streaks`.
- `wheel_log`.
- `notif_config` (key, label, time, enabled, muted).
- Metric logs: `weight_log`, `steps_log`, `gym_log`, `sleep_log`, `inventory`, `spend_log` (whatever exists in `schema.sql`).
- `kv` → migrate to **DataStore** (it's a key/value singleton store; don't make it a Room table unless a query joins it).

**Migrations:** replicate the additive philosophy — never destructive. Provide `Migration(n, n+1)` classes and a `MigrationTestHelper` test. Ship at schema version 1 that already contains all current columns (a fresh install needs no migration), but wire the migration framework so future additive changes are trivial.

---

## 5. Domain logic — port each engine module as use-cases

Each `mealsentry/engine/*.py` becomes a set of **pure use-cases** in `:domain` (constructor-inject repository interfaces; return domain models; zero Android). Preserve the exact rules:

- **Nag state machine** (`engine/nag.py`) → `NagStateMachine` + use-cases `AdvanceTask`, `ConfirmTask` (retroactive complete), `SkipTask`, `SnoozeTask`, `DueForEscalation`, `OpenTasks`. States `PENDING→NAGGED_1/2/3→FAILED/DONE`, "receipts" = warning timestamps quoted back. Keep the terminal-state set and escalation timing identical.
- **Game** (`engine/game.py`) → `GameEngine`: XP rewards table, `COIN_REWARDS` (meal/protein_floor/gym/steps/sleep/weigh_in), boss-week ×2, level curve (`level_for_xp`), respect meter, streaks, `spendCoins` (guarded, returns bool), `grantCoins`, `grantXp`, penalties (`failed_task`, `failed_week`), `breakStreak`. Copy all constants.
- **Foods** (`engine/foods.py`) → `FoodsEngine`: per-100g macro compute by grams, `default_portion` category map, **auto unique id** (`create_food`), `duplicate_food`, `set_default_g`, **recent-first ordering** (`list_recent_first`), `recent_foods`, `last_grams` (default logging quantity = last used). Fuzzy `find_food` (alias/normalize match).
- **Meals/combos** (`engine/meals.py`) → `MealsEngine`: list (enabled, unlocked), `find_meal`, `today_totals`, `max_per_week` enforcement, `MealNotFound`.
- **Rewards** (`engine/rewards.py`) → `RewardsEngine`: list with affordability flag, add/set_cost/delete, `kind` semantics (cheat reward also logs a meal on redeem).
- **Classes** (`engine/classes.py`) → `ClassesEngine`: the six classes (assassin/ranger/monk/brawler/warrior/tank) with height/weight bands, `H_TOL/W_TOL`, `epithet(height,weight,class)` (body-based Greek troll epithets — Ψηλός/Κοντός, Χοντρός/Αδύνατος), `best_fit`, `describe`.
- **Wheel** (`engine/wheel.py`) → `WheelEngine`: `WHEEL_SEGMENTS` weights (meal 40 / exercise 25 / coins 15 / xp 12 / jackpot 8), recent-outcome avoidance via `wheel_log`, meal outcome avoids recently-eaten foods, muscle-group challenges map, coin/xp ranges, jackpot. Spin **costs 1 coin** (service applies the spend/grants).
- **Notifs** (`engine/notifs.py`) → `NotifsEngine`: `notif_config` CRUD, `isActive(key)` (enabled AND not muted; **unknown key → true** so ad-hoc pings are never silenced), `getTime` (with `random`/invalid fallback), `isEnabledDefault`.
- **Facts** (`engine/facts.py`) → `FactsEngine`: no-repeat-within-window pick via `facts_seen`, verdict stars.
- **Gym** (`engine/gym.py`) → `GymEngine`: weekly target, pressure computation, pings-per-day, weekday-session-logged, weekly verdict (hit/miss, prev sessions).
- **Sleep** (`engine/sleep.py`) → `SleepEngine`: 4-digit / `HH:MM HH:MM` parse → duration, target compare.

**Orchestration** (`service.py`) → a thin `MealSentryService`/use-case set that composes engines + side-effects (e.g. `SpinWheel`: coin-guard → spend → resolve → apply grants → log; `LogFood`: eat + meal XP + once/day protein-floor award + today totals). Keep the composition identical.

---

## 6. Coach persona system

Port `coaches/chad_coach.yaml` (manifest: name, avatar, tier thresholds) + `coaches/templates_gr.yaml` (all situation templates, per tone tier **LOW/MID/HIGH**) into **bundled assets** (`assets/coaches/…` as JSON) loaded by the `:coach` module.

```kotlin
interface Coach {
    fun render(situation: String, respect: Int, vararg args: Pair<String, Any?>): String
}
```

- Tone tier is chosen from the **respect meter** exactly as in Python (`respect → LOW/MID/HIGH`).
- Support the "receipts" interpolation (quoting prior warning timestamps).
- The active coach is selected via DI; a `CoachRegistry` allows a future second persona by dropping in another asset bundle + manifest entry. **Do not hardcode Chad's strings in Kotlin** — they live in the templates, same as Python.

---

## 7. Scheduling & notifications (replaces APScheduler + Telegram)

The Python `scheduler.py` cron schedule is **data-driven** from `notif_config`. Reproduce it on Android:

- **Time-of-day nags** (prep_morning 08:30, meal1 14:15, protein_pace 16:45, protein_pace_aggressive 19:00, meal2 19:30 Mon/Wed/Thu/Fri, steps 20:00, protein_verdict 21:00, prep_evening 21:30, sleep_winddown 23:00, screens_off 23:30, gym_pressure 18:30 Mon–Thu, shopping Tue 18:00 + follow-ups, weekly_verdict Sun 22:00, daily fact at a random 15:00–18:00 slot) → schedule with **AlarmManager `setExactAndAllowWhileIdle`** (or `WorkManager` for the non-exact ones). Build the alarm set at boot from `notif_config` (read `enabled` + `time`), exactly like `_register_jobs`.
- **Escalation tick** (every 5 min in Python) → a periodic **WorkManager** worker that runs `DueForEscalation` and re-emits nags. (WorkManager min period is 15 min; if you need tighter escalation, use a repeating alarm — document the choice.)
- **Gating:** before publishing any notification, call `NotifsEngine.isActive(key)` — **skip if disabled or muted** (runtime mute). Replicate the special cases where the fired `situation` key differs from the `notif_config` key (e.g. `protein_pace_aggressive`, `facts`) — see `scheduler.py`.
- **Boot recovery:** `BootReceiver` (`RECEIVE_BOOT_COMPLETED`) re-arms all alarms and runs one escalation tick (ports `_on_boot`).
- **Android specifics:**
  - One **NotificationChannel per category** (meals, steps, gym, sleep, shopping, facts, escalation) with sensible importance.
  - Runtime **`POST_NOTIFICATIONS`** permission (Android 13+) — request on first run with a clear Greek rationale screen.
  - **`SCHEDULE_EXACT_ALARM` / `USE_EXACT_ALARM`** — request/guide the user to allow exact alarms (Android 12+); degrade gracefully to inexact if denied.
  - Notification **actions** replace Telegram inline buttons: e.g. meal nag → actions "✅ Έφαγα" / "🍽️ Άλλο" / "💤 +30'"; these route through a `NotificationActionReceiver` into the same use-cases (`ConfirmTask`, `SnoozeTask`, open the Log screen).
  - **Retroactive complete** = a Settings/notifications action listing today's open tasks → `ConfirmTask`.

---

## 8. UI (Jetpack Compose, Material 3)

Bottom-nav + nested navigation. Screens (each = a `feature` module with ViewModel + StateFlow, observing Room):

1. **Home / Σήμερα** — today's status: kcal/protein vs target, protein floor, tasks (done/open/failed), streaks, quick "τι έφαγες" CTA, coins balance.
2. **Log / Καταγραφή** — the core loop: **granular foods weighed in grams** (default grams = `last_grams`/favourite), **recent-first** food list, categories (πρωτεΐνη/υδατάνθρακες/λιπαρά/…), combos, one-tap re-log, "+ νέο φαγητό". Optional voice: wire Android **`SpeechRecognizer`** / voice-keyboard so the user can dictate (the "Wispr Flow" analog) → free-text → `find_food`; keep parsing pluggable (a `FoodTextParser` interface) since NL parsing is deferred.
3. **Character / RPG dashboard** — class avatar (emoji), level + XP bar, coins, respect meter, the body-based **epithet** line ("🗡️ Ψηλός Χοντρός Assassin · Ideal …"), level-gated tiers. Port the dashboard payload shape.
4. **Rewards / Ανταμοιβές** — shop grid, cost, affordability, redeem (spend coins; cheat rewards also log the meal).
5. **Wheel / 🎰 Τροχός** — animated wheel; spin costs 1 coin; render outcome (meal→one-tap log via the Log flow, exercise challenge, coins, xp, jackpot); "Ξανά".
6. **History / Ημερολόγιο** — calendar + per-day drill-down (meals, tasks, weight, steps, gym, sleep), trends (weight series chart).
7. **Settings / Ρυθμίσεις** — profile (incl. class `select`), **foods CRUD** (add/edit/duplicate, auto-id, default_g, delete), **rewards CRUD**, **notifications management** (per-row enable/mute + time picker; retroactive complete). This is the in-app replacement for the token-gated web control page — **no token needed** (on-device).
8. **Facts / Trivia** — the fun-facts feed with coach verdict stars.

Design: Material 3, dynamic color, **dark mode**, large-touch targets, accessibility (content descriptions, TalkBack), responsive to font scaling. Centralise tokens in `:core:designsystem`. Emoji-forward like the Telegram UX.

---

## 9. Seeding & assets

Bundle the Python `data/*.json` and `coaches/*.yaml` (converted to JSON) under `assets/`. On **first launch** (guard with a DataStore flag), a `SeedLoader` in `:data` populates Room with `INSERT OR IGNORE` semantics (idempotent, never clobbers user edits) — the exact analog of `db.py::seed_*`. Include: foods, meals, facts, rewards, notif_config, coach templates. Compute `default_g` via the `default_portion` map when a seed omits it.

**Backup / import (extensibility, Phase 8):** JSON export/import of the whole DB via the Storage Access Framework. This also gives a migration path to import the user's existing Pi data later (export from the Python app → import here). No network involved.

---

## 10. Testing

Port the Python `tests/` conceptually — same scenarios, Kotlin idioms:

- **Domain unit tests** (JUnit5 + coroutines-test + in-memory Room repos): nag transitions & receipts, game XP/coins/levels/respect/streaks/penalties, foods macro math + recency + auto-id + last_grams, meals totals + weekly caps, rewards affordability + redeem side-effects, classes epithet/best-fit, **wheel** (spend exactly 1, refuse at 0, valid outcome types, log written, meal avoids recent food — mirror `tests/test_wheel.py`), **notifs** (seed keys, enable/mute matrix, `isActive`, unknown-key→true, time validation/fallback, retroactive complete — mirror `tests/test_notifs.py`), facts no-repeat, gym pressure/verdict, sleep parse.
- **Room migration test** (`MigrationTestHelper`).
- **ViewModel tests** with Turbine on the exposed StateFlow.
- **A few Compose UI tests** for the Log and Wheel critical paths.
- Target meaningful coverage on `:domain` (it holds all the rules).

---

## 11. Build, quality, CI, release

- **Gradle KTS + version catalog.** `./gradlew check` runs ktlint + detekt + all unit tests.
- **Variants:** `debug` (applicationIdSuffix `.debug`, debuggable) + `release` (R8/minify + shrinkResources). Local debug signing config; release signing via a keystore + Gradle properties (never commit the keystore or passwords — `.gitignore` them; document env/`local.properties` usage).
- **CI:** GitHub Actions — `assembleDebug` + `testDebugUnitTest` + `lint`/`detekt` on push/PR; upload the debug APK artifact. (Sideload-friendly since it's single-user; keep the structure Play-ready.)
- **Conventional Commits**, clean history, **no co-author trailers**.
- **`.gitignore`:** `*.keystore`, `local.properties`, `/build`, `.gradle/`, `*.jks`, signing props. No secrets in the repo (there are none at runtime, but keep signing material out).

---

## 12. Extensibility playbook (bake these in from day 1)

- **New coach persona:** drop `assets/coaches/<name>.json` (manifest + templates) + register in `CoachRegistry`. No Kotlin logic change.
- **New feature:** new `:feature:*` module + use-case(s) in `:domain`; wire a nav destination + bottom-nav entry. No edits to unrelated features.
- **New logged metric:** add Room entity + DAO + repo method + use-case + a Home/History card.
- **New reward kind / wheel segment / class:** data-only where possible (seed JSON + a `when` branch), mirroring how the Python engines are extended.
- Keep `domain` free of framework types so the rules stay portable (a future iOS/KMP port could reuse `:domain` via Kotlin Multiplatform — structure with that option open, but don't build KMP now).

---

## 13. Build order (phased — build, test, run, commit each phase)

Work in small, verifiable increments. After each phase: `./gradlew check`, run on an emulator, commit.

- **Phase 0 — Scaffold.** Multi-module Gradle + version catalog, Hilt, Compose, Material 3 theme in `:core:designsystem`, empty NavHost + bottom nav, CI skeleton.
- **Phase 1 — Data + domain foundation.** Room (all entities/DAOs/db + migration framework), DataStore, `SeedLoader` + bundled assets, repository interfaces (`:domain`) + impls (`:data`). Port `game`, `foods`, `meals` engines + their tests.
- **Phase 2 — Nag + scheduling core.** `nag` state machine + tasks/warnings, `notifs` engine + `notif_config`, AlarmScheduler + WorkManager escalation tick + NotificationPublisher + BootReceiver + channels + permissions. Port `tests/test_notifs.py`.
- **Phase 3 — Core loop UI.** Home (status) + Log (granular grams, recent-first, combos, favourites, new food). This is the daily-driver; make it fast and pleasant.
- **Phase 4 — Gamification.** Character/RPG dashboard (classes + epithets, level/XP/coins/respect/tiers), Rewards shop, **Wheel** (+ animation). Port `classes`, `rewards`, `wheel` + `tests/test_wheel.py`.
- **Phase 5 — Settings & notification management.** Profile (incl class), foods CRUD (auto-id/edit/duplicate/default_g/delete), rewards CRUD, notifications management (enable/mute/time + retroactive complete).
- **Phase 6 — History & facts.** Calendar/history + weight/steps/gym/sleep drill-downs + trend chart; facts feed with verdicts; `gym`/`sleep`/`facts` engines.
- **Phase 7 — Polish.** Dark mode, dynamic color, accessibility pass, empty/error states, animations, app icon + splash, string audit (all Greek), font-scaling.
- **Phase 8 — Backup/import + release.** JSON export/import (SAF) incl. Pi-data import path; release build (R8), keystore signing, GitHub Actions APK. Tag `v1.0.0`.

---

## 14. Definition of done

- Runs fully offline on a clean device; all features from §5/§8 work with no network.
- Behaviour matches the Python reference on every ported rule (spot-check against `engine/` + `tests/`).
- `./gradlew check` green (ktlint + detekt + unit tests); migration test passes.
- Notifications fire on schedule, respect enable/mute, survive reboot, and their actions work.
- All user-facing text is Greek; all code/commits English; **no co-author trailers**.
- Adding a second coach persona or a new feature module requires no changes to unrelated code.
