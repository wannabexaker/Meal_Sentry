"""RPG classes (playful, body-based). The user picks an aspirational class; the system
adds a fun epithet based on how their height/weight compares to that class's ideal build.
It never body-shames with slurs — the tone is self-chosen gaming banter ("Ψηλός Χοντρός
Assassin"). Complements the level number; does not replace it.
"""

from __future__ import annotations

# ideal height (cm) and weight (kg) ranges per class
CLASSES: dict[str, dict] = {
    "assassin": {"name": "Assassin", "emoji": "🗡️", "h": (160, 175), "w": (58, 72),
                 "desc": "λεπτός & γρήγορος"},
    "ranger":   {"name": "Ranger",   "emoji": "🏹", "h": (178, 195), "w": (68, 85),
                 "desc": "λεπτός & ψηλός"},
    "monk":     {"name": "Monk",     "emoji": "🥋", "h": (165, 182), "w": (60, 78),
                 "desc": "ελαφρύς & ευλύγιστος"},
    "brawler":  {"name": "Brawler",  "emoji": "🥊", "h": (165, 180), "w": (78, 105),
                 "desc": "κοντός & στιβαρός"},
    "warrior":  {"name": "Warrior",  "emoji": "⚔️", "h": (178, 200), "w": (85, 130),
                 "desc": "μεγαλόσωμος & δυνατός"},
    "tank":     {"name": "Tank",     "emoji": "🛡️", "h": (185, 205), "w": (105, 160),
                 "desc": "βαρύς & ψηλός"},
}
H_TOL, W_TOL = 5, 3   # cm / kg beyond the ideal before an epithet kicks in
DEFAULT_CLASS = "warrior"


def epithet(height_cm: float, weight_kg: float, class_id: str) -> list[str]:
    """Fun adjectives for how far the body is from the class ideal (empty = good fit)."""
    c = CLASSES.get(class_id)
    if not c:
        return []
    adj = []
    if height_cm > c["h"][1] + H_TOL:
        adj.append("Ψηλός")
    elif height_cm < c["h"][0] - H_TOL:
        adj.append("Κοντός")
    if weight_kg > c["w"][1] + W_TOL:
        adj.append("Χοντρός")
    elif weight_kg < c["w"][0] - W_TOL:
        adj.append("Αδύνατος")
    return adj


def describe(height_cm: float, weight_kg: float, class_id: str) -> dict:
    c = CLASSES.get(class_id) or CLASSES[DEFAULT_CLASS]
    cid = class_id if class_id in CLASSES else DEFAULT_CLASS
    adj = epithet(height_cm, weight_kg, cid)
    title = " ".join([*adj, c["name"]]) if adj else f"Επίδοξος {c['name']}"
    return {
        "class_id": cid, "class_name": c["name"], "emoji": c["emoji"],
        "epithet": adj, "title": title, "desc": c["desc"],
        "ideal_h": list(c["h"]), "ideal_w": list(c["w"]), "fit": not adj,
    }


def best_fit(height_cm: float, weight_kg: float) -> str:
    """Suggest the class the body is closest to (fewest epithets, then smallest deviation)."""
    def score(cid: str) -> tuple[int, float]:
        c = CLASSES[cid]
        hmid, wmid = sum(c["h"]) / 2, sum(c["w"]) / 2
        dev = abs(height_cm - hmid) / 10 + abs(weight_kg - wmid) / 5
        return (len(epithet(height_cm, weight_kg, cid)), dev)
    return min(CLASSES, key=score)


def list_classes() -> list[dict]:
    return [{"id": cid, "name": c["name"], "emoji": c["emoji"], "desc": c["desc"],
             "ideal_h": list(c["h"]), "ideal_w": list(c["w"])} for cid, c in CLASSES.items()]
