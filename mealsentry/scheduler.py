"""APScheduler wiring (spec §4/§5): the full weekday/weekend cron schedule, the nag
escalation tick, shopping enforcer, gym pressure, sleep windows, and the daily facts push.

Telegram-agnostic: messages go out through a ``notifier`` coroutine
``send(text, *, buttons=None, photo=None)`` where ``buttons`` is a list of button rows,
each button a ``(label, callback_data)`` tuple. The bot builds the actual markup.

State recovery: on start we run one escalation tick so a reboot mid-escalation re-arms any
overdue pings from the DB rather than losing them.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .config import Config
from .db import Database
from .engine import facts, game, gym, nag, notifs
from .service import Service
from .util import date_str

log = logging.getLogger("mealsentry.scheduler")

Notifier = Callable[..., Awaitable[None]]

# Confirm-required tasks (drive the nag state machine + escalation tick).
TASK_SPECS: dict[str, dict] = {
    "prep_morning": {"situation": "prep_morning", "category": None,
                     "confirm": "✅ Έτοιμο", "fail": "task_failed", "label": "Prep πρωί"},
    "meal1": {"situation": "meal_reminder", "category": "meals",
              "confirm": "✅ Έφαγα", "fail": "meal_failed", "label": "Γεύμα 1"},
    "meal2": {"situation": "meal_reminder", "category": "meals",
              "confirm": "✅ Έφαγα", "fail": "meal_failed", "label": "Γεύμα 2"},
    "steps": {"situation": "steps_check", "category": "steps",
              "confirm": "✅ Καταχωρώ", "fail": "task_failed", "label": "Βήματα"},
    "prep_evening": {"situation": "prep_evening", "category": None,
                     "confirm": "✅ Έτοιμο", "fail": "task_failed", "label": "Prep βράδυ"},
    "shopping": {"situation": "shopping_enforcer", "category": None,
                 "confirm": "✅ Πήγα", "fail": "shopping_missed", "label": "Ψώνια"},
}

ESCALATION_TICK_MIN = 5


class NagScheduler:
    def __init__(self, config: Config, db: Database, coach, notifier: Notifier):
        self.config = config
        self.db = db
        self.coach = coach
        self.notify = notifier
        self.service = Service(db, config)
        self.scheduler = AsyncIOScheduler(timezone=config.tz)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        await self._register_jobs()
        self.scheduler.start()
        # boot recovery + today's facts push
        self.scheduler.add_job(self._on_boot, DateTrigger(run_date=datetime.now(self.config.tz)
                                                           + timedelta(seconds=2)))
        log.info("Scheduler started with %d jobs", len(self.scheduler.get_jobs()))

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    async def _on_boot(self) -> None:
        await self._escalation_tick()   # re-arm overdue escalations after a restart
        self._schedule_daily_fact()

    # ------------------------------------------------------------------ registration
    def _cron(self, func, **kw) -> None:
        self.scheduler.add_job(func, CronTrigger(timezone=self.config.tz, **kw),
                               misfire_grace_time=3600, coalesce=True)

    async def _register_jobs(self) -> None:
        """Register cron jobs using DB-configured times where applicable.

        Rows with ``enabled = 0`` in ``notif_config`` skip registration entirely — disable
        + retime take effect on the next restart. Runtime mute is handled per-callback via
        :func:`notifs.is_active`.
        """
        wd = "mon-fri"

        async def slot(key: str, dh: int, dm: int) -> tuple[int, int] | None:
            if not await notifs.is_enabled_default(self.db, key):
                return None
            return await notifs.get_time(self.db, key, dh, dm)

        # --- weekday nutrition/steps/prep schedule ---
        if (t := await slot("prep_morning", 8, 30)):
            self._cron(lambda: self.fire_task("prep_morning"), day_of_week=wd, hour=t[0], minute=t[1])
        if (t := await slot("meal1", 14, 15)):
            self._cron(lambda: self.fire_task("meal1"), day_of_week=wd, hour=t[0], minute=t[1])
        if (t := await slot("protein_pace", 16, 45)):
            self._cron(self.protein_pace, day_of_week=wd, hour=t[0], minute=t[1])
        if (t := await slot("protein_pace_aggressive", 19, 0)):
            self._cron(self.protein_pace_aggressive, day_of_week=wd, hour=t[0], minute=t[1])
        # Meal 2 at 19:30 on Mon/Wed/Thu/Fri (Tuesday is the shopping run instead)
        if (t := await slot("meal2", 19, 30)):
            self._cron(lambda: self.fire_task("meal2"), day_of_week="mon,wed,thu,fri",
                       hour=t[0], minute=t[1])
        if (t := await slot("steps", 20, 0)):
            self._cron(lambda: self.fire_task("steps"), day_of_week=wd, hour=t[0], minute=t[1])
        if (t := await slot("protein_verdict", 21, 0)):
            self._cron(self.protein_verdict, day_of_week=wd, hour=t[0], minute=t[1])
        if (t := await slot("prep_evening", 21, 30)):
            self._cron(lambda: self.fire_task("prep_evening"), day_of_week=wd, hour=t[0], minute=t[1])

        # --- sleep windows (daily) ---
        if (t := await slot("sleep_winddown", 23, 0)):
            self._cron(lambda: self._simple("sleep_winddown"), hour=t[0], minute=t[1])
        if (t := await slot("screens_off", 23, 30)):
            self._cron(lambda: self._simple("screens_off"), hour=t[0], minute=t[1])

        # --- Tuesday shopping enforcer + countdowns ---
        if (t := await slot("shopping", 18, 0)):
            self._cron(lambda: self.fire_task("shopping"), day_of_week="tue", hour=t[0], minute=t[1])
            # Follow-ups are anchored to the shopping key: only register when enabled.
            self._cron(lambda: self.fire_task("shopping"), day_of_week="tue", hour=19, minute=30)
            self._cron(lambda: self.shopping_countdown(45), day_of_week="tue", hour=20, minute=15)
            self._cron(self.shopping_close, day_of_week="tue", hour=21, minute=0)

        # --- gym pressure ---
        if (t := await slot("gym_pressure", 18, 30)):
            self._cron(self.gym_pressure_ping, day_of_week="mon-thu", hour=t[0], minute=t[1])
        for h in (12, 15, 17):
            self._cron(self.gym_lastcall, day_of_week="fri", hour=h, minute=0)

        # --- weekend ---
        self._cron(self.gym_weekend_reminder, day_of_week="sat,sun", hour=10, minute=0)
        self._cron(self.saturday_shopping, day_of_week="sat", hour=10, minute=0)
        self._cron(self.sunday_awareness, day_of_week="sat", hour=17, minute=0)
        if (t := await slot("weekly_verdict", 22, 0)):
            self._cron(self.weekly_verdict, day_of_week="sun", hour=t[0], minute=t[1])

        # --- daily facts push: reschedule each morning to a random 15:00–18:00 slot ---
        self._cron(self._schedule_daily_fact, hour=0, minute=1)

        # --- escalation tick ---
        self.scheduler.add_job(self._escalation_tick,
                               CronTrigger(timezone=self.config.tz, minute=f"*/{ESCALATION_TICK_MIN}"),
                               coalesce=True, max_instances=1)

    # ------------------------------------------------------------------ helpers
    def now(self) -> datetime:
        return self.config.now()

    def _buttons(self, task_key: str, spec: dict):
        # Meal reminders offer one-tap logging of common meals + "other" (full list).
        if spec.get("category") == "meals":
            return [
                [("🍗 Κοτόπουλο", "eat:chicken"), ("🥩 Κιμάς", "eat:beef")],
                [("🍽️ Άλλο", "menu:food"), ("💤 +30'", f"snooze:{task_key}")],
            ]
        return [[(spec["confirm"], f"done:{task_key}"),
                 ("⏭ Skip", f"skip:{task_key}"),
                 ("💤 +30'", f"snooze:{task_key}")]]

    async def _respect(self) -> int:
        st = await game.get_state(self.db)
        return st["respect"]

    async def _simple(self, situation: str, **data) -> None:
        # Runtime mute gate: notif_config.key aligns with the situation for canonical
        # slots (sleep_winddown / screens_off / protein_verdict / etc.). Unknown keys
        # (ad-hoc pings without a config row) evaluate to True → allowed.
        if not await notifs.is_active(self.db, situation):
            return
        text = self.coach.render(situation, respect=await self._respect(), **data)
        await self.notify(text)

    # ------------------------------------------------------------------ task firing
    async def fire_task(self, task_key: str) -> None:
        if not await notifs.is_active(self.db, task_key):
            return
        spec = TASK_SPECS[task_key]
        when = self.now()
        res = await nag.advance(self.db, task_key, when)
        await self._emit(task_key, spec, res, when)

    async def _escalation_tick(self) -> None:
        when = self.now()
        for task_key in await nag.due_for_escalation(self.db, when):
            spec = TASK_SPECS.get(task_key)
            if spec is None:
                continue
            res = await nag.advance(self.db, task_key, when)
            await self._emit(task_key, spec, res, when)

    async def _emit(self, task_key: str, spec: dict, res: nag.NagResult, when: datetime) -> None:
        if not res.notify:
            return
        respect = await self._respect()
        extra = await self._task_data(task_key, when)
        data = {"meal": spec["label"], "task": spec["label"],
                "warn_times": res.receipts_text, **extra}
        if res.kind == "failed":
            text = self.coach.render(spec["fail"], respect=respect, **data)
            await self.notify(text)
            await self._apply_failure(spec, when)
        else:
            text = self.coach.render(spec["situation"], respect=respect, **data)
            await nag.record_warning(self.db, task_key, when, res.level, text)
            await self.notify(text, buttons=self._buttons(task_key, spec))

    async def _task_data(self, task_key: str, when: datetime) -> dict:
        if task_key == "steps":
            st = await self.service.status(when)
            gap = max(0, st["today"]["steps_target"] - st["today"]["steps"])
            return {"have": st["today"]["steps"], "target": st["today"]["steps_target"],
                    "gap": gap, "walk_min": max(5, round(gap / 110))}
        if task_key == "shopping":
            sl = await self.service.shopping_list(when)
            items = ", ".join(f"{i['item']} {int(i['need_g'])}g" for i in sl["items"][:6]) or "βασικά"
            return {"items": items, "store": self.config.shop_store}
        return {}

    async def _apply_failure(self, spec: dict, when: datetime) -> None:
        await game.penalize(self.db, "failed_task", when)
        if spec.get("category"):
            await game.break_streak(self.db, spec["category"], when)

    # ------------------------------------------------------------------ notifications
    async def protein_pace(self) -> None:
        when = self.now()
        st = await self.service.status(when)
        t = st["today"]
        await self._simple("protein_pace", have=round(t["protein_g"]),
                           floor=t["protein_floor_g"], gap=t["protein_gap_g"])

    async def protein_pace_aggressive(self) -> None:
        if not await notifs.is_active(self.db, "protein_pace_aggressive"):
            return
        when = self.now()
        st = await self.service.status(when)
        t = st["today"]
        if t["protein_gap_g"] > 0:  # only nag if projected below floor
            # Bypass _simple's own gate — we've already checked the aggressive-specific key.
            text = self.coach.render("protein_pace", respect=await self._respect(),
                                     tier="LOW", have=round(t["protein_g"]),
                                     floor=t["protein_floor_g"], gap=t["protein_gap_g"])
            await self.notify(text)

    async def protein_verdict(self) -> None:
        when = self.now()
        st = await self.service.status(when)
        gap = st["today"]["protein_gap_g"]
        if gap > 0:
            await self._simple("protein_verdict", gap=gap)

    async def shopping_countdown(self, minutes_left: int) -> None:
        row = await self.db.fetchone(
            "SELECT state FROM tasks WHERE date = ? AND task_key = 'shopping'",
            (date_str(self.now()),))
        if row and row["state"] not in nag.TERMINAL:
            await self._simple("shopping_countdown", min_left=minutes_left,
                               store=self.config.shop_store)

    async def shopping_close(self) -> None:
        when = self.now()
        row = await self.db.fetchone(
            "SELECT state FROM tasks WHERE date = ? AND task_key = 'shopping'", (date_str(when),))
        if row and row["state"] not in nag.TERMINAL:
            await nag.advance(self.db, "shopping", when)  # push toward failure
            # force fail now that the store is closed
            await self.db.execute(
                "UPDATE tasks SET state='FAILED', next_ts=NULL, done_ts=? "
                "WHERE date=? AND task_key='shopping'",
                (when.isoformat(timespec="seconds"), date_str(when)))
            await self._simple("shopping_missed")
            await game.penalize(self.db, "failed_task", when)

    async def gym_pressure_ping(self) -> None:
        when = self.now()
        pressure = await gym.compute_pressure(self.db, when)
        if pressure <= 0:
            return
        sessions = await gym.sessions_this_week(self.db, when)
        days_left = 6 - when.weekday()  # until Sunday
        for _ in range(gym.pings_per_day(pressure)):
            await self._simple("gym_pressure", sessions=sessions,
                               target=gym.WEEKLY_TARGET, days_left=max(1, days_left))
            break  # one message per scheduled fire; frequency handled by pressure schedule

    async def gym_lastcall(self) -> None:
        when = self.now()
        if await gym.weekday_session_logged(self.db, when):
            return
        sessions = await gym.sessions_this_week(self.db, when)
        await self._simple("gym_lastcall", sessions=sessions, target=gym.WEEKLY_TARGET)

    async def gym_weekend_reminder(self) -> None:
        await self._simple("gym_pressure", sessions=await gym.sessions_this_week(self.db, self.now()),
                           target=gym.WEEKLY_TARGET, days_left=max(1, 6 - self.now().weekday()))

    async def saturday_shopping(self) -> None:
        when = self.now()
        sl = await self.service.shopping_list(when)
        items = ", ".join(f"{i['item']} {int(i['need_g'])}g" for i in sl["items"][:8]) or "βασικά"
        await self._simple("shopping_enforcer", items=items, store=self.config.shop_store)

    async def sunday_awareness(self) -> None:
        when = self.now()
        stock = await self.service.shopping_list(when)
        items = ", ".join(f"{i['item']} {int(i['need_g'])}g" for i in stock["items"][:6]) or "OK"
        await self.notify(f"🛒 Αύριο Κυριακή — κλειστά. Inventory gap: {items}. Αρκεί;")

    async def weekly_verdict(self) -> None:
        when = self.now()
        v = await gym.weekly_verdict(self.db, when)
        situation = "gym_verdict_hit" if v.hit_target else "gym_verdict_miss"
        await self._simple(situation, sessions=v.sessions, target=v.target, prev=v.prev_sessions)
        if not v.hit_target:
            await game.penalize(self.db, "failed_week", when)
        # weekly report is emitted by the bot layer (needs chart upload); signal via flag
        await self.db.kv_set("weekly_report_due", date_str(when))
        await self._emit_weekly_report(when)

    async def _emit_weekly_report(self, when: datetime) -> None:
        """Text weekly report. Chart PNG is added by the bot if it wires a photo notifier."""
        data = await self.service.weekly_report(when)
        intro = self.coach.render("weekly_report_intro", respect=await self._respect())
        lines = [
            intro,
            f"• Adherence: {data['adherence_pct']}%  ({data['tasks_done']}✓ / {data['tasks_failed']}✗)",
            f"• Gym: {data['gym_sessions']}/{data['gym_target']}",
            f"• Μ.Ο. πρωτεΐνης: {data['avg_protein']}g",
            f"• Αγνοημένες προειδοποιήσεις: {data['ignored_warnings']}",
            f"• XP: {data['xp']} ({data['xp_delta']:+d}) — Lvl {data['level']} {data['level_name']}",
            f"• Respect: {data['respect']} ({data['respect_delta']:+d})",
        ]
        await self.notify("\n".join(lines))

    # ------------------------------------------------------------------ facts push
    def _schedule_daily_fact(self) -> None:
        now = self.now()
        target = now.replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(
            minutes=random.randint(0, 179))
        if target <= now:
            target = target + timedelta(days=1)
        self.scheduler.add_job(self.push_fact, DateTrigger(run_date=target),
                               id="daily_fact", replace_existing=True)
        log.info("Next fact push scheduled for %s", target.isoformat())

    async def push_fact(self) -> None:
        if not await notifs.is_active(self.db, "facts"):
            return
        when = self.now()
        fact = await facts.pick_fact(self.db, when)
        if fact is None:
            return
        intro = self.coach.render("fact_intro", respect=await self._respect())
        stars = facts.verdict_stars(fact.verdict)
        await self.notify(f"{intro}\n\n*{fact.title}*\n{fact.body}\n\nCoach verdict: {stars} ({fact.verdict}/5)")
