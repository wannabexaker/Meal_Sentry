"""Telegram adapter (python-telegram-bot v21+, async).

Thin layer: parses commands/callbacks, calls the ``Service`` + ``Coach``, and renders
replies. Single-user whitelist by ``MEALSENTRY_USER_ID`` (hard reject). Owns the notifier
that the scheduler uses to push nags, and boots the scheduler in ``post_init``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import wraps

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .charts import render_weight_trend
from .config import Config, load_config
from .db import Database, init_db
from .engine import facts, foods, game, meals, nag
from .paths import ROOT
from .scheduler import NagScheduler
from .service import Service
from .tone import Coach

log = logging.getLogger("mealsentry.bot")

NEWMEAL_NAME, NEWMEAL_KCAL, NEWMEAL_PROTEIN = range(3)
NEWFACT_TITLE, NEWFACT_BODY, NEWFACT_VERDICT = range(3, 6)


@dataclass
class AppContext:
    config: Config
    db: Database
    service: Service
    coach: Coach = None  # type: ignore[assignment]
    scheduler: NagScheduler | None = None
    application: Application | None = field(default=None, repr=False)

    # ---- lifecycle ----
    async def on_startup(self, application: Application) -> None:
        self.application = application
        await self.db.connect()
        await init_db(self.db, self.config)
        self.coach = Coach.load(self.config.active_coach, intensity=self.config.intensity)
        self.scheduler = NagScheduler(self.config, self.db, self.coach, self.notify)
        self.scheduler.start()
        log.info("MealSentry started. Coach=%s user=%s", self.coach.display_name,
                 self.config.user_id)

    async def on_shutdown(self, application: Application) -> None:
        if self.scheduler:
            self.scheduler.shutdown()
        await self.db.close()

    # ---- notifier used by the scheduler ----
    async def notify(self, text: str, *, buttons=None, photo=None) -> None:
        markup = _markup(buttons)
        if photo is not None:
            await self.application.bot.send_photo(  # type: ignore[union-attr]
                self.config.user_id, photo=photo, caption=text, reply_markup=markup)
        else:
            await self.application.bot.send_message(  # type: ignore[union-attr]
                self.config.user_id, text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)

    async def respect(self) -> int:
        return (await game.get_state(self.db))["respect"]


def _markup(buttons) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    rows = [[InlineKeyboardButton(label, callback_data=data) for label, data in row]
            for row in buttons]
    return InlineKeyboardMarkup(rows)


def _ctx(context: ContextTypes.DEFAULT_TYPE) -> AppContext:
    return context.bot_data["ctx"]


def guard(handler):
    """Reject anyone who is not the whitelisted user."""
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        ctx = _ctx(context)
        user = update.effective_user
        if user is None or user.id != ctx.config.user_id:
            if update.message:
                await update.message.reply_text(ctx.coach.render("denied"))
            return ConversationHandler.END  # ignored by plain handlers; ends conversations
        return await handler(update, context)
    return wrapper


# --------------------------------------------------------------------------- commands
@guard
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    p = await ctx.service.profile()
    await update.message.reply_text(
        ctx.coach.render("greeting", name=p.get("name", ""))
        + "\n\n👇 Χρησιμοποίησε το μενού — δεν χρειάζεται να θυμάσαι εντολές.",
        reply_markup=MAIN_MENU)


@guard
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    s = await ctx.service.status()
    t, g, game_s = s["today"], s["gym"], s["game"]
    to_next = f" (+{game_s['xp_to_next']} για next)" if game_s["xp_to_next"] else ""
    text = (
        f"📋 *MealSentry — {s['name']}*\n"
        f"Βάρος: {s['weight_kg']}kg (start {s['start_weight_kg']})\n"
        f"Στόχος: {t['kcal_target']} kcal | Πρωτεΐνη floor {t['protein_floor_g']}g\n"
        f"Σήμερα: {t['kcal']} kcal, {t['protein_g']}g πρωτεΐνη "
        f"(λείπουν {t['protein_gap_g']}g)\n"
        f"Βήματα: {t['steps']}/{t['steps_target']}\n"
        f"Gym: {g['sessions']}/{g['target']} αυτή τη βδομάδα\n"
        f"🎮 Lvl {game_s['level']} {game_s['level_name']} | XP {game_s['xp']}{to_next}\n"
        f"Respect: {game_s['respect']}/100 ({game_s['respect_tier']}) | "
        f"🎟 tokens: {game_s['cheat_tokens']}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


@guard
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🛠 *Εντολές*\n"
        "/status — εικόνα ημέρας\n"
        "/ate <meal_id> [μερίδα] — κατέγραψε γεύμα (π.χ. /ate chicken 0.5)\n"
        "/skip <meal_id> — παράλειψη\n"
        "/meals — λίστα γευμάτων (κουμπιά)\n"
        "/newmeal — νέο γεύμα (βήματα)\n"
        "/weight <kg> — ζύγισμα (ξαναϋπολογίζει στόχους)\n"
        "/steps <n> — βήματα\n"
        "/gym done [λεπτά] — προπόνηση\n"
        "/sleep <ύπνος> <ξύπνημα> — π.χ. /sleep 23:40 07:10\n"
        "/stock <είδος> <g> — απόθεμα\n"
        "/spent <€> <κατηγορία> — έξοδο\n"
        "/list — λίστα ψώνια\n"
        "/w — weed-night flag (munchies)\n"
        "/report — εβδομαδιαίο | /charts — γράφημα βάρους\n"
        "/fact — trivia | /newfact — πρόσθεσε trivia",
        parse_mode=ParseMode.MARKDOWN,
    )


@guard
async def cmd_ate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    if not context.args:
        await update.message.reply_text("Χρήση: /ate <meal_id> [μερίδα]. Δες /meals.")
        return
    meal_id = context.args[0]
    fraction = 1.0
    if len(context.args) > 1:
        try:
            fraction = float(context.args[1].replace(",", "."))
        except ValueError:
            fraction = 1.0
    try:
        res = await ctx.service.ate(meal_id, ctx.service.now(), fraction)
    except meals.MealLocked as e:
        await update.message.reply_text(str(e))
        return
    except meals.MealCapReached as e:
        await update.message.reply_text(f"{e}\nΕναλλακτικά: {e.alternative}")
        return
    except meals.MealNotFound as e:
        await update.message.reply_text(str(e))
        return
    # close the nearest open meal task, if any
    await _confirm_open_meal_task(ctx)
    logged, today = res["logged"], res["today"]
    xp = res["award"]["xp_delta"]
    extra = ""
    if res["floor_award"]:
        extra = f"\n💪 Protein floor! +{res['floor_award']['xp_delta']} XP"
    await update.message.reply_text(
        f"✅ {logged['name']} ×{logged['fraction']}: {logged['kcal']} kcal, "
        f"{logged['protein_g']}g πρωτεΐνη (+{xp} XP)\n"
        f"Σύνολο σήμερα: {today['kcal']} kcal, {today['protein_g']}/"
        f"{today['protein_floor_g']}g πρωτεΐνη{extra}")


async def _confirm_open_meal_task(ctx: AppContext) -> None:
    when = ctx.service.now()
    for row in await nag.open_tasks(ctx.db, when):
        if row["task_key"] in ("meal1", "meal2"):
            await nag.confirm(ctx.db, row["task_key"], when)
            break


@guard
async def cmd_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    meal_id = context.args[0] if context.args else "?"
    await update.message.reply_text(f"⏭ Skipped {meal_id}. Χωρίς XP, χωρίς penalty.")


@guard
async def cmd_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    try:
        kg = float(context.args[0].replace(",", "."))
    except (IndexError, ValueError):
        await update.message.reply_text("Χρήση: /weight <kg>")
        return
    res = await ctx.service.log_weight(kg, ctx.service.now())
    t = res["targets"]
    await update.message.reply_text(
        ctx.coach.render("weight_logged", kg=kg, target_kcal=t["calorie_target"],
                         floor=t["protein_floor_g"], respect=await ctx.respect()))
    if res["stalled"] and res["proposal"]:
        pr = res["proposal"]
        await update.message.reply_text(
            ctx.coach.render("stall_proposal", item=pr["item"], kcal=pr["kcal"]),
            reply_markup=_markup([[("✅ Εφάρμοσε", f"applycut:{pr['new_deficit']}"),
                                   ("❌ Όχι", "applycut:no")]]))


@guard
async def cmd_steps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    try:
        n = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("Χρήση: /steps <αριθμός>")
        return
    res = await ctx.service.log_steps(n, ctx.service.now())
    await nag.confirm(ctx.db, "steps", ctx.service.now())
    if res["hit"]:
        xp = res["award"]["xp_delta"] if res["award"] else 0
        await update.message.reply_text(ctx.coach.render("steps_ok", xp=xp))
    else:
        gap = res["target"] - n
        await update.message.reply_text(f"👟 {n}/{res['target']}. Λείπουν {gap}. Κουνήσου.")


@guard
async def cmd_gym(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    minutes = 60
    args = context.args or []
    for a in args:
        if a.isdigit():
            minutes = int(a)
    res = await ctx.service.log_gym(minutes, ctx.service.now())
    a = res["award"]
    up = ""
    if a["level_up"]:
        up = "\n" + ctx.coach.render("level_up", level_name=a["level_name"],
                                     unlock=", ".join(a["unlocks"]) or "—")
    await update.message.reply_text(
        f"🏋️ Session {res['sessions']}/{res['target']} ({minutes}′). +{a['xp_delta']} XP{up}")


@guard
async def cmd_sleep(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    if len(context.args) < 2:
        await update.message.reply_text("Χρήση: /sleep <ύπνος> <ξύπνημα> π.χ. /sleep 23:40 07:10")
        return
    try:
        res = await ctx.service.log_sleep(context.args[0], context.args[1], ctx.service.now())
    except ValueError:
        await update.message.reply_text("Λάθος ώρα. Μορφή HH:MM.")
        return
    e = res["entry"]
    msg = f"😴 {e['hours']}h ({e['bed']}→{e['wake']})."
    if res["escalate"]:
        msg += "\n" + ctx.coach.render("sleep_escalation", nights=res["short_nights"])
    elif not e["below_target"] and res["award"]:
        msg += f" +{res['award']['xp_delta']} XP"
    await update.message.reply_text(msg)


@guard
async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    try:
        item, grams = context.args[0], float(context.args[1].replace(",", "."))
    except (IndexError, ValueError):
        await update.message.reply_text("Χρήση: /stock <είδος> <γραμμάρια>")
        return
    res = await ctx.service.set_stock(item, grams, ctx.service.now())
    pred = res["prediction"]
    tail = ""
    if pred["runout_date"]:
        tail = f"\nΤελειώνει ~{pred['runout_date']} (burn {pred['burn_per_day']}g/μέρα)."
    await update.message.reply_text(f"📦 {item}: {grams}g.{tail}")


@guard
async def cmd_spent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    try:
        amount = float(context.args[0].replace(",", "."))
        category = context.args[1] if len(context.args) > 1 else "chicken"
    except (IndexError, ValueError):
        await update.message.reply_text("Χρήση: /spent <€> <κατηγορία>")
        return
    res = await ctx.service.spend(amount, category, ctx.service.now())
    budget = f" / {res['budget']}€ budget" if res["budget"] else ""
    await update.message.reply_text(
        f"💶 {category}: σύνολο μήνα {res['month_total']}€{budget}.")


@guard
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    sl = await ctx.service.shopping_list(ctx.service.now())
    if not sl["items"]:
        await update.message.reply_text("🛒 Τίποτα επείγον. Απόθεμα οκ.")
        return
    lines = [f"• {i['item']}: {int(i['need_g'])}g (έχεις {int(i['stock_g'])}g)"
             for i in sl["items"]]
    run = sl["next_run"]
    when = f"\nΕπόμενο run: {run[0]} ({run[1]})" if run else ""
    await update.message.reply_text(f"🛒 Λίστα ({sl['store']}):\n" + "\n".join(lines) + when)


@guard
async def cmd_weed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    await ctx.db.kv_set(f"weed:{ctx.service.now().date().isoformat()}", "1")
    await update.message.reply_text(
        ctx.coach.render("munchies"),
        reply_markup=_markup([[("🥛 Γιαούρτι", "munch:yogurt"), ("🍎 Φρούτο", "munch:fruit"),
                               ("🚫 Τίποτα", "munch:none")]]))


@guard
async def cmd_meals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    all_meals = await meals.list_meals(ctx.db)
    lines, buttons = [], []
    for m in all_meals:
        tag = " 🔒" if m.locked else ""
        lines.append(f"• `{m.id}` {m.name} — {int(m.kcal)}kcal / {int(m.protein_g)}p{tag}")
        if not m.locked:
            buttons.append([(f"🍽 {m.name}", f"eat:{m.id}")])
    await update.message.reply_text(
        "🍴 *Γεύματα* (πάτα για καταγραφή):\n" + "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN, reply_markup=_markup(buttons[:20]))


@guard
async def cmd_fact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    fact = await facts.pick_fact(ctx.db, ctx.service.now())
    if fact is None:
        await update.message.reply_text("Δεν βρέθηκε trivia.")
        return
    await update.message.reply_text(
        f"🧠 *{fact.title}*\n{fact.body}\n\nCoach verdict: "
        f"{facts.verdict_stars(fact.verdict)} ({fact.verdict}/5)",
        parse_mode=ParseMode.MARKDOWN)


@guard
async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    d = await ctx.service.weekly_report()
    intro = ctx.coach.render("weekly_report_intro", respect=await ctx.respect())
    text = (
        f"{intro}\n"
        f"• Adherence: {d['adherence_pct']}% ({d['tasks_done']}✓/{d['tasks_failed']}✗)\n"
        f"• Gym: {d['gym_sessions']}/{d['gym_target']}\n"
        f"• Μ.Ο. πρωτεΐνης: {d['avg_protein']}g\n"
        f"• Αγνοημένες προειδοποιήσεις: {d['ignored_warnings']}\n"
        f"• XP {d['xp']} ({d['xp_delta']:+d}) — Lvl {d['level']} {d['level_name']}\n"
        f"• Respect {d['respect']} ({d['respect_delta']:+d})")
    await update.message.reply_text(text)


@guard
async def cmd_charts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    series = await ctx.service.weight_series(60)
    p = await ctx.service.profile()
    out = ROOT / "charts" / "weight_trend.png"
    render_weight_trend(series, out, target=p.get("start_weight_kg", 0) - 10)
    with open(out, "rb") as fh:
        await update.message.reply_photo(fh, caption="📈 Τάση βάρους (60 ημέρες)")


# --------------------------------------------------------------------------- callbacks
@guard
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _ctx(context)
    query = update.callback_query
    await query.answer()
    action, _, arg = query.data.partition(":")
    when = ctx.service.now()

    if action == "done":
        changed = await nag.confirm(ctx.db, arg, when)
        xp_line = "✅ Καταγράφηκε."
        if changed and TASK_CATEGORY.get(arg) == "meals":
            award = await game.award(ctx.db, "meal", when)
            xp_line = ctx.coach.render("praise", xp=award.xp_delta, respect=award.respect)
        await query.edit_message_text(xp_line)
    elif action == "skip":
        await nag.confirm(ctx.db, arg, when)
        await query.edit_message_text("⏭ Skipped.")
    elif action == "snooze":
        await nag.snooze(ctx.db, arg, when, 30)
        await query.edit_message_text(ctx.coach.render("snoozed"))
    elif action == "eat":
        try:
            res = await ctx.service.ate(arg, when)
            await _confirm_open_meal_task(ctx)
            extra = (f"\n💪 Protein floor! +{res['floor_award']['xp_delta']} XP"
                     if res["floor_award"] else "")
            await query.edit_message_text(
                f"✅ {res['logged']['name']}: {res['logged']['kcal']}kcal, "
                f"{res['logged']['protein_g']}p (+{res['award']['xp_delta']} XP)" + extra)
        except (meals.MealLocked, meals.MealCapReached, meals.MealNotFound) as e:
            await query.edit_message_text(str(e))
    elif action == "cat":
        await _send_food_list(ctx, arg, query)
    elif action == "foodcats":
        await _send_food_categories(ctx, query=query)
    elif action == "combos":
        await _send_meal_picker(ctx, query)
    elif action == "recent":
        await _send_recent_foods(ctx, query)
    elif action == "fpick":
        await _send_food_grams(ctx, arg, query)
    elif action == "eatg":
        food_id, _, grams = arg.rpartition(":")
        try:
            await _log_food(ctx, food_id, float(grams), query.edit_message_text)
            await _confirm_open_meal_task(ctx)
        except (meals.MealNotFound, ValueError):
            await query.edit_message_text("Σφάλμα καταγραφής.")
    elif action == "fgcustom":
        context.user_data["await"] = "food_grams"
        context.user_data["food_pending"] = arg
        food = await foods.get_food(ctx.db, arg)
        await query.edit_message_text(f"✏️ Πόσα γραμμάρια «{food['name'] if food else arg}»; (στείλε αριθμό)")
    elif action == "steps":
        res = await ctx.service.log_steps(int(arg), when)
        await nag.confirm(ctx.db, "steps", when)
        if res["hit"]:
            xp = res["award"]["xp_delta"] if res["award"] else 0
            await query.edit_message_text(ctx.coach.render("steps_ok", xp=xp))
        else:
            gap = res["target"] - res["steps"]
            await query.edit_message_text(f"👟 {res['steps']}/{res['target']}. Λείπουν {gap}. Κουνήσου.")
    elif action == "gymlog":
        res = await ctx.service.log_gym(int(arg), when)
        a = res["award"]
        up = ("\n" + ctx.coach.render("level_up", level_name=a["level_name"],
                                      unlock=", ".join(a["unlocks"]) or "—")) if a["level_up"] else ""
        await query.edit_message_text(
            f"🏋️ Session {res['sessions']}/{res['target']} ({arg}′). +{a['xp_delta']} XP{up}")
    elif action == "menu":
        if arg == "food":
            await _send_food_categories(ctx, query=query)
        elif arg == "steps_other":
            context.user_data["await"] = "steps"
            await query.edit_message_text("👟 Στείλε μου τον αριθμό βημάτων (π.χ. 10500).")
        elif arg == "weight":
            context.user_data["await"] = "weight"
            await query.edit_message_text("⚖️ Στείλε μου το βάρος σε kg (π.χ. 95.4).")
        elif arg == "sleep":
            context.user_data["await"] = "sleep"
            await query.edit_message_text("😴 Στείλε: ώρα ύπνου + ξύπνημα (π.χ. 23:40 07:10).")
    elif action == "applycut":
        if arg == "no":
            await query.edit_message_text("OK, κρατάμε το τρέχον deficit.")
        else:
            targets = await ctx.service.apply_cut(int(arg), when)
            await query.edit_message_text(
                f"✂️ Εφαρμόστηκε. Νέος στόχος: {targets.calorie_target} kcal.")
    elif action == "munch":
        await query.edit_message_text(ctx.coach.render("munchies_ack"))


TASK_CATEGORY = {"meal1": "meals", "meal2": "meals"}


# --------------------------------------------------------------------------- /newmeal flow
@guard
async def nm_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["newmeal"] = {}
    await update.message.reply_text("🆕 Νέο γεύμα. Όνομα;  (/cancel για ακύρωση)")
    return NEWMEAL_NAME


async def nm_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["newmeal"]["name"] = update.message.text.strip()
    await update.message.reply_text("Θερμίδες (kcal) ανά μερίδα;")
    return NEWMEAL_KCAL


async def nm_kcal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        context.user_data["newmeal"]["kcal"] = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Δώσε αριθμό kcal.")
        return NEWMEAL_KCAL
    await update.message.reply_text("Πρωτεΐνη (g) ανά μερίδα;")
    return NEWMEAL_PROTEIN


async def nm_protein(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ctx = _ctx(context)
    try:
        protein = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Δώσε αριθμό g.")
        return NEWMEAL_PROTEIN
    d = context.user_data["newmeal"]
    meal_id = facts._slugify(d["name"])  # reuse slug helper
    await meals.add_meal(ctx.db, meal_id, d["name"], "", d["kcal"], protein)
    await update.message.reply_text(f"✅ Προστέθηκε `{meal_id}`: {int(d['kcal'])}kcal, "
                                    f"{int(protein)}p.", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


# --------------------------------------------------------------------------- /newfact flow
@guard
async def nf_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["newfact"] = {}
    await update.message.reply_text("🆕 Νέο trivia. Τίτλος;  (/cancel)")
    return NEWFACT_TITLE


async def nf_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["newfact"]["title"] = update.message.text.strip()
    await update.message.reply_text("Κείμενο (2-3 προτάσεις);")
    return NEWFACT_BODY


async def nf_body(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["newfact"]["body"] = update.message.text.strip()
    await update.message.reply_text("Verdict 1-5 (1=hype, 5=στέρεη επιστήμη);")
    return NEWFACT_VERDICT


async def nf_verdict(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ctx = _ctx(context)
    try:
        verdict = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Δώσε 1-5.")
        return NEWFACT_VERDICT
    d = context.user_data["newfact"]
    fact = await facts.add_fact(ctx.db, d["title"], d["body"], verdict)
    await update.message.reply_text(
        f"✅ Προστέθηκε: *{fact.title}* {facts.verdict_stars(fact.verdict)}",
        parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Ακυρώθηκε.")
    return ConversationHandler.END


# --------------------------------------------------------------------------- menu (tap UX)
BTN_STATUS = "📋 Κατάσταση"
BTN_FOOD = "🍽️ Φαγητό"
BTN_STEPS = "👟 Βήματα"
BTN_GYM = "🏋️ Γυμναστήριο"
BTN_WEIGHT = "⚖️ Ζύγισμα"
BTN_SLEEP = "😴 Ύπνος"
BTN_FACT = "🧠 Trivia"
BTN_REPORT = "📊 Report"
BTN_DASH = "🎒 Dashboard"
BTN_HELP = "❓ Βοήθεια"

MAIN_MENU = ReplyKeyboardMarkup(
    [[BTN_STATUS, BTN_FOOD], [BTN_STEPS, BTN_GYM], [BTN_WEIGHT, BTN_SLEEP],
     [BTN_FACT, BTN_REPORT], [BTN_DASH, BTN_HELP]],
    resize_keyboard=True, is_persistent=True,
)
MENU_LABELS = {BTN_STATUS, BTN_FOOD, BTN_STEPS, BTN_GYM, BTN_WEIGHT, BTN_SLEEP,
               BTN_FACT, BTN_REPORT, BTN_DASH, BTN_HELP}

STEPS_PRESETS = [[("8.000", "steps:8000"), ("10.000", "steps:10000")],
                 [("11.000", "steps:11000"), ("12.000", "steps:12000")],
                 [("✏️ Άλλο", "menu:steps_other")]]
GYM_PRESETS = [[("45′", "gymlog:45"), ("60′", "gymlog:60"), ("90′", "gymlog:90")]]


FOOD_CATEGORIES = [
    ("protein", "🥩 Πρωτεΐνη"), ("carb", "🍚 Υδατ/κες"), ("dairy", "🧀 Γαλακτ."),
    ("veg", "🥗 Λαχανικά"), ("fruit", "🍎 Φρούτα"), ("fat", "🥑 Λίπη"),
    ("legume", "🫘 Όσπρια"), ("sauce", "🥫 Σάλτσες"), ("supplement", "💊 Συμπλ."),
    ("snack", "🍫 Σνακ"), ("sweetener", "🍯 Γλυκ."), ("treat", "🍬 Treats"),
]


async def _send_food_categories(ctx: AppContext, *, query=None, message=None) -> None:
    """First level of the granular food picker: category buttons + a Combos shortcut."""
    present = {f["category"] for f in await foods.list_foods(ctx.db)}
    rows, row = [], []
    for cid, label in FOOD_CATEGORIES:
        if cid not in present:
            continue
        row.append((label, f"cat:{cid}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    tail = [("🍱 Combos", "combos")]
    if await foods.recent_foods(ctx.db, 1):
        tail.insert(0, ("🕐 Πρόσφατα", "recent"))
    rows.append(tail)
    text = "🍽️ Διάλεξε κατηγορία (ή combo):"
    if query is not None:
        await query.edit_message_text(text, reply_markup=_markup(rows))
    elif message is not None:
        await message.reply_text(text, reply_markup=_markup(rows))


async def _send_food_list(ctx: AppContext, category: str, query) -> None:
    rows, row = [], []
    for f in await foods.list_recent_first(ctx.db, category=category):  # recent-eaten first
        row.append((f"{f['name']} · {int(f['default_g'])}g", f"fpick:{f['id']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([("⬅️ Κατηγορίες", "foodcats")])
    await query.edit_message_text(f"Διάλεξε τρόφιμο — {category}:", reply_markup=_markup(rows))


async def _send_recent_foods(ctx: AppContext, query) -> None:
    items = await foods.recent_foods(ctx.db, 10)
    rows = [[(f"{f['name']} · {int(f['default_g'])}g", f"fpick:{f['id']}")] for f in items]
    rows.append([("⬅️ Κατηγορίες", "foodcats")])
    await query.edit_message_text("🕐 Πρόσφατα (τα τελευταία 10):", reply_markup=_markup(rows))


async def _send_food_grams(ctx: AppContext, food_id: str, query) -> None:
    food = await foods.get_food(ctx.db, food_id)
    if food is None:
        await query.edit_message_text("Δεν βρέθηκε το τρόφιμο.")
        return
    d = food["default_g"]
    g = lambda x: int(round(x))  # noqa: E731
    rows = [
        [(f"✅ {g(d)}g", f"eatg:{food_id}:{g(d)}"), (f"½ · {g(d*0.5)}g", f"eatg:{food_id}:{g(d*0.5)}")],
        [(f"×1.5 · {g(d*1.5)}g", f"eatg:{food_id}:{g(d*1.5)}"),
         (f"×2 · {g(d*2)}g", f"eatg:{food_id}:{g(d*2)}")],
        [("✏️ Άλλα γραμμάρια", f"fgcustom:{food_id}")],
    ]
    await query.edit_message_text(f"🍽️ {food['name']} — πόσα γραμμάρια;", reply_markup=_markup(rows))


async def _log_food(ctx: AppContext, food_id: str, grams: float, reply) -> None:
    res = await ctx.service.eat_food(food_id, ctx.service.now(), grams)
    lg, today = res["logged"], res["today"]
    extra = f"\n💪 Protein floor! +{res['floor_award']['xp_delta']} XP" if res["floor_award"] else ""
    await reply(
        f"✅ {lg['name']} {int(round(lg['grams']))}g: {lg['kcal']} kcal, {lg['protein_g']}g "
        f"(+{res['award']['xp_delta']} XP)\nΣύνολο: {today['kcal']} kcal, "
        f"{today['protein_g']}/{today['protein_floor_g']}g πρωτεΐνη{extra}")


async def _send_meal_picker(ctx: AppContext, query=None, *, message=None) -> None:
    """Tap-to-log buttons for enabled, unlocked meals/combos (no command needed)."""
    rows, row = [], []
    for m in await meals.list_meals(ctx.db):
        if m.locked:
            continue
        row.append((m.name, f"eat:{m.id}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    text = "🍽️ Τι έφαγες; (πάτα για καταγραφή)"
    if query is not None:
        await query.edit_message_text(text, reply_markup=_markup(rows))
    elif message is not None:
        await message.reply_text(text, reply_markup=_markup(rows))


@guard
async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Μενού 👇", reply_markup=MAIN_MENU)


@guard
async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a tap on the persistent reply keyboard."""
    ctx = _ctx(context)
    text = update.message.text
    if text == BTN_STATUS:
        await cmd_status(update, context)
    elif text == BTN_FOOD:
        await _send_food_categories(ctx, message=update.message)
    elif text == BTN_STEPS:
        await update.message.reply_text("👟 Πόσα βήματα;", reply_markup=_markup(STEPS_PRESETS))
    elif text == BTN_GYM:
        await update.message.reply_text("🏋️ Προπόνηση — διάρκεια;", reply_markup=_markup(GYM_PRESETS))
    elif text == BTN_WEIGHT:
        context.user_data["await"] = "weight"
        await update.message.reply_text("⚖️ Στείλε μου το βάρος σε kg (π.χ. 95.4).")
    elif text == BTN_SLEEP:
        context.user_data["await"] = "sleep"
        await update.message.reply_text("😴 Στείλε: ώρα ύπνου + ξύπνημα (π.χ. 23:40 07:10).")
    elif text == BTN_FACT:
        await cmd_fact(update, context)
    elif text == BTN_REPORT:
        await cmd_report(update, context)
    elif text == BTN_DASH:
        url = ctx.config.dashboard_url or f"http://{ctx.config.api_host}:{ctx.config.api_port}/"
        await update.message.reply_text(f"🎒 Dashboard (σφαιρική RPG εικόνα):\n{url}")
    elif text == BTN_HELP:
        await cmd_help(update, context)


