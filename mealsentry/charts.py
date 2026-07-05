"""Chart rendering (matplotlib, headless). Kept out of the engine layer so the engine
has no heavy plotting dependency; only the bot/API import this.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: no display needed on the Pi
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402


def render_weight_trend(
    points: list[dict], out_path: str | Path, *, target: float | None = None
) -> Path:
    """Render a weight-trend PNG from [{'date','kg'}, ...]. Returns the output path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(7, 4), dpi=110)
    if points:
        dates = [datetime.fromisoformat(p["date"]) for p in points]
        kgs = [p["kg"] for p in points]
        ax.plot(dates, kgs, marker="o", linewidth=2, color="#e6522c", label="Βάρος (kg)")
        if len(kgs) >= 2:
            ax.annotate(f"{kgs[-1]:.1f}", (dates[-1], kgs[-1]),
                        textcoords="offset points", xytext=(0, 8), fontsize=9)
        if target:
            ax.axhline(target, color="#2c7be6", linestyle="--", linewidth=1, label=f"Στόχος {target}")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        fig.autofmt_xdate(rotation=30)
    else:
        ax.text(0.5, 0.5, "Δεν υπάρχουν δεδομένα βάρους ακόμα",
                ha="center", va="center", transform=ax.transAxes)

    ax.set_title("MealSentry — Τάση βάρους")
    ax.set_ylabel("kg")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    return out_path
