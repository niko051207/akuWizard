import json

from paths import user_data_path

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
SETTINGS_FILE = user_data_path("settings.json")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
PINCH_LEVELS = [("LOW", 0.8), ("NORMAL", 1.0), ("HIGH", 1.25)]
WINDOW_BASE = (800, 600)
WINDOW_SCALES = [0, 1.0, 1.25, 1.5, 1.75, 2.0]

DEFAULTS = {
    "camera_index": 0,
    "camera_name": "",
    "master_volume": 0.8,
    "music_volume": 0.7,
    "sfx_volume": 0.8,
    "fullscreen": False,
    "window_scale": 0,
    "show_fps": False,
    "screen_shake": True,
    "pinch_sensitivity": 1,
}

_VOLUME_KEYS = ("master_volume", "music_volume", "sfx_volume")


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------
def _sanitize(data):
    for k in _VOLUME_KEYS:
        try:
            data[k] = max(0.0, min(1.0, float(data[k])))
        except (TypeError, ValueError):
            data[k] = DEFAULTS[k]
    for k in ("fullscreen", "show_fps", "screen_shake"):
        data[k] = bool(data[k])
    try:
        data["window_scale"] = max(0, min(len(WINDOW_SCALES) - 1, int(data["window_scale"])))
    except (TypeError, ValueError):
        data["window_scale"] = DEFAULTS["window_scale"]
    try:
        data["pinch_sensitivity"] = max(0, min(len(PINCH_LEVELS) - 1, int(data["pinch_sensitivity"])))
    except (TypeError, ValueError):
        data["pinch_sensitivity"] = DEFAULTS["pinch_sensitivity"]
    return data


def load_settings():
    data = dict(DEFAULTS)
    try:
        with open(SETTINGS_FILE) as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            data.update({k: saved[k] for k in DEFAULTS if k in saved})
    except (OSError, ValueError):
        pass
    return _sanitize(data)


def save_settings(data):
    merged = load_settings()
    merged.update(data)
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(_sanitize(merged), f, indent=2)
    except OSError as e:
        print("Couldn't save settings:", e)


def pinch_scale(data):
    return PINCH_LEVELS[data.get("pinch_sensitivity", 1)][1]


# ---------------------------------------------------------------------------
# Window size
# ---------------------------------------------------------------------------
def fitting_scales(desktop):
    dw, dh = desktop
    bw, bh = WINDOW_BASE
    fits = [k for k in WINDOW_SCALES[1:] if bw * k <= dw * 0.95 and bh * k <= dh * 0.88]
    return fits or [1.0]


def window_size(data, desktop):
    fits = fitting_scales(desktop)
    k = WINDOW_SCALES[data.get("window_scale", 0)]
    if k == 0 or k not in fits:
        k = fits[-1]
    return int(WINDOW_BASE[0] * k), int(WINDOW_BASE[1] * k)