@guard
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch a plain value typed after a prompt (weight/steps/sleep) — no command needed."""
    ctx = _ctx(context)
    awaiting = context.user_data.pop("await", None)
    text = (update.message.text or "").strip()
    when = ctx.service.now()

    if awaiting == "food_grams":
        food_id = context.user_data.pop("food_pending", None)
        try:
            grams = float(text.replace(",", "."))
        except ValueError:
            context.user_data["await"] = "food_grams"
            context.user_data["food_pending"] = food_id
            await update.message.reply_text("Δώσε αριθμό γραμμαρίων, π.χ. 180")
            return
        if food_id:
            await _log_food(ctx, food_id, grams, update.message.reply_text)
        return
    if awaiting == "weight":
        try:
            kg = float(text.replace(",", "."))
        except ValueError:
            context.user_data["await"] = "weight"
            await update.message.reply_text("Δώσε αριθμό, π.χ. 95.4")
            return
        res = await ctx.service.log_weight(kg, when)
        t = res["targets"]
        await update.message.reply_text(
            ctx.coach.render("weight_logged", kg=kg, target_kcal=t["calorie_target"],
                             floor=t["protein_floor_g"], respect=await ctx.respect()))
        if res["stalled"] and res["proposal"]:
            pr = res["proposal"]
            await update.message.reply_text(
                ctx.coach.render("stall_proposal", item=pr["item"], kcal=pr["kcal"]),
                reply_markup=_markup([[("✅ Εφάρμοσε", f"applycut:{pr['new_deficit']}"),
                                       ("❌ Όχι", "applycut:no")]]))
    elif awaiting == "steps":
        try:
            n = int(text.replace(".", "").replace(",", "").replace(" ", ""))
        except ValueError:
            context.user_data["await"] = "steps"
            await update.message.reply_text("Δώσε αριθμό βημάτων, π.χ. 10500")
            return
        res = await ctx.service.log_steps(n, when)
        await nag.confirm(ctx.db, "steps", when)
        if res["hit"]:
            xp = res["award"]["xp_delta"] if res["award"] else 0
            await update.message.reply_text(ctx.coach.render("steps_ok", xp=xp))
        else:
            await update.message.reply_text(f"👟 {n}/{res['target']}. Λείπουν {res['target'] - n}.")
    elif awaiting == "sleep":
        parts = text.replace("-", " ").split()
        if len(parts) < 2:
            context.user_data["await"] = "sleep"
            await update.message.reply_text("Μορφή: 23:40 07:10")
            return
        try:
            res = await ctx.service.log_sleep(parts[0], parts[1], when)
        except ValueError:
            context.user_data["await"] = "sleep"
            await update.message.reply_text("Λάθος ώρα. Μορφή HH:MM HH:MM.")
            return
        e = res["entry"]
        msg = f"😴 {e['hours']}h ({e['bed']}→{e['wake']})."
        if res["escalate"]:
            msg += "\n" + ctx.coach.render("sleep_escalation", nights=res["short_nights"])
        await update.message.reply_text(msg)
    else:
        await update.message.reply_text("👇 Διάλεξε από το μενού.", reply_markup=MAIN_MENU)


# --------------------------------------------------------------------------- wiring
def build_application(ctx: AppContext) -> Application:
    app = (
        Application.builder()
        .token(ctx.config.token)
        .post_init(ctx.on_startup)
        .post_shutdown(ctx.on_shutdown)
        .build()
    )
    app.bot_data["ctx"] = ctx

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("newmeal", nm_start)],
        states={
            NEWMEAL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, nm_name)],
            NEWMEAL_KCAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, nm_kcal)],
            NEWMEAL_PROTEIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, nm_protein)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("newfact", nf_start)],
        states={
            NEWFACT_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, nf_title)],
            NEWFACT_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, nf_body)],
            NEWFACT_VERDICT: [MessageHandler(filters.TEXT & ~filters.COMMAND, nf_verdict)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    for name, handler in [
        ("start", cmd_start), ("status", cmd_status), ("help", cmd_help), ("menu", cmd_menu),
        ("ate", cmd_ate), ("skip", cmd_skip), ("weight", cmd_weight), ("steps", cmd_steps),
        ("gym", cmd_gym), ("sleep", cmd_sleep), ("stock", cmd_stock), ("spent", cmd_spent),
        ("list", cmd_list), ("w", cmd_weed), ("meals", cmd_meals), ("fact", cmd_fact),
        ("report", cmd_report), ("charts", cmd_charts),
    ]:
        app.add_handler(CommandHandler(name, handler))
    # Tap-driven menu: exact-label buttons first, then a catch-all for typed values.
    app.add_handler(MessageHandler(filters.Text(MENU_LABELS), on_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(CallbackQueryHandler(on_callback))
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # httpx logs every request URL at INFO — and Telegram request URLs embed the bot
    # token. Keep it at WARNING so the token never lands in journald/log files.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    config = load_config(require_secrets=True)
    ctx = AppContext(config=config, db=Database(str(config.resolved_db_path())),
                     service=None)  # type: ignore[arg-type]
    ctx.service = Service(ctx.db, config)
    app = build_application(ctx)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
