"""Coach persona layer.

MealSentry is the platform; a *coach* is a swappable personality that delivers the
messages. A coach = a manifest (``coaches/<id>.yaml``) + a template set
(``tone/templates_gr.yaml``). ``Coach.render()`` picks a message for a given situation and
respect-driven tone tier, fills placeholders with the user's own logged data, and rotates
variants to avoid repetition.

Zero Telegram / DB imports — the bot and API call this with plain data.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import paths
from .engine.game import respect_tier

TIERS = ("LOW", "MID", "HIGH")


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:  # noqa: D401
        return "?"


def _safe_format(template: str, data: dict) -> str:
    try:
        return string.Formatter().vformat(template, (), _SafeDict(data))
    except (ValueError, IndexError):
        return template


@dataclass
class Coach:
    id: str
    display_name: str
    description: str
    intensity: int
    templates: dict
    _last_idx: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, coach_id: str = "chad_coach", *, intensity: int | None = None) -> Coach:
        manifest_path = paths.COACHES_DIR / f"{coach_id}.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        rel = manifest.get("templates", "tone/templates_gr.yaml")
        tmpl_path = Path(rel)
        if not tmpl_path.is_absolute():
            tmpl_path = paths.ROOT / rel
        templates = yaml.safe_load(tmpl_path.read_text(encoding="utf-8")) or {}
        return cls(
            id=manifest["id"],
            display_name=manifest["display_name"],
            description=manifest.get("description", ""),
            intensity=int(intensity if intensity is not None else manifest.get("default_intensity", 2)),
            templates=templates,
        )

    # ------------------------------------------------------------------ rendering
    def _effective_tier(self, tier: str) -> str:
        """Bias the tier by the global intensity knob (1 softer .. 3 harsher)."""
        if self.intensity >= 3 and tier == "MID":
            return "LOW"
        if self.intensity >= 3 and tier == "HIGH":
            return "MID"
        if self.intensity <= 1 and tier == "LOW":
            return "MID"
        return tier

    def _choose(self, key: str, variants: list[str]) -> str:
        if not variants:
            return ""
        if len(variants) == 1:
            return variants[0]
        last = self._last_idx.get(key, -1)
        idx = random.randrange(len(variants))
        if idx == last:  # avoid immediate repetition
            idx = (idx + 1) % len(variants)
        self._last_idx[key] = idx
        return variants[idx]

    def render(
        self, situation: str, *, tier: str | None = None,
        respect: int | None = None, **data,
    ) -> str:
        entry = self.templates.get(situation)
        if entry is None:
            return _safe_format("{coach}: {situation}", {"coach": self.display_name,
                                                          "situation": situation})
        if isinstance(entry, dict):
            if tier is None:
                tier = respect_tier(respect if respect is not None else 50)
            tier = self._effective_tier(tier)
            variants = entry.get(tier) or entry.get("MID") or next(iter(entry.values()))
            choose_key = f"{situation}:{tier}"
        else:
            variants = entry
            choose_key = situation
        text = self._choose(choose_key, list(variants))
        data.setdefault("coach", self.display_name)
        return _safe_format(text, data)

    def has(self, situation: str) -> bool:
        return situation in self.templates
