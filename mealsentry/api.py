"""FastAPI backend (spec §10): a thin, read-only REST layer over the same SQLite the bot
uses — the future Android app's backend. Runs as its own process/systemd unit on port
8787; it never starts the scheduler (the bot owns writes/nags).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import paths
from .config import load_config
from .db import Database, init_db
from .engine import facts, foods, meals
from .service import Service
from .token import verify_token

log = logging.getLogger("mealsentry.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config(require_secrets=False)
    db = Database(str(config.resolved_db_path()))
    await db.connect()
    await init_db(db, config)          # idempotent; ensures schema exists
    app.state.db = db
    app.state.service = Service(db, config)
    app.state.config = config
    log.info("MealSentry API up on %s:%s", config.api_host, config.api_port)
    try:
        yield
    finally:
        await db.close()


app = FastAPI(title="MealSentry API", version="0.1.0", lifespan=lifespan)


def _svc(app: FastAPI) -> Service:
    return app.state.service


@app.get("/", include_in_schema=False)
async def dashboard_page() -> FileResponse:
    """Serve the MMORPG-style inventory dashboard."""
    return FileResponse(paths.WEB_DIR / "dashboard.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(paths.WEB_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/dashboard/data")
async def dashboard_data() -> dict:
    """Aggregated payload the dashboard polls: character, quests, inventory, loot, stats."""
    return await _svc(app).dashboard()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": "mealsentry"}


@app.get("/status")
async def status() -> dict:
    return await _svc(app).status()


@app.get("/meals")
async def list_meals(include_disabled: bool = False) -> list[dict]:
    rows = await meals.list_meals(app.state.db, include_disabled=include_disabled)
    return [
        {"id": m.id, "name": m.name, "contents": m.contents, "kcal": m.kcal,
         "protein_g": m.protein_g, "locked": m.locked, "enabled": m.enabled,
         "max_per_week": m.max_per_week, "tags": m.tags}
        for m in rows
    ]


@app.get("/logs")
async def logs(limit: int = 20) -> dict:
    return await _svc(app).recent_logs(limit=limit)


@app.get("/report")
async def report() -> dict:
    return await _svc(app).weekly_report()


@app.get("/weight")
async def weight(days: int = 60) -> list[dict]:
    return await _svc(app).weight_series(days=days)


@app.get("/shopping")
async def shopping() -> dict:
    return await _svc(app).shopping_list(_svc(app).now())


@app.get("/facts/random")
async def random_fact() -> dict:
    fact = await facts.pick_fact(app.state.db, _svc(app).now(), mark_seen=False)
    if fact is None:
        raise HTTPException(status_code=404, detail="no facts")
    return {"id": fact.id, "title": fact.title, "body": fact.body,
            "verdict": fact.verdict, "stars": facts.verdict_stars(fact.verdict)}


# --------------------------------------------------------------------------- control page
async def require_token(t: str = Query(..., description="control-page token")) -> None:
    secret = await app.state.service.dashboard_secret()
    if not verify_token(secret, t):
        raise HTTPException(status_code=403, detail="Ο σύνδεσμος έληξε. Στείλε /dashboard στο bot.")


class ProfileIn(BaseModel):
    name: str | None = None
    age: int | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    steps_target: int | None = None
    gym_target_sessions: int | None = None
    sleep_target_hours: float | None = None
    protein_factor: float | None = None
    deficit_kcal: int | None = None


class FoodIn(BaseModel):
    id: str
    name: str
    category: str = "other"
    kcal: float
    protein: float
    carbs: float = 0
    fat: float = 0
    default_g: float = 100
    aliases: list[str] = []


@app.get("/d/{token}", include_in_schema=False)
async def control_page(token: str) -> FileResponse:
    secret = await app.state.service.dashboard_secret()
    if not verify_token(secret, token):
        raise HTTPException(status_code=403, detail="Ο σύνδεσμος έληξε. Στείλε /dashboard στο bot.")
    return FileResponse(paths.WEB_DIR / "settings.html")


@app.get("/api/profile")
async def api_get_profile() -> dict:
    return await _svc(app).profile()


@app.get("/api/foods")
async def api_foods() -> list[dict]:
    return await foods.list_foods(app.state.db)


@app.post("/api/profile", dependencies=[Depends(require_token)])
async def api_profile(body: ProfileIn) -> dict:
    return await _svc(app).update_profile(**body.model_dump(exclude_none=True))


@app.post("/api/foods", dependencies=[Depends(require_token)])
async def api_add_food(body: FoodIn) -> dict:
    return await foods.add_food(
        app.state.db, body.id, body.name, body.kcal, body.protein, body.carbs, body.fat,
        category=body.category, default_g=body.default_g, aliases=body.aliases)


@app.post("/api/foods/{food_id}/default_g", dependencies=[Depends(require_token)])
async def api_food_default_g(food_id: str, grams: float = Body(..., embed=True)) -> dict:
    await foods.set_default_g(app.state.db, food_id, grams)
    return {"id": food_id, "default_g": grams}


@app.delete("/api/foods/{food_id}", dependencies=[Depends(require_token)])
async def api_delete_food(food_id: str) -> dict:
    await foods.delete_food(app.state.db, food_id)
    return {"deleted": food_id}


def main() -> None:
    import uvicorn

    config = load_config(require_secrets=False)
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("mealsentry.api:app", host=config.api_host, port=config.api_port, reload=False)


if __name__ == "__main__":
    main()
