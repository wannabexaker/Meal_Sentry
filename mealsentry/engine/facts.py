"""Fun Facts module (spec §13): daily 'Coach Trivia', /fact on-demand, /newfact.

Facts never repeat within 60 days. Selection prefers never-shown facts, then the oldest
seen, so the full pool cycles before anything repeats.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..db import Database
from ..util import date_str

NO_REPEAT_DAYS = 60


@dataclass
class Fact:
    id: str
    title: str
    body: str
    verdict: int
    tags: str
    custom: bool


def verdict_stars(verdict: int) -> str:
    verdict = max(1, min(5, verdict))
    return "★" * verdict + "☆" * (5 - verdict)


def _row_to_fact(row) -> Fact:
    return Fact(row["id"], row["title"], row["body"], row["verdict"],
                row["tags"], bool(row["custom"]))


async def pick_fact(db: Database, when: datetime, *, mark_seen: bool = True) -> Fact | None:
    """Pick an eligible fact (not shown in the last 60 days) and optionally mark it seen."""
    cutoff = (datetime.fromisoformat(date_str(when)).date()
              - timedelta(days=NO_REPEAT_DAYS)).isoformat()
    row = await db.fetchone(
        """
        SELECT f.* FROM facts f
        LEFT JOIN (SELECT fact_id, MAX(shown_date) AS last FROM facts_seen GROUP BY fact_id) s
               ON f.id = s.fact_id
        WHERE s.last IS NULL OR s.last < ?
        ORDER BY (s.last IS NOT NULL), s.last, RANDOM()
        LIMIT 1
        """,
        (cutoff,),
    )
    if row is None:
        # Everything shown within the window (small pool + heavy use): reuse the oldest.
        row = await db.fetchone(
            """
            SELECT f.* FROM facts f
            LEFT JOIN (SELECT fact_id, MAX(shown_date) AS last FROM facts_seen GROUP BY fact_id) s
                   ON f.id = s.fact_id
            ORDER BY s.last IS NOT NULL, s.last, RANDOM() LIMIT 1
            """
        )
    if row is None:
        return None
    fact = _row_to_fact(row)
    if mark_seen:
        await db.execute(
            "INSERT OR IGNORE INTO facts_seen(fact_id, shown_date) VALUES (?, ?)",
            (fact.id, date_str(when)),
        )
    return fact


async def get_fact(db: Database, fact_id: str) -> Fact | None:
    row = await db.fetchone("SELECT * FROM facts WHERE id = ?", (fact_id,))
    return _row_to_fact(row) if row else None


def _slugify(title: str) -> str:
    nfd = unicodedata.normalize("NFD", title.lower())
    ascii_ish = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_ish).strip("_")
    return slug[:40] or "fact"


async def add_fact(
    db: Database, title: str, body: str, verdict: int, *, tags: str = "custom"
) -> Fact:
    """Add a user fact (/newfact). Generates a unique id from the title."""
    verdict = max(1, min(5, int(verdict)))
    base = f"user_{_slugify(title)}"
    fact_id = base
    n = 1
    while await db.fetchval("SELECT 1 FROM facts WHERE id = ?", (fact_id,)):
        n += 1
        fact_id = f"{base}_{n}"
    await db.execute(
        "INSERT INTO facts(id, title, body, verdict, tags, source, custom) "
        "VALUES (?, ?, ?, ?, ?, 'user', 1)",
        (fact_id, title.strip(), body.strip(), verdict, tags),
    )
    return Fact(fact_id, title.strip(), body.strip(), verdict, tags, True)


async def counts(db: Database) -> dict[str, int]:
    total = await db.fetchval("SELECT COUNT(*) FROM facts", default=0)
    custom = await db.fetchval("SELECT COUNT(*) FROM facts WHERE custom = 1", default=0)
    return {"total": total, "custom": custom, "seed": total - custom}
