import json
import os

from dollarpy import Template, Point, Recognizer

from paths import user_data_path

# Writable copy of spells.json (next to the code when running from source,
# %APPDATA%/WarOfWizards in the .exe, seeded from the bundled default).
SPELLS_FILE = user_data_path("spells.json", seed_from_bundle=True)

DEFAULT_DAMAGE = 100
DEFAULT_COLOR = [0, 200, 255]
DEFAULT_COST = 25   # mana cost to cast, if not specified for a spell
DEFAULT_TYPE = "attack"  # "attack" deals damage; "defense" blocks an incoming enemy attack

# What each attack spell does besides damage. Set "effect" in spells.json (or
# record_spells.py --effect) to override; spells without one use these defaults.
EFFECTS = {
    "none": "Plain damage",
    "burn": "Burns the enemy for 3 s",
    "interrupt": "Instant hit · stops a charging attack · breaks wards",
    "counter": "Slow orb that destroys enemy shots in its path",
}
DEFENSE_ROLE = "Blocks for 1.5 s · cast just before impact to reflect"
EFFECT_DEFAULTS_BY_NAME = {
    "fireball": "burn",
    "lightning bolt": "interrupt",
    "lightning": "interrupt",
    "dihh": "counter",
    "arcane": "counter",
}


def spell_effect(name, entry):
    """The spell's effect: its own "effect" field, else a default by name, else "none"."""
    if entry.get("type", DEFAULT_TYPE) == "defense":
        return "parry"
    eff = entry.get("effect")
    if eff in EFFECTS:
        return eff
    return EFFECT_DEFAULTS_BY_NAME.get(name.strip().lower(), "none")


SHORT_ROLES = {
    "none": "",
    "burn": "Burns over time",
    "interrupt": "Stuns · breaks wards",
    "counter": "Eats enemy shots",
    "parry": "Parry reflects shots",
}


def spell_role_short(name, entry):
    return SHORT_ROLES[spell_effect(name, entry)]


def spell_role(name, entry):
    """Short description of what the spell does, for the spellbook and HUD."""
    eff = spell_effect(name, entry)
    return DEFENSE_ROLE if eff == "parry" else EFFECTS[eff]


def new_spell_entry():
    return {
        "damage": DEFAULT_DAMAGE,
        "color": list(DEFAULT_COLOR),
        "cost": DEFAULT_COST,
        "type": DEFAULT_TYPE,
        "gestures": [],
    }  # optional: "effect" (see EFFECTS)


def load_spell_data():
    """
    Load spells.json and return:
        {"Fireball": {"damage": 150, "color": [255, 60, 60], "gestures": [[{"x":..,"y":..}, ...], ...]}}

    Missing, empty, or broken file -> {}.
    Old format ({"Fireball": [gesture, gesture]}) is upgraded automatically.
    """
    if not os.path.exists(SPELLS_FILE):
        return {}
    try:
        with open(SPELLS_FILE, "r") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print("Couldn't read spells.json:", e)
        return {}
    if not isinstance(raw, dict):
        return {}

    data = {}
    for name, value in raw.items():
        entry = new_spell_entry()
        if isinstance(value, list):            # old format
            entry["gestures"] = value
        elif isinstance(value, dict):
            entry["damage"] = value.get("damage", DEFAULT_DAMAGE)
            entry["color"] = value.get("color", list(DEFAULT_COLOR))
            entry["cost"] = value.get("cost", DEFAULT_COST)
            entry["type"] = value.get("type", DEFAULT_TYPE)
            entry["gestures"] = value.get("gestures", [])
            if value.get("effect") in EFFECTS:
                entry["effect"] = value["effect"]
        data[name] = entry
    return data


def save_spell_data(data):
    """Write to a temp file first so a crash can't corrupt spells.json."""
    tmp = SPELLS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, SPELLS_FILE)


class FastRecognizer(Recognizer):
    """$P recognizer that normalizes templates once (dollarpy redoes it on every
    call, which caused a hitch on release) and also reports the runner-up spell."""

    def __init__(self, templates, n=32):
        super().__init__(templates)
        self.n = n
        self._cache = [(t.name, self._normalize(t, n)) for t in templates]

    @staticmethod
    def _score(d):
        return max((2 - d) / 2, 0.0)

    def recognize_ranked(self, points):
        """Returns (best_name, best_score, runner_up_score); scores are 0..1.
        runner_up_score is the best score of any *other* spell."""
        if len(points) < 2:
            return None, 0.0, 0.0
        pts = self._normalize(points, self.n)
        best = {}
        for name, tpl in self._cache:
            d = self._greedy_cloud_match(pts, tpl, self.n)
            if d < best.get(name, float("inf")):
                best[name] = d
        if not best:
            return None, 0.0, 0.0
        ranked = sorted(best.items(), key=lambda kv: kv[1])
        runner_up = self._score(ranked[1][1]) if len(ranked) > 1 else 0.0
        return ranked[0][0], self._score(ranked[0][1]), runner_up


def build_templates(spell_data):
    templates = []
    for spell_name, entry in spell_data.items():
        for gesture in entry["gestures"]:
            pts = [Point(p["x"], p["y"]) for p in gesture]
            if len(pts) >= 2:
                templates.append(Template(spell_name, pts))
    return templates


def setup_recognizer():
    """
    Returns (recognizer, spell_data).
    recognizer is None if no gestures have been recorded yet.
    """
    spell_data = load_spell_data()
    templates = build_templates(spell_data)
    if not templates:
        print("No spells in spells.json yet. Run record_spells.py first.")
        return None, spell_data
    return FastRecognizer(templates), spell_